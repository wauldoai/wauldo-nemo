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
from typing import Any, Optional

from wauldo.async_client import AsyncHttpClient

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


def _claims_digest(claims: Any) -> list[dict]:
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


async def wauldo_fact_check_action(
    bot_message: str,
    source_context: Optional[str] = None,
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

    Returns a dict consumable from Colang with a ``decision`` of
    ``allow`` / ``annotate`` / ``refuse``, plus the per-claim ``claims``
    (each with ``evidence`` / ``reason``).
    """
    config = config or RailConfig(thresholds=thresholds or PolicyThresholds())

    resolved = _resolve_context(source_context, context)
    # No ground-truth context → nothing to verify against.
    if not resolved or not resolved.strip():
        return _degraded(config.on_missing_context, "no_context")

    own_client = client is None
    client = client or _client_from_env(config)
    try:
        result = await client.fact_check(bot_message, resolved, mode=config.mode)
    except Exception as exc:  # noqa: BLE001 — network / 5xx / timeout
        logger.warning(
            "wauldo fact-check failed (%s) — applying on_error=%s",
            exc,
            config.on_error.name,
        )
        return _degraded(config.on_error, f"error:{type(exc).__name__}")
    finally:
        if own_client:
            await _close(client)

    decision = decide(result, config.thresholds)
    return {
        "decision": _DECISION_TO_STR[decision],
        "verdict": result.verdict,
        "action": result.action,
        "confidence": result.confidence,
        "hallucination_rate": result.hallucination_rate,
        "supported_claims": result.supported_claims,
        "total_claims": result.total_claims,
        "claims": _claims_digest(result.claims),
        "note": None,
    }


async def wauldo_verify_citations_action(
    bot_message: str,
    sources: Optional[list] = None,
    *,
    client: Optional[AsyncHttpClient] = None,
    config: Optional[RailConfig] = None,
) -> dict[str, Any]:
    """Check ``bot_message`` is sufficiently cited (POST /v1/verify).

    ``decision`` is ``allow`` when citations meet ``min_citation_ratio``,
    otherwise ``config.on_insufficient_citations``.
    """
    config = config or RailConfig()
    own_client = client is None
    client = client or _client_from_env(config)
    try:
        result = await client.verify_citation(
            bot_message, sources=sources, threshold=config.min_citation_ratio
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "wauldo citation check failed (%s) — applying on_error=%s",
            exc,
            config.on_error.name,
        )
        return {
            "decision": _DECISION_TO_STR[config.on_error],
            "citation_ratio": 0.0,
            "has_sufficient_citations": False,
            "phantom_count": 0,
            "uncited_sentences": [],
            "note": f"error:{type(exc).__name__}",
        }
    finally:
        if own_client:
            await _close(client)

    decision = (
        RailDecision.PASS if result.has_sufficient_citations else config.on_insufficient_citations
    )
    return {
        "decision": _DECISION_TO_STR[decision],
        "citation_ratio": result.citation_ratio,
        "has_sufficient_citations": result.has_sufficient_citations,
        "phantom_count": result.phantom_count or 0,
        "uncited_sentences": result.uncited_sentences,
        "note": None,
    }


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
            context: Any = None,
        ) -> dict:
            return await wauldo_fact_check_action(
                bot_message, source_context, context=context, client=client, config=config
            )

        rails.register_action(_fact_check, name="wauldo_fact_check")

    if verify_citations:

        async def _verify_citations(bot_message: str = "", sources: Optional[list] = None) -> dict:
            return await wauldo_verify_citations_action(
                bot_message, sources, client=client, config=config
            )

        rails.register_action(_verify_citations, name="wauldo_verify_citations")
