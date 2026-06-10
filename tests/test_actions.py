"""Action tests — no NeMo runtime, a fake async client (no network)."""

import asyncio

from wauldo import ClaimResult, FactCheckResponse, RelevanceResult, VerifyCitationResponse

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
        self.last_query = None
        self.last_relevance_mode = None

    async def fact_check(
        self, text, source_context, mode="lexical", query=None, relevance_mode=None
    ):
        self.last_query = query
        self.last_relevance_mode = relevance_mode
        if self._exc:
            raise self._exc
        return self._fc

    async def verify_citation(self, text, sources=None, threshold=None):
        if self._exc:
            raise self._exc
        return self._vc

    async def close(self):
        pass


def _fc(verdict="verified", action="allow", halluc=0.0, claims=None, relevance=None):
    return FactCheckResponse(
        verdict=verdict,
        action=action,
        hallucination_rate=halluc,
        mode="lexical",
        total_claims=len(claims or []),
        supported_claims=sum(c.supported for c in (claims or [])),
        confidence=0.9,
        claims=claims or [],
        relevance=relevance,
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


# ── observability: request_id + latency on every payload ─────────────────


def test_payload_carries_request_id_and_latency():
    fc = _fc()
    out = run(wauldo_fact_check_action("a", "ctx", client=FakeClient(fc=fc)))
    assert len(out["request_id"]) == 12
    assert isinstance(out["latency_ms"], float)
    assert out["shadowed"] is False


# ── shadow / audit mode: never block, keep the real verdict ──────────────


def test_shadow_mode_never_blocks_but_keeps_verdict():
    claims = [_claim(False, evidence="source says 2010", reason="numerical_mismatch")]
    fc = _fc(verdict="rejected", action="block", halluc=1.0, claims=claims)
    cfg = RailConfig(shadow=True)
    out = run(wauldo_fact_check_action("Rust 1999", "ctx", client=FakeClient(fc=fc), config=cfg))
    # user is never blocked...
    assert out["decision"] == "allow"
    assert out["shadowed"] is True
    # ...but the real verdict + evidence stay visible for logging/observers.
    assert out["verdict"] == "rejected"
    assert out["hallucination_rate"] == 1.0
    assert out["claims"][0]["evidence"] == "source says 2010"


def test_shadow_mode_on_citation_rail():
    vc = VerifyCitationResponse(
        citation_ratio=0.1,
        has_sufficient_citations=False,
        sentence_count=3,
        citation_count=0,
        uncited_sentences=["a"],
        processing_time_ms=1,
    )
    out = run(
        wauldo_verify_citations_action(
            "answer", client=FakeClient(vc=vc), config=RailConfig(shadow=True)
        )
    )
    assert out["decision"] == "allow"
    assert out["shadowed"] is True
    assert out["has_sufficient_citations"] is False


# ── relevance: query threading + decoupled gate ──────────────────────────


def _rel(score, verdict="relevant"):
    return RelevanceResult(score=score, verdict=verdict)


def test_explicit_query_threads_to_sdk_with_relevance_mode():
    client = FakeClient(fc=_fc())
    run(wauldo_fact_check_action("a", "ctx", "When was Rust released?", client=client))
    assert client.last_query == "When was Rust released?"
    assert client.last_relevance_mode == "fast"


def test_auto_query_from_nemo_last_user_message():
    client = FakeClient(fc=_fc())
    ctx = {"relevant_chunks": "ctx", "last_user_message": "When was Rust released?"}
    run(wauldo_fact_check_action("a", None, context=ctx, client=client))
    assert client.last_query == "When was Rust released?"


def test_explicit_query_wins_over_context():
    client = FakeClient(fc=_fc())
    ctx = {"last_user_message": "from context"}
    run(wauldo_fact_check_action("a", "ctx", "explicit", context=ctx, client=client))
    assert client.last_query == "explicit"


def test_no_query_sends_no_relevance_params_and_payload_relevance_is_none():
    client = FakeClient(fc=_fc())
    out = run(wauldo_fact_check_action("a", "ctx", client=client))
    assert client.last_query is None
    assert client.last_relevance_mode is None
    assert out["relevance"] is None


def test_relevance_mode_none_disables_auto_query():
    client = FakeClient(fc=_fc())
    ctx = {"relevant_chunks": "ctx", "last_user_message": "a question"}
    cfg = RailConfig(relevance_mode=None)
    run(wauldo_fact_check_action("a", None, context=ctx, client=client, config=cfg))
    assert client.last_query is None


def test_relevance_block_surfaces_in_payload():
    fc = _fc(relevance=_rel(0.91))
    out = run(wauldo_fact_check_action("a", "ctx", "q", client=FakeClient(fc=fc)))
    assert out["relevance"] == {"score": 0.91, "verdict": "relevant", "rationale": None}


def test_verified_but_off_topic_allows_by_default():
    # Decoupling: relevance never touches the factual verdict, and the
    # default floor (0.0) never changes the decision.
    fc = _fc(verdict="verified", action="allow", relevance=_rel(0.2, "off_topic"))
    out = run(wauldo_fact_check_action("a", "ctx", "q", client=FakeClient(fc=fc)))
    assert out["decision"] == "allow"
    assert out["verdict"] == "verified"
    assert out["relevance"]["verdict"] == "off_topic"


def test_min_relevance_score_escalates_through_the_action():
    fc = _fc(verdict="verified", action="allow", relevance=_rel(0.2, "off_topic"))
    cfg = RailConfig(thresholds=PolicyThresholds(min_relevance_score=0.7))
    out = run(wauldo_fact_check_action("a", "ctx", "q", client=FakeClient(fc=fc), config=cfg))
    assert out["decision"] == "annotate"
    assert out["verdict"] == "verified"  # factual verdict untouched


def test_min_relevance_refuse_blocks_off_topic_but_true():
    fc = _fc(verdict="verified", action="allow", relevance=_rel(0.2, "off_topic"))
    cfg = RailConfig(
        thresholds=PolicyThresholds(min_relevance_score=0.7, on_low_relevance=RailDecision.REFUSE)
    )
    out = run(wauldo_fact_check_action("a", "ctx", "q", client=FakeClient(fc=fc), config=cfg))
    assert out["decision"] == "refuse"


def test_floor_with_relevance_disabled_annotates_and_says_why():
    # min_relevance_score > 0 + relevance_mode=None: the gate can never pass,
    # so every call ANNOTATEs — the note must make that diagnosable.
    cfg = RailConfig(relevance_mode=None, thresholds=PolicyThresholds(min_relevance_score=0.7))
    out = run(wauldo_fact_check_action("a", "ctx", client=FakeClient(fc=_fc()), config=cfg))
    assert out["decision"] == "annotate"
    assert out["note"] == "relevance_floor_set_but_disabled"


def test_floor_with_no_query_annotates_with_unavailable_note():
    # Floor armed, relevance enabled, but no query resolvable → score missing.
    cfg = RailConfig(thresholds=PolicyThresholds(min_relevance_score=0.7))
    out = run(wauldo_fact_check_action("a", "ctx", client=FakeClient(fc=_fc()), config=cfg))
    assert out["decision"] == "annotate"
    assert out["note"] == "relevance_floor_set_but_unavailable"


def test_relevance_warning_passes_through():
    fc = _fc()
    fc = fc.model_copy(update={"relevance_warning": "embeddings_unavailable"})
    out = run(wauldo_fact_check_action("a", "ctx", "q", client=FakeClient(fc=fc)))
    assert out["relevance"] is None
    assert out["relevance_warning"] == "embeddings_unavailable"


def test_degraded_payload_carries_relevance_key():
    out = run(wauldo_fact_check_action("answer", None, client=FakeClient()))
    assert out["relevance"] is None  # shape stable for Colang access


def test_action_result_exposes_wauldo_relevance_context_update():
    import pytest

    pytest.importorskip("nemoguardrails")
    from wauldo_nemo.actions import _action_result

    fc = _fc(relevance=_rel(0.91))
    payload = run(wauldo_fact_check_action("a", "ctx", "q", client=FakeClient(fc=fc)))
    result = _action_result(payload)
    assert result.context_updates["wauldo_relevance"]["score"] == 0.91
    assert result.context_updates["wauldo_relevance"]["verdict"] == "relevant"


# ── explainability: refuse_template ──────────────────────────────────────


def test_refuse_template_renders_from_failed_claim():
    claims = [_claim(False, evidence="source says 2010", reason="numerical_mismatch")]
    fc = _fc(verdict="rejected", action="block", halluc=1.0, claims=claims)
    cfg = RailConfig(refuse_template="Can't confirm '{first_failed_claim}' — source: {evidence}")
    out = run(wauldo_fact_check_action("Rust 1999", "ctx", client=FakeClient(fc=fc), config=cfg))
    assert out["decision"] == "refuse"
    assert out["refuse_message"] == "Can't confirm 'Rust released 2010' — source: source says 2010"


def test_bad_refuse_template_does_not_crash():
    claims = [_claim(False)]
    fc = _fc(verdict="rejected", action="block", halluc=1.0, claims=claims)
    cfg = RailConfig(refuse_template="broken {unknown_placeholder}")
    out = run(wauldo_fact_check_action("x", "ctx", client=FakeClient(fc=fc), config=cfg))
    assert out["decision"] == "refuse"  # generation not crashed
    assert out["refuse_message"] is None  # bad template skipped
