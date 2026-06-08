"""Operational configuration for the rails — separate from the verdict
mapping (`policy.PolicyThresholds`), which only decides how a *successful*
fact-check maps to a decision.

`RailConfig` covers the operational concerns a production guardrail must
answer explicitly:

- **What if there's no ground-truth context?** You can't fact-check against
  nothing. Default: annotate ("unverified"), never crash.
- **What if Wauldo is unreachable?** A guardrail must fail *deliberately*.
  Default: fail-open (let the answer through, flagged) so a verification
  outage doesn't take the whole bot down. Set `on_error=RailDecision.REFUSE`
  for fail-closed.
- **Timeout** on the verification round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .policy import PolicyThresholds, RailDecision


@dataclass(frozen=True)
class RailConfig:
    """Operational config for the Wauldo rails."""

    thresholds: PolicyThresholds = field(default_factory=PolicyThresholds)
    mode: str = "lexical"

    #: No context to verify against → can't fact-check. Default: annotate.
    on_missing_context: RailDecision = RailDecision.ANNOTATE

    #: Wauldo unreachable / errored. Default fail-open (PASS) so a
    #: verification outage doesn't break the bot; REFUSE = fail-closed.
    on_error: RailDecision = RailDecision.PASS

    #: Hard timeout (seconds) on a SINGLE verification attempt.
    timeout: float = 8.0

    #: Number of verification attempts before giving up and applying
    #: ``on_error``. Default 1 (single attempt, fail fast) — a guardrail sits
    #: in the response hot path, so retrying a down/slow Wauldo with backoff
    #: would add that backoff to *every* response before fail-open/closed
    #: kicks in. Raise it only if you'd rather wait than degrade. With the
    #: default, ``timeout`` is the real upper bound on added latency.
    max_retries: int = 1

    #: Backoff base (seconds) between attempts when ``max_retries`` > 1.
    retry_backoff: float = 0.5

    #: Citation rail: minimum ratio of cited sentences before a response is
    #: flagged as under-cited.
    min_citation_ratio: float = 0.5

    #: Citation rail: what to do when citations are insufficient.
    on_insufficient_citations: RailDecision = RailDecision.ANNOTATE
