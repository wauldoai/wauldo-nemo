# Changelog

All notable changes to wauldo-nemo.

## [0.1.0] - 2026-06-07

### Added
- `wauldo_fact_check_action` — async NeMo Guardrails action that verifies a bot
  message against its retrieved context via `wauldo.HttpClient.fact_check()` and
  returns a rail decision (`allow` / `annotate` / `refuse`).
- `register(rails, thresholds=...)` — wire the action onto an `LLMRails` instance
  under the name `wauldo_fact_check`.
- `policy.decide()` + `PolicyThresholds` — framework-agnostic mapping from a
  fact-check result to a `RailDecision`. Honesty invariant: thresholds only
  escalate the server's verdict, never downgrade it.
- Runnable example output rail under `examples/config/` (`config.yml` + `rails.co`).

> Requires `wauldo>=0.19` (first SDK version exposing `HttpClient.fact_check`).
