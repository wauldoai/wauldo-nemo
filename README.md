# wauldo-nemo

[![PyPI](https://img.shields.io/pypi/v/wauldo-nemo.svg)](https://pypi.org/project/wauldo-nemo/)
[![Python](https://img.shields.io/pypi/pyversions/wauldo-nemo.svg)](https://pypi.org/project/wauldo-nemo/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Independent answer verification as [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails)
**output rails**, powered by [Wauldo](https://wauldo.com). Every bot message is
checked against its retrieved context **claim-by-claim**: unsupported answers
are refused, weakly-supported ones are annotated — with the **evidence** for
each verdict.

This is a thin adapter. All verdict logic lives in the
[`wauldo` SDK](https://pypi.org/project/wauldo/); this repo only maps a
verification result to a rail decision and never re-implements parsing.

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

> Pulls `wauldo[async]>=0.19.1` automatically (the rails use the async client
> so they never block NeMo's event loop).

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

## Configuration (`RailConfig`)

| Field | Default | Effect |
|-------|---------|--------|
| `thresholds.min_confidence` | `0.0` | Below it, an `allow` is downgraded to *annotate*. |
| `thresholds.max_hallucination_rate` | `1.0` | Above it, the response is *refused*. |
| `thresholds.strict` | `False` | A server `review` becomes a *refusal*. |
| `mode` | `"lexical"` | `lexical` (fast) / `hybrid` / `semantic`. |
| `on_missing_context` | `ANNOTATE` | No context to verify against → can't fact-check. |
| `on_error` | `PASS` | Wauldo unreachable → **fail-open** (flag) vs `REFUSE` (fail-closed). |
| `timeout` | `8.0` | Timeout (seconds) on a **single** verification attempt. |
| `max_retries` | `1` | Attempts before applying `on_error`. Default `1` = fail fast: a guardrail is in the hot path, so retrying a down Wauldo with backoff would add latency to every response. With the default, `timeout` is the real latency bound. |
| `min_citation_ratio` | `0.5` | Citation rail: minimum cited-sentence ratio. |
| `on_insufficient_citations` | `ANNOTATE` | Citation rail decision when under-cited. |

Environment: `WAULDO_API_KEY` (required), `WAULDO_BASE_URL` (defaults to
`https://api.wauldo.com`).

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
