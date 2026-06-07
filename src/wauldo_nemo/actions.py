"""NeMo Guardrails output rail backed by Wauldo fact-checking.

Thin glue: it calls `wauldo.HttpClient.fact_check()` (the SDK is the single
source of verdict logic — we do NOT re-implement any parsing here) and runs
the result through `policy.decide`. Register it on an `LLMRails` instance and
wire it from a Colang `output rail` flow (see `examples/config/`).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from wauldo import HttpClient

from .policy import PolicyThresholds, RailDecision, decide

# Returned to Colang so the flow can branch on it.
ALLOW = "allow"
ANNOTATE = "annotate"
REFUSE = "refuse"

_DECISION_TO_STR = {
    RailDecision.PASS: ALLOW,
    RailDecision.ANNOTATE: ANNOTATE,
    RailDecision.REFUSE: REFUSE,
}


def _client_from_env() -> HttpClient:
    return HttpClient(
        base_url=os.environ.get("WAULDO_BASE_URL", "https://api.wauldo.com"),
        api_key=os.environ.get("WAULDO_API_KEY"),
    )


async def wauldo_fact_check_action(
    bot_message: str,
    context: Optional[str] = None,
    *,
    client: Optional[HttpClient] = None,
    thresholds: Optional[PolicyThresholds] = None,
    mode: str = "lexical",
) -> dict[str, Any]:
    """Verify `bot_message` against `context` and return a rail verdict.

    Returns a dict consumable from Colang::
        {decision, verdict, action, confidence, hallucination_rate,
         supported_claims, total_claims}

    `decision` is one of `allow` / `annotate` / `refuse`.
    """
    client = client or _client_from_env()
    thresholds = thresholds or PolicyThresholds()

    result = client.fact_check(bot_message, source_context=context, mode=mode)
    decision = decide(result, thresholds)

    return {
        "decision": _DECISION_TO_STR[decision],
        "verdict": result.verdict,
        "action": result.action,
        "confidence": result.confidence,
        "hallucination_rate": result.hallucination_rate,
        "supported_claims": result.supported_claims,
        "total_claims": result.total_claims,
    }


def register(rails: Any, *, thresholds: Optional[PolicyThresholds] = None) -> None:
    """Register the action on an `LLMRails` instance under the name
    `wauldo_fact_check`. Import-light wrapper so `policy`/this module stay
    usable without the guardrails runtime installed."""
    th = thresholds or PolicyThresholds()

    async def _action(bot_message: str = "", context: Optional[str] = None) -> dict:
        return await wauldo_fact_check_action(
            bot_message, context, thresholds=th
        )

    rails.register_action(_action, name="wauldo_fact_check")
