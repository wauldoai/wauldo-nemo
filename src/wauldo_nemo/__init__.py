"""wauldo-nemo — Wauldo verification as NeMo Guardrails rails.

Public surface:
- `register` — register the rails on an `LLMRails` instance.
- `wauldo_fact_check_action` / `wauldo_verify_citations_action` — the async actions.
- `RailConfig` — operational config (missing-context, fail-open/closed, timeout).
- `RailDecision`, `PolicyThresholds`, `decide` — the framework-agnostic policy.
"""

from .actions import (
    ALLOW,
    ANNOTATE,
    REFUSE,
    register,
    wauldo_fact_check_action,
    wauldo_verify_citations_action,
)
from .config import RailConfig
from .policy import PolicyThresholds, RailDecision, decide

__version__ = "0.3.0"

__all__ = [
    "register",
    "wauldo_fact_check_action",
    "wauldo_verify_citations_action",
    "RailConfig",
    "decide",
    "RailDecision",
    "PolicyThresholds",
    "ALLOW",
    "ANNOTATE",
    "REFUSE",
]
