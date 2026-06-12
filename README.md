# wauldo-nemo

[![PyPI](https://img.shields.io/pypi/v/wauldo-nemo.svg)](https://pypi.org/project/wauldo-nemo/)
[![Python](https://img.shields.io/pypi/pyversions/wauldo-nemo.svg)](https://pypi.org/project/wauldo-nemo/)
[![CI](https://github.com/wauldoai/wauldo-nemo/actions/workflows/publish.yml/badge.svg)](https://github.com/wauldoai/wauldo-nemo/actions/workflows/publish.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Independent answer verification as [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails)
**output rails**, powered by [Wauldo](https://wauldo.com). Every bot message is
checked against its retrieved context **claim-by-claim**: unsupported answers
are refused, weakly-supported ones are annotated — with the **evidence** for
each verdict.

This is a thin adapter. All verdict logic lives in the
[`wauldo` SDK](https://pypi.org/project/wauldo/); this repo only maps a
verification result to a rail decision and never re-implements parsing.

> 📖 **Integration guide & live overview:** <https://wauldo.com/integrations/nemo>

## Why not NeMo's built-in `self check facts`?

NeMo's built-in self-check rail asks the **same** generation LLM *"is this
true?"* — same-model bias, no sources, no granularity. wauldo-nemo calls an
**independent verification service** that returns:

- **claim-level** verdicts (which specific claim failed),
- the **evidence** in your sources behind each verdict,
- a numeric **hallucination rate** and per-claim confidence,
- a fast rule-based `lexical` mode (sub-second) plus `hybrid` / `semantic`.

The policy is **honesty-bound**: thresholds may only make the rail *stricter*,
never override the verification service into a more lenient verdict.

## Use case

A RAG support bot retrieves policy docs and answers *"Can I return this after
60 days?"*. The model says **"Yes, returns are accepted within 60 days."** but
the sources say **14 days**. The output rail fact-checks the answer against the
retrieved context, sees a `numerical_mismatch`, and **refuses** the answer
before the user ever sees it — surfacing the exact claim and the contradicting
evidence to your logs.

```
[refuse] verdict=rejected halluc=1.00
  claim   : "returns are accepted within 60 days"  supported=false
  evidence: "Our return policy: 14 days."  reason=numerical_mismatch
```

## Install

```bash
pip install 'wauldo-nemo[nemo]'
export WAULDO_API_KEY=tig_live_...
```

> Pulls `wauldo>=0.20.0` and `aiohttp` automatically (the rails use the async
> client so they never block NeMo's event loop).

## ⚠️ Wauldo is the output rail, not the main LLM

Keep your generation model (`type: main`) a standard provider — OpenAI,
Anthropic, a local model. **Do not set Wauldo as the main LLM.** Wauldo is a
*guarded* API: its prompt-injection classifier rejects NeMo's internal Colang
dialog meta-prompts with `403 security_violation`. Wauldo's role is
verification (the output rail), not generation.

## How it works

```
bot message ──▶ wauldo_fact_check action ──▶ AsyncHttpClient.fact_check(text, context)
                                              │
                                              ▼
                                     policy.decide(result, thresholds)
                                              │
                          allow ──▶ pass   review ──▶ annotate   block ──▶ refuse
```

Two rails are available:

| Rail | Action | Checks |
|------|--------|--------|
| Fact-check | `wauldo_fact_check` | claims are supported by the context |
| Citations | `wauldo_verify_citations` | the answer actually cites its sources |

## Quickstart — verify against the live API (no generation LLM)

The fastest way to confirm the rail works, driven through NeMo's real runtime:

```bash
export WAULDO_API_KEY=tig_live_...
python examples/run_example.py
# [hallucinated] decision=refuse verdict=rejected halluc=1.00
# [grounded]     decision=allow  verdict=verified halluc=0.00
```

## Production usage

```python
from nemoguardrails import LLMRails, RailsConfig
from wauldo_nemo import register, RailConfig, PolicyThresholds, RailDecision

rails = LLMRails(RailsConfig.from_path("examples/config"))  # main: openai etc.
register(
    rails,
    config=RailConfig(
        thresholds=PolicyThresholds(strict=True, max_hallucination_rate=0.4),
        on_missing_context=RailDecision.ANNOTATE,  # no context → can't verify
        on_error=RailDecision.PASS,                # Wauldo outage → fail-open
        timeout=8.0,
    ),
    verify_citations=True,  # also enable the citation rail
)
```

## Relevance gating — catch off-topic-but-true answers

Factuality and relevance are different failure modes: an answer can be fully
verified against the sources AND not answer the question that was asked. Since
0.4.0 the fact-check rail also sends the user's question (auto-read from NeMo's
`$last_user_message`, or passed explicitly as `query=`) and gets back a
`relevance` block — `{score, verdict}` with verdict `relevant` / `partial` /
`off_topic` — **decoupled from the factual verdict**.

By default relevance is informational only (it never changes the decision).
Opt in to gate on it:

```python
register(
    rails,
    thresholds=PolicyThresholds(
        min_relevance_score=0.85,             # below → escalate
        on_low_relevance=RailDecision.REFUSE,  # block off-topic-but-true
    ),
)
```

Escalate-only, like every other knob: relevance can never soften a factual
block. If the floor is set but relevance couldn't be computed, the response is
annotated with a `note` saying why — the gate is never silently skipped.
Disable relevance entirely with `RailConfig(relevance_mode=None)` — but don't
combine that with a floor: an armed `min_relevance_score` can never pass
without a score, so every call would annotate
(`note="relevance_floor_set_but_disabled"`). Downstream rails can read
`$wauldo_relevance`.

## Configuration (`RailConfig`)

| Field | Default | Effect |
|-------|---------|--------|
| `thresholds.min_confidence` | `0.0` | Below it, an `allow` is downgraded to *annotate*. |
| `thresholds.max_hallucination_rate` | `1.0` | Above it, the response is *refused*. |
| `thresholds.min_relevance_score` | `0.0` | Relevance floor (inert at `0.0`). Below it, the decision escalates to `on_low_relevance`. |
| `thresholds.on_low_relevance` | `ANNOTATE` | Escalation for low relevance; `REFUSE` blocks off-topic-but-true answers. |
| `thresholds.strict` | `False` | A server `review` becomes a *refusal*. |
| `mode` | `"lexical"` | `lexical` (fast) / `hybrid` / `semantic`. |
| `relevance_mode` | `"fast"` | Relevance computation (server-side embedding cosine, no LLM cost). `None` disables relevance. |
| `on_missing_context` | `ANNOTATE` | No context to verify against → can't fact-check. |
| `on_error` | `PASS` | Wauldo unreachable → **fail-open** (flag) vs `REFUSE` (fail-closed). |
| `timeout` | `8.0` | Timeout (seconds) on a **single** verification attempt. |
| `max_retries` | `1` | Attempts before applying `on_error`. Default `1` = fail fast: a guardrail is in the hot path, so retrying a down Wauldo with backoff would add latency to every response. With the default, `timeout` is the real latency bound. |
| `min_citation_ratio` | `0.5` | Citation rail: minimum cited-sentence ratio. |
| `on_insufficient_citations` | `ANNOTATE` | Citation rail decision when under-cited. |
| `shadow` | `False` | **Audit mode**: call Wauldo, log the verdict, but never block (decision forced to `allow`). Roll out on real traffic before enforcing. |
| `refuse_template` | `None` | Optional refusal message rendered from the failed claim. Placeholders: `{first_failed_claim}` / `{evidence}` / `{verdict}`. |

Environment: `WAULDO_API_KEY` (required), `WAULDO_BASE_URL` (defaults to
`https://api.wauldo.com`).

## Production (observability, shadow mode, overhead)

**Shadow / audit mode** — roll the rail out on live traffic without blocking
anyone. It calls Wauldo and logs the real verdict, but forces `decision` to
`allow`; the payload still carries the true `verdict` / `hallucination_rate` /
`claims` plus `shadowed: True`. Flip `RailConfig(shadow=True)` → measure → then
enforce.

**Structured logging** — every decision emits one log line via
`logging.getLogger("wauldo_nemo")` with `extra={"wauldo": {...}}` (request_id,
decision, verdict, hallucination_rate, latency_ms, shadowed, note). It's
formatter-agnostic — plain logging, structlog, or your JSON formatter all pick
it up. The `request_id` is also in the returned payload, so you can correlate a
NeMo turn with its Wauldo log line.

**OpenTelemetry** (optional) — `pip install 'wauldo-nemo[otel]'` and the verify
call shows up as a `wauldo.fact_check` span (decision / verdict /
hallucination_rate / request_id attributes) inside your generation trace in
Jaeger / Honeycomb / any OTLP backend. No-op with zero overhead when the extra
isn't installed.

**Evidence in context** — the registered rail returns a NeMo `ActionResult`, so
downstream rails / `$history` / your UI can read `$wauldo_evidence`,
`$wauldo_verdict`, `$wauldo_decision`, `$wauldo_relevance`, `$wauldo_request_id`.

**Overhead** — the adapter's own work (context resolve + policy + logging) is
**≈0.06 ms p50** (`python benchmarks/overhead.py`); the Wauldo API round-trip
dominates, and `lexical` mode is sub-second.

## Troubleshooting

- **`403 security_violation` during generation** — you set Wauldo as the `main`
  model. Use a standard provider for generation; Wauldo is the output rail.
- **Answers always `annotate` with `note=no_context`** — the rail received no
  context. Pass `source_context=$relevant_chunks` in the flow, or set your
  retriever's `$relevant_chunks` (the action auto-reads it). NB: the argument
  is `source_context`, not `context` — `context` is reserved by NeMo.

## Develop

```bash
pip install -e '.[dev]'
pytest        # policy + action tests, no guardrails runtime needed
```

## License

MIT
