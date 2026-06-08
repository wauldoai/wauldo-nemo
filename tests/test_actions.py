"""Action tests — no NeMo runtime, a fake async client (no network)."""

import asyncio

from wauldo import ClaimResult, FactCheckResponse, VerifyCitationResponse

from wauldo_nemo import (
    PolicyThresholds,
    RailConfig,
    RailDecision,
    wauldo_fact_check_action,
    wauldo_verify_citations_action,
)


class FakeClient:
    def __init__(self, fc=None, vc=None, exc=None):
        self._fc, self._vc, self._exc = fc, vc, exc

    async def fact_check(self, text, source_context, mode="lexical"):
        if self._exc:
            raise self._exc
        return self._fc

    async def verify_citation(self, text, sources=None, threshold=None):
        if self._exc:
            raise self._exc
        return self._vc

    async def close(self):
        pass


def _fc(verdict="verified", action="allow", halluc=0.0, claims=None):
    return FactCheckResponse(
        verdict=verdict,
        action=action,
        hallucination_rate=halluc,
        mode="lexical",
        total_claims=len(claims or []),
        supported_claims=sum(c.supported for c in (claims or [])),
        confidence=0.9,
        claims=claims or [],
        processing_time_ms=5,
    )


def _claim(supported, evidence=None, reason=None):
    return ClaimResult(
        text="Rust released 2010",
        claim_type="Fact",
        supported=supported,
        confidence=0.9,
        confidence_label="sufficient",
        verdict="verified" if supported else "rejected",
        action="allow" if supported else "block",
        reason=reason,
        evidence=evidence,
    )


def run(coro):
    return asyncio.run(coro)


# ── missing context ──────────────────────────────────────────────────────


def test_no_context_does_not_crash_and_annotates_by_default():
    out = run(wauldo_fact_check_action("answer", None, client=FakeClient()))
    assert out["decision"] == "annotate"
    assert out["note"] == "no_context"
    assert out["verdict"] == "unverified"


def test_no_context_can_be_configured_to_pass():
    cfg = RailConfig(on_missing_context=RailDecision.PASS)
    out = run(wauldo_fact_check_action("answer", "", client=FakeClient(), config=cfg))
    assert out["decision"] == "allow"


# ── fail-open / fail-closed ──────────────────────────────────────────────


def test_error_fail_open_by_default():
    out = run(wauldo_fact_check_action("a", "ctx", client=FakeClient(exc=RuntimeError("down"))))
    assert out["decision"] == "allow"
    assert out["note"].startswith("error:")


def test_error_fail_closed_when_configured():
    cfg = RailConfig(on_error=RailDecision.REFUSE)
    out = run(
        wauldo_fact_check_action("a", "ctx", client=FakeClient(exc=RuntimeError()), config=cfg)
    )
    assert out["decision"] == "refuse"


# ── happy path surfaces per-claim evidence ───────────────────────────────


def test_blocked_answer_refuses_and_surfaces_evidence():
    claims = [_claim(False, evidence="source says 2010", reason="numerical_mismatch")]
    fc = _fc(verdict="rejected", action="block", halluc=1.0, claims=claims)
    out = run(wauldo_fact_check_action("Rust 1999", "ctx", client=FakeClient(fc=fc)))
    assert out["decision"] == "refuse"
    assert out["claims"][0]["evidence"] == "source says 2010"
    assert out["claims"][0]["reason"] == "numerical_mismatch"


def test_strict_thresholds_escalate():
    fc = _fc(verdict="weak", action="review")
    cfg = RailConfig(thresholds=PolicyThresholds(strict=True))
    out = run(wauldo_fact_check_action("a", "ctx", client=FakeClient(fc=fc), config=cfg))
    assert out["decision"] == "refuse"


# ── citation rail ────────────────────────────────────────────────────────


def test_citation_rail_flags_undercited():
    vc = VerifyCitationResponse(
        citation_ratio=0.1,
        has_sufficient_citations=False,
        sentence_count=3,
        citation_count=0,
        uncited_sentences=["a", "b"],
        processing_time_ms=1,
    )
    out = run(wauldo_verify_citations_action("answer", client=FakeClient(vc=vc)))
    assert out["decision"] == "annotate"
    assert out["has_sufficient_citations"] is False


def test_citation_rail_passes_when_sufficient():
    vc = VerifyCitationResponse(
        citation_ratio=0.9,
        has_sufficient_citations=True,
        sentence_count=3,
        citation_count=3,
        uncited_sentences=[],
        processing_time_ms=1,
    )
    out = run(wauldo_verify_citations_action("answer", client=FakeClient(vc=vc)))
    assert out["decision"] == "allow"


# ── retry config threads to the transport (latency bound on outage) ──────


def test_client_from_env_applies_retry_config(monkeypatch):
    """A guardrail must fail fast: the RailConfig retry knobs reach the SDK
    transport so `timeout` is the real upper bound on outage latency, not
    `retries × backoff`."""
    from wauldo_nemo.actions import _client_from_env

    monkeypatch.setenv("WAULDO_BASE_URL", "http://x")
    monkeypatch.setenv("WAULDO_API_KEY", "k")
    client = _client_from_env(RailConfig(max_retries=1, retry_backoff=0.5))
    transport = getattr(client, "_transport", None)
    assert transport is not None
    assert transport.max_retries == 1
    assert transport.retry_backoff == 0.5


def test_rail_config_defaults_to_single_attempt():
    """Default must be fail-fast (1 attempt) — a verification outage cannot
    add backoff latency to every response."""
    assert RailConfig().max_retries == 1
