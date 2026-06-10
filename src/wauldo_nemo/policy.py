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
    - `min_relevance_score`: relevance gate, a SEPARATE policy axis from
      factuality (a response can be fully verified AND off-topic). When > 0
      and the server's `relevance.score` is below it, the decision escalates
      to `on_low_relevance`. If the floor is set but relevance could not be
      computed (no query / embeddings unavailable), the response is ANNOTATEd
      — the gate is never silently skipped, and never invents a score.
    - `on_low_relevance`: escalation applied when relevance is below the
      floor. ANNOTATE by default; REFUSE to block off-topic-but-true answers.
    - `strict`: when True, a server `review` becomes REFUSE instead of
      ANNOTATE.
    """

    min_confidence: float = 0.0
    max_hallucination_rate: float = 1.0
    min_relevance_score: float = 0.0
    on_low_relevance: RailDecision = RailDecision.ANNOTATE
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

    if thresholds.min_relevance_score > 0.0:
        # `relevance` is optional on the response (and absent on older SDK
        # stand-ins) — getattr keeps pre-0.4 conformers working at runtime.
        relevance = getattr(result, "relevance", None)
        score = getattr(relevance, "score", None)
        if score is None:
            # Floor demanded but relevance not computed: flag it, never
            # pretend the gate passed.
            decision = max(decision, RailDecision.ANNOTATE)
        elif score < thresholds.min_relevance_score:
            decision = max(decision, thresholds.on_low_relevance)

    if thresholds.strict and decision == RailDecision.ANNOTATE:
        decision = RailDecision.REFUSE

    return decision
