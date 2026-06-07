# wauldo-nemo

Wauldo fact-checking as a [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails)
**output rail**. Every bot message is verified against its retrieved context
before it reaches the user; unsupported answers are refused, weakly-supported
ones are annotated.

This is a thin adapter — all verdict logic lives in the
[`wauldo` SDK](https://pypi.org/project/wauldo/). This repo only maps a
`/v1/fact-check` result to a rail decision and never re-implements parsing.

## Install

```bash
pip install wauldo-nemo[nemo]
```

> Requires `wauldo>=0.19` (the version that exposes `HttpClient.fact_check`).
> While 0.19 is unreleased, install the SDK from source:
> `pip install -e ../wauldo-monorepo/sdk-python`.

## How it works

```
bot message ──▶ wauldo_fact_check action ──▶ HttpClient.fact_check(text, context)
                                              │
                                              ▼
                                     policy.decide(result, thresholds)
                                              │
                          allow ──▶ pass   review ──▶ annotate   block ──▶ refuse
```

The policy **never downgrades** the server's verdict — thresholds can only make
the rail stricter (e.g. `strict=True` turns `review` into a refusal,
`max_hallucination_rate` forces a refusal above a ceiling).

## Usage

```python
from nemoguardrails import LLMRails, RailsConfig
from wauldo_nemo import register, PolicyThresholds

rails = LLMRails(RailsConfig.from_path("examples/config"))
register(rails, thresholds=PolicyThresholds(strict=True, max_hallucination_rate=0.4))
```

See [`examples/`](examples/) for a runnable config (`config.yml` + `rails.co`).

## Configuration

| Threshold | Default | Effect |
|-----------|---------|--------|
| `min_confidence` | `0.0` | Below it, an `allow` is downgraded to *annotate*. |
| `max_hallucination_rate` | `1.0` | Above it, the response is *refused*. |
| `strict` | `False` | When set, a server `review` becomes a *refusal*. |

Environment: `WAULDO_API_KEY` (required), `WAULDO_BASE_URL` (defaults to
`https://api.wauldo.com`).

## Develop

```bash
pip install -e '.[dev]'
pytest        # policy tests, no guardrails runtime needed
```

## License

MIT
