# Changelog

All notable changes to wauldo-nemo.

## [0.3.0] - 2026-06-09

Production observability + Ops-rollout features. No breaking changes; all new
fields default to the previous behaviour.

### Added
- **Shadow / audit mode** (`RailConfig.shadow`): call Wauldo and log the verdict
  but never block — `decision` is forced to `allow` while the real verdict stays
  in the payload (`shadowed: True`). Roll out on real traffic before enforcing.
- **Structured logging**: every decision emits one `logging` line with
  `extra={"wauldo": {...}}` (request_id, decision, verdict, hallucination_rate,
  latency_ms, shadowed, note) — formatter-agnostic, no forced JSON.
- **`request_id` + `latency_ms`** in every returned payload, to correlate a NeMo
  turn with its Wauldo log line.
- **OpenTelemetry** (optional `[otel]` extra): the verify call becomes a
  `wauldo.fact_check` span with verdict attributes. No-op when not installed.
- **Evidence in context**: the registered rail returns a NeMo `ActionResult`, so
  downstream rails / `$history` / UIs can read `$wauldo_evidence`,
  `$wauldo_verdict`, `$wauldo_decision`, `$wauldo_request_id`.
- **`RailConfig.refuse_template`**: optional refusal message rendered from the
  failed claim (`{first_failed_claim}` / `{evidence}` / `{verdict}`).
- `benchmarks/overhead.py`: measures the adapter's local overhead (~0.06 ms p50).

### Changed
- CI now runs `mypy --strict` (with `ruff`) and the package is fully typed under
  strict mode.

## [0.2.1] - 2026-06-08

### Fixed
- **Broken fresh install**: the dependency was `wauldo[async]`, but that extra
  does not exist on the SDK (it never pulled aiohttp). A clean
  `pip install wauldo-nemo` therefore shipped without aiohttp and the async
  rails `ImportError`ed on the first real call. Now depends on `aiohttp>=3.9`
  directly.

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
- **Fail-fast retries** (`RailConfig.max_retries`, default `1`): the rail sits in
  the response hot path, so the SDK's default 3-retry backoff would add ~3s to
  *every* response during a Wauldo outage before `on_error` fired. The default is
  now a single attempt — `timeout` is the real upper bound on added latency.
  Outage-added latency measured 3.15s → 0.01s. Raise `max_retries` to trade
  latency for resilience.
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
