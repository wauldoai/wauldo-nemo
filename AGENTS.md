# AGENTS.md — wauldo-nemo

> Contributor & coding-agent guide (vendor-neutral; read by AGENTS.md-aware tools).

**Langue** : échanges en français. Code + commits en anglais.

## What this is

Thin adapter exposing Wauldo fact-checking as a NeMo Guardrails **output rail**.
Depends on the published `wauldo` SDK (`pip install wauldo`) — it does **not**
copy-paste any Wauldo core logic. All verdict parsing lives in the SDK.

## Architecture (keep this boundary)

- `policy.py` — framework-agnostic. Maps a fact-check result to a `RailDecision`.
  **No `nemoguardrails` import here.** Honesty rule: thresholds may only
  *escalate* severity, never downgrade the server's verdict (never turn a
  `block` into a pass).
- `actions.py` — the NeMo glue. Calls `wauldo.HttpClient.fact_check()` then
  `policy.decide()`. Imports the SDK; the `register()` helper wires it onto an
  `LLMRails` instance.

## Template intent (rule of three)

This repo is the **first** Wauldo × framework integration. It is meant to be a
template for LangChain / LlamaIndex adapters, but do **not** abstract a shared
"integration base" until the **second** integration exists. Replicate, don't
abstract, until then.

## Test

```bash
pip install -e '.[dev]' && pytest    # policy tests, no guardrails runtime
```

## Do not

- Re-implement verdict/claim parsing here — extend the SDK instead.
- Position Wauldo as an orchestrator. It's a verification layer; NeMo owns the
  flow control.
- Claim "deterministic" — the pipeline uses LLMs. The honest differentiator is
  claim-level granularity + evidence mapping + the verdict/action it returns.
