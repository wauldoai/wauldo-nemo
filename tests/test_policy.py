"""Policy unit tests — no NeMo Guardrails runtime required.

These lock the honesty invariant: the rail never downgrades the server
verdict, only escalates it.
"""

from dataclasses import dataclass
from typing import Any, Optional

from wauldo_nemo.policy import PolicyThresholds, RailDecision, decide


@dataclass
class FakeRelevance:
    score: float
    verdict: str = "relevant"
    rationale: Optional[str] = None


@dataclass
class FakeResult:
    action: str
    confidence: float = 0.9
    hallucination_rate: float = 0.0
    relevance: Any = None


def test_allow_passes_by_default():
    r = FakeResult(action="allow")
    assert decide(r, PolicyThresholds()) is RailDecision.PASS


def test_review_annotates_by_default():
    r = FakeResult(action="review")
    assert decide(r, PolicyThresholds()) is RailDecision.ANNOTATE


def test_block_refuses():
    r = FakeResult(action="block")
    assert decide(r, PolicyThresholds()) is RailDecision.REFUSE


def test_strict_escalates_review_to_refuse():
    r = FakeResult(action="review")
    assert decide(r, PolicyThresholds(strict=True)) is RailDecision.REFUSE


def test_strict_never_downgrades_block():
    r = FakeResult(action="block")
    assert decide(r, PolicyThresholds(strict=True)) is RailDecision.REFUSE


def test_low_confidence_downgrades_allow_to_annotate():
    r = FakeResult(action="allow", confidence=0.2)
    assert decide(r, PolicyThresholds(min_confidence=0.5)) is RailDecision.ANNOTATE


def test_high_halluc_rate_forces_refuse():
    r = FakeResult(action="allow", confidence=0.99, hallucination_rate=0.8)
    assert decide(r, PolicyThresholds(max_hallucination_rate=0.5)) is RailDecision.REFUSE


def test_thresholds_only_escalate_never_lenient():
    # A blocking verdict with high confidence + low halluc must still REFUSE.
    r = FakeResult(action="block", confidence=1.0, hallucination_rate=0.0)
    assert decide(r, PolicyThresholds(min_confidence=0.0)) is RailDecision.REFUSE


# ── relevance gate: separate axis, escalate-only ─────────────────────────


def test_low_relevance_annotates_by_default():
    r = FakeResult(action="allow", relevance=FakeRelevance(score=0.3, verdict="off_topic"))
    assert decide(r, PolicyThresholds(min_relevance_score=0.7)) is RailDecision.ANNOTATE


def test_low_relevance_can_refuse():
    r = FakeResult(action="allow", relevance=FakeRelevance(score=0.3, verdict="off_topic"))
    t = PolicyThresholds(min_relevance_score=0.7, on_low_relevance=RailDecision.REFUSE)
    assert decide(r, t) is RailDecision.REFUSE


def test_strict_escalates_low_relevance_annotate_to_refuse():
    r = FakeResult(action="allow", relevance=FakeRelevance(score=0.3, verdict="off_topic"))
    t = PolicyThresholds(min_relevance_score=0.7, strict=True)
    assert decide(r, t) is RailDecision.REFUSE


def test_relevance_floor_zero_is_inert_even_when_off_topic():
    # Default min_relevance_score=0.0 → relevance never changes the decision.
    r = FakeResult(action="allow", relevance=FakeRelevance(score=0.1, verdict="off_topic"))
    assert decide(r, PolicyThresholds()) is RailDecision.PASS


def test_high_relevance_passes_the_gate():
    r = FakeResult(action="allow", relevance=FakeRelevance(score=0.92))
    assert decide(r, PolicyThresholds(min_relevance_score=0.7)) is RailDecision.PASS


def test_relevance_floor_set_but_relevance_missing_annotates():
    # The gate is never silently skipped: floor demanded + no relevance
    # computed (no query / embeddings unavailable) → flag, don't invent.
    r = FakeResult(action="allow", relevance=None)
    assert decide(r, PolicyThresholds(min_relevance_score=0.7)) is RailDecision.ANNOTATE


def test_relevance_never_downgrades_a_block():
    # Verified-relevance cannot soften a factual block (escalate-only).
    r = FakeResult(action="block", relevance=FakeRelevance(score=0.99))
    assert decide(r, PolicyThresholds(min_relevance_score=0.7)) is RailDecision.REFUSE


def test_result_without_relevance_attribute_still_works():
    # Pre-0.4 stand-ins (no `relevance` field) must not crash the policy.
    @dataclass
    class Legacy:
        action: str
        confidence: float = 0.9
        hallucination_rate: float = 0.0

    assert decide(Legacy(action="allow"), PolicyThresholds()) is RailDecision.PASS
    t = PolicyThresholds(min_relevance_score=0.7)
    assert decide(Legacy(action="allow"), t) is RailDecision.ANNOTATE
