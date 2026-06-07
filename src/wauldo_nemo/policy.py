"""Rail decision policy — the honesty-critical core, framework-agnostic.

This module has NO dependency on NeMo Guardrails. It maps a Wauldo
`/v1/fact-check` result to a rail decision so the mapping lives in one
place and is unit-testable without the guardrails runtime.

Design rule (honesty): we NEVER make the verdict more lenient than the
server already decided. The server returns its own recommended `action`
(`allow` / `review` / `block`); the policy starts from that and may only
*escalate* severity based on configured thresholds. It never downgrades a
`block` into a pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol


class RailDecision(IntEnum):
    """Ordered by severity so `max(...)` picks the most restrictive."""

    PASS = 0  # let the response through unchanged
    ANNOTATE = 1  # let it through but flag it as unverified / weak
    REFUSE = 2  # block the response, return a safe fallback instead


class FactCheckLike(Protocol):
    """Structural view of `wauldo.FactCheckResponse` — only the fields the
    policy reads. Keeps this module decoupled from the SDK's concrete type
    (and lets tests pass a lightweight stand-in)."""

    action: str
    confidence: float
    hallucination_rate: float


@dataclass(frozen=True)
class PolicyThresholds:
    """Knobs an integrator tightens. Defaults respect the server verdict
    as-is. Every knob can only make the rail STRICTER, never more lenient.

    - `min_confidence`: below this, an otherwise-`allow` response is
      downgraded to ANNOTATE.
    - `max_hallucination_rate`: above this, the response is REFUSED
      regardless of the server action.
    - `strict`: when True, a server `review` becomes REFUSE instead of
      ANNOTATE.
    """

    min_confidence: float = 0.0
    max_hallucination_rate: float = 1.0
    strict: bool = False


def _base_decision(action: str) -> RailDecision:
    """Map the server's own recommendation to a rail decision."""
    return {
        "allow": RailDecision.PASS,
        "review": RailDecision.ANNOTATE,
        "block": RailDecision.REFUSE,
    }.get(action, RailDecision.ANNOTATE)  # unknown action → fail safe-ish


def decide(result: FactCheckLike, thresholds: PolicyThresholds) -> RailDecision:
    """Return the most restrictive decision implied by the server verdict
    and the configured thresholds. Only escalates, never downgrades."""
    decision = _base_decision(result.action)

    if result.hallucination_rate > thresholds.max_hallucination_rate:
        decision = max(decision, RailDecision.REFUSE)

    if result.confidence < thresholds.min_confidence:
        decision = max(decision, RailDecision.ANNOTATE)

    if thresholds.strict and decision == RailDecision.ANNOTATE:
        decision = RailDecision.REFUSE

    return decision
