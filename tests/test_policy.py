"""Policy unit tests — no NeMo Guardrails runtime required.

These lock the honesty invariant: the rail never downgrades the server
verdict, only escalates it.
"""

from dataclasses import dataclass

from wauldo_nemo.policy import PolicyThresholds, RailDecision, decide


@dataclass
class FakeResult:
    action: str
    confidence: float = 0.9
    hallucination_rate: float = 0.0


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
