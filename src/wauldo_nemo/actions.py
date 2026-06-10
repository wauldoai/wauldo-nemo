"""NeMo Guardrails rails backed by Wauldo verification.

Thin glue: it calls the Wauldo SDK (the single source of verdict logic — we
do NOT re-implement any parsing here) and runs the result through the policy.
Two rails:

- ``wauldo_fact_check``       — verify an answer against its context.
- ``wauldo_verify_citations`` — check the answer is sufficiently cited.

The actions are async and use the async SDK client so they never block NeMo's
event loop, reuse a single client per registration, surface per-claim
evidence, and degrade deliberately (missing context / Wauldo outage) via
``RailConfig`` instead of crashing the generation.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Optional

from wauldo.async_client import AsyncHttpClient

from ._otel import span
from .config import RailConfig
from .policy import PolicyThresholds, RailDecision, decide

logger = logging.getLogger("wauldo_nemo")

ALLOW = "allow"
ANNOTATE = "annotate"
REFUSE = "refuse"

_DECISION_TO_STR = {
    RailDecision.PASS: ALLOW,
    RailDecision.ANNOTATE: ANNOTATE,
    RailDecision.REFUSE: REFUSE,
}


def _client_from_env(config: RailConfig) -> AsyncHttpClient:
    return AsyncHttpClient(
        base_url=os.environ.get("WAULDO_BASE_URL", "https://api.wauldo.com"),
        api_key=os.environ.get("WAULDO_API_KEY"),
        timeout=int(config.timeout),
        max_retries=config.max_retries,
        retry_backoff=config.retry_backoff,
    )


def _claims_digest(claims: Any) -> list[dict[str, Any]]:
    """Surface per-claim evidence — the headline explainability that a plain
    pass/fail throws away."""
    out = []
    for c in claims or []:
        out.append(
            {
                "text": c.text,
                "verdict": c.verdict,
                "supported": c.supported,
                "evidence": c.evidence,
                "reason": c.reason,
            }
        )
    return out


def _degraded(decision: RailDecision, note: str) -> dict[str, Any]:
    """Verdict block for the cases where we couldn't actually verify
    (no context, or Wauldo errored). Honest: verdict is ``unverified``."""
    return {
        "decision": _DECISION_TO_STR[decision],
        "verdict": "unverified",
        "action": "review",
        "confidence": 0.0,
        "hallucination_rate": 0.0,
        "supported_claims": 0,
        "total_claims": 0,
        "claims": [],
        "relevance": None,
        "note": note,
    }


def _chunks_to_str(rc: Any) -> Optional[str]:
    """Coerce NeMo's `$relevant_chunks` (str, or list of str / {content}) to
    a single context string."""
    if isinstance(rc, str):
        return rc
    if isinstance(rc, list):
        parts = [c.get("content", "") if isinstance(c, dict) else str(c) for c in rc]
        joined = "\n\n".join(p for p in parts if p)
        return joined or None
    return None


def _resolve_context(source_context: Optional[str], context: Any) -> Optional[str]:
    """Explicit `source_context` wins; otherwise auto-read `relevant_chunks`
    from NeMo's injected context dict (zero-config for RAG bots)."""
    if source_context:
        return source_context
    if isinstance(context, str):
        return context
    if isinstance(context, dict):
        return _chunks_to_str(context.get("relevant_chunks"))
    return None


def _resolve_query(query: Optional[str], context: Any) -> Optional[str]:
    """Explicit `query` wins; otherwise auto-read the user's question from
    NeMo's injected context dict (`last_user_message`, maintained by the
    colang runtime) — zero-config relevance for chat bots."""
    if query and query.strip():
        return query
    if isinstance(context, dict):
        last = context.get("last_user_message")
        if isinstance(last, str) and last.strip():
            return last
    return None


def _relevance_digest(relevance: Any) -> Optional[dict[str, Any]]:
    """Flatten the SDK's `RelevanceResult` (score / verdict / rationale) so the
    payload stays a plain dict consumable from Colang."""
    if relevance is None:
        return None
    return {
        "score": relevance.score,
        "verdict": relevance.verdict,
        "rationale": relevance.rationale,
    }


def _log_decision(kind: str, payload: dict[str, Any]) -> None:
    """Emit one structured log per decision. ``extra={"wauldo": {...}}`` keeps
    it formatter-agnostic — plain logging, structlog, or a JSON formatter the
    caller already runs all pick it up. The verbose lists are dropped."""
    skip = {"claims", "uncited_sentences"}
    logger.info(kind, extra={"wauldo": {k: v for k, v in payload.items() if k not in skip}})


def _finalize(
    payload: dict[str, Any],
    config: RailConfig,
    request_id: str,
    latency_ms: Optional[float],
    kind: str,
) -> dict[str, Any]:
    """Attach correlation fields, apply shadow mode, and log — the single exit
    path for every rail return. Shadow never blocks the user (decision forced to
    ``allow``) but the real verdict stays in the payload + the log."""
    payload["request_id"] = request_id
    payload["latency_ms"] = latency_ms
    shadowed = config.shadow and payload["decision"] != ALLOW
    if shadowed:
        payload["decision"] = ALLOW
    payload["shadowed"] = shadowed
    _log_decision(kind, payload)
    return payload


def _refuse_message(template: str, claims: list[dict[str, Any]], verdict: str) -> Optional[str]:
    """Render ``config.refuse_template`` from the first unsupported claim. A bad
    template must never crash the generation — on error we log and skip it."""
    failed = next((c for c in claims if not c.get("supported")), {})
    try:
        return template.format(
            first_failed_claim=failed.get("text", ""),
            evidence=failed.get("evidence") or "",
            verdict=verdict,
        )
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("invalid refuse_template (%s) — skipping refuse_message", exc)
        return None


async def wauldo_fact_check_action(
    bot_message: str,
    source_context: Optional[str] = None,
    query: Optional[str] = None,
    *,
    context: Any = None,
    client: Optional[AsyncHttpClient] = None,
    config: Optional[RailConfig] = None,
    thresholds: Optional[PolicyThresholds] = None,
) -> dict[str, Any]:
    """Verify ``bot_message`` against its context and return a rail verdict.

    ``source_context`` is the ground-truth to check against. If omitted, it is
    auto-read from NeMo's injected ``context`` dict (``relevant_chunks``) — so
    a RAG bot needs no extra wiring. NOTE: ``context`` is reserved by NeMo
    (it injects the conversation context dict), which is why the explicit
    argument is ``source_context``.

    ``query`` is the user's question, used by the server to compute a
    ``relevance`` block (decoupled from the factual verdict — a verified
    answer can still be off-topic). If omitted, it is auto-read from the
    context (``last_user_message``). Disable with
    ``RailConfig(relevance_mode=None)``.

    Returns a dict consumable from Colang with a ``decision`` of
    ``allow`` / ``annotate`` / ``refuse``, plus the per-claim ``claims``
    (each with ``evidence`` / ``reason``) and ``relevance``
    (``score`` / ``verdict`` / ``rationale``, or ``None``).
    """
    config = config or RailConfig(thresholds=thresholds or PolicyThresholds())
    request_id = uuid.uuid4().hex[:12]
    kind = "wauldo_fact_check"

    resolved = _resolve_context(source_context, context)
    # No ground-truth context → nothing to verify against.
    if not resolved or not resolved.strip():
        return _finalize(
            _degraded(config.on_missing_context, "no_context"), config, request_id, None, kind
        )

    resolved_query = _resolve_query(query, context) if config.relevance_mode else None

    own_client = client is None
    client = client or _client_from_env(config)
    t0 = time.perf_counter()
    with span("wauldo.fact_check") as sp:
        sp.set_attribute("wauldo.request_id", request_id)
        try:
            if resolved_query:
                result = await client.fact_check(
                    bot_message,
                    resolved,
                    mode=config.mode,
                    query=resolved_query,
                    relevance_mode=config.relevance_mode,
                )
            else:
                result = await client.fact_check(bot_message, resolved, mode=config.mode)
        except Exception as exc:  # noqa: BLE001 — network / 5xx / timeout
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.warning(
                "wauldo fact-check failed (%s) [request_id=%s] — applying on_error=%s",
                exc,
                request_id,
                config.on_error.name,
            )
            return _finalize(
                _degraded(config.on_error, f"error:{type(exc).__name__}"),
                config,
                request_id,
                latency_ms,
                kind,
            )
        finally:
            if own_client:
                await _close(client)

        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        decision = decide(result, config.thresholds)
        claims = _claims_digest(result.claims)
        relevance = _relevance_digest(getattr(result, "relevance", None))
        payload: dict[str, Any] = {
            "decision": _DECISION_TO_STR[decision],
            "verdict": result.verdict,
            "action": result.action,
            "confidence": result.confidence,
            "hallucination_rate": result.hallucination_rate,
            "supported_claims": result.supported_claims,
            "total_claims": result.total_claims,
            "claims": claims,
            "relevance": relevance,
            "relevance_warning": getattr(result, "relevance_warning", None),
            "note": None,
        }
        if config.thresholds.min_relevance_score > 0.0 and relevance is None:
            # The floor is armed but no score was computed — `decide()` already
            # escalated to ANNOTATE; say WHY, so logs/traces can tell
            # "relevance disabled by config" from "score unavailable".
            payload["note"] = (
                "relevance_floor_set_but_disabled"
                if config.relevance_mode is None
                else "relevance_floor_set_but_unavailable"
            )
            sp.set_attribute("wauldo.relevance_gate_reason", payload["note"])
        if config.refuse_template and decision == RailDecision.REFUSE:
            payload["refuse_message"] = _refuse_message(
                config.refuse_template, claims, result.verdict
            )
        sp.set_attribute("wauldo.decision", payload["decision"])
        sp.set_attribute("wauldo.verdict", result.verdict)
        sp.set_attribute("wauldo.hallucination_rate", result.hallucination_rate)
        sp.set_attribute("wauldo.total_claims", result.total_claims)
        if relevance is not None:
            sp.set_attribute("wauldo.relevance_score", relevance["score"])
            sp.set_attribute("wauldo.relevance_verdict", relevance["verdict"])

    return _finalize(payload, config, request_id, latency_ms, kind)


async def wauldo_verify_citations_action(
    bot_message: str,
    sources: Optional[list[dict[str, Any]]] = None,
    *,
    client: Optional[AsyncHttpClient] = None,
    config: Optional[RailConfig] = None,
) -> dict[str, Any]:
    """Check ``bot_message`` is sufficiently cited (POST /v1/verify).

    ``decision`` is ``allow`` when citations meet ``min_citation_ratio``,
    otherwise ``config.on_insufficient_citations``.
    """
    config = config or RailConfig()
    request_id = uuid.uuid4().hex[:12]
    kind = "wauldo_verify_citations"
    own_client = client is None
    client = client or _client_from_env(config)
    t0 = time.perf_counter()
    with span("wauldo.verify_citations") as sp:
        sp.set_attribute("wauldo.request_id", request_id)
        try:
            result = await client.verify_citation(
                bot_message, sources=sources, threshold=config.min_citation_ratio
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.warning(
                "wauldo citation check failed (%s) [request_id=%s] — applying on_error=%s",
                exc,
                request_id,
                config.on_error.name,
            )
            return _finalize(
                {
                    "decision": _DECISION_TO_STR[config.on_error],
                    "citation_ratio": 0.0,
                    "has_sufficient_citations": False,
                    "phantom_count": 0,
                    "uncited_sentences": [],
                    "note": f"error:{type(exc).__name__}",
                },
                config,
                request_id,
                latency_ms,
                kind,
            )
        finally:
            if own_client:
                await _close(client)

        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        decision = (
            RailDecision.PASS
            if result.has_sufficient_citations
            else config.on_insufficient_citations
        )
        payload = {
            "decision": _DECISION_TO_STR[decision],
            "citation_ratio": result.citation_ratio,
            "has_sufficient_citations": result.has_sufficient_citations,
            "phantom_count": result.phantom_count or 0,
            "uncited_sentences": result.uncited_sentences,
            "note": None,
        }
        sp.set_attribute("wauldo.decision", payload["decision"])
        sp.set_attribute("wauldo.citation_ratio", result.citation_ratio)

    return _finalize(payload, config, request_id, latency_ms, kind)


def _action_result(payload: dict[str, Any]) -> Any:
    """Wrap a rail payload in NeMo's ``ActionResult`` so the verdict is both the
    action's return value (``$result``) AND written into the conversation
    context — downstream rails / ``$history`` / a UI can read ``$wauldo_evidence``.
    Falls back to the plain dict if NeMo isn't importable (keeps tests dep-free)."""
    try:
        from nemoguardrails.actions.actions import ActionResult
    except Exception:  # noqa: BLE001
        return payload
    evidence = [
        c["evidence"]
        for c in payload.get("claims", [])
        if not c.get("supported") and c.get("evidence")
    ]
    return ActionResult(
        return_value=payload,
        context_updates={
            "wauldo_decision": payload["decision"],
            "wauldo_verdict": payload.get("verdict"),
            "wauldo_evidence": evidence,
            "wauldo_relevance": payload.get("relevance"),
            "wauldo_request_id": payload.get("request_id"),
        },
    )


async def _close(client: AsyncHttpClient) -> None:
    close = getattr(client, "close", None) or getattr(client, "aclose", None)
    if close is None:
        return
    try:
        res = close()
        if hasattr(res, "__await__"):
            await res
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


def register(
    rails: Any,
    *,
    config: Optional[RailConfig] = None,
    thresholds: Optional[PolicyThresholds] = None,
    fact_check: bool = True,
    verify_citations: bool = False,
) -> None:
    """Register the Wauldo rails on an `LLMRails` instance.

    A single async client is built here and reused across calls. Pass a
    `RailConfig` for full control, or just `thresholds` for the common case.
    Enable the citation rail with `verify_citations=True`.
    """
    config = config or RailConfig(thresholds=thresholds or PolicyThresholds())
    client = _client_from_env(config)

    if fact_check:
        # `context` is auto-injected by NeMo (the conversation context dict);
        # `source_context` lets a flow pass the ground-truth explicitly.
        async def _fact_check(
            bot_message: str = "",
            source_context: Optional[str] = None,
            query: Optional[str] = None,
            context: Any = None,
        ) -> Any:
            payload = await wauldo_fact_check_action(
                bot_message, source_context, query, context=context, client=client, config=config
            )
            return _action_result(payload)

        rails.register_action(_fact_check, name="wauldo_fact_check")

    if verify_citations:

        async def _verify_citations(
            bot_message: str = "", sources: Optional[list[dict[str, Any]]] = None
        ) -> Any:
            payload = await wauldo_verify_citations_action(
                bot_message, sources, client=client, config=config
            )
            return _action_result(payload)

        rails.register_action(_verify_citations, name="wauldo_verify_citations")
