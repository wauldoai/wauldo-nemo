# Changelog

All notable changes to wauldo-nemo.

## [0.2.0] - 2026-06-07

### Fixed
- **No-context crash**: with `wauldo>=0.19.1` (where `source_context` is required),
  the rail raised `ValueError` whenever the context was empty (e.g. NeMo
  `$relevant_chunks` unset). It now returns `decision="annotate"` /
  `note="no_context"` via `RailConfig.on_missing_context`, never crashing.
- **Event-loop blocking**: the action used the *sync* SDK client inside an async
  action, blocking NeMo's loop for the whole round-trip. Now uses the **async**
  client (`AsyncHttpClient`), and reuses a single client per `register()`.

### Added
- **Failure policy** (`RailConfig.on_error`): fail-open (default, flag and pass) or
  fail-closed (`RailDecision.REFUSE`) when Wauldo is unreachable.
- **Per-claim evidence**: the fact-check action now returns `claims[]` (each with
  `verdict` / `evidence` / `reason`), not just counts.
- **Citation rail** `wauldo_verify_citations` (`POST /v1/verify`) — flags
  under-cited answers. Enable with `register(rails, verify_citations=True)`.
- `RailConfig` — operational config (mode, missing-context, fail-open/closed,
  timeout, citation thresholds). `register(..., config=...)`; `thresholds=` still
  accepted for the common case.

### Changed
- Dependency bumped to `wauldo[async]>=0.19.1` (pulls aiohttp).
- `register()` now takes `config` / `verify_citations`; the action signatures take
  `config` (the `thresholds=` shortcut still works).

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
