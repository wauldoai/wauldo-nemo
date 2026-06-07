"""wauldo-nemo — Wauldo fact-checking as a NeMo Guardrails output rail.

Public surface:
- `wauldo_fact_check_action` — the async action (verify + decide).
- `register` — register it on an `LLMRails` instance.
- `RailDecision`, `PolicyThresholds`, `decide` — the framework-agnostic policy.
"""

from .actions import ALLOW, ANNOTATE, REFUSE, register, wauldo_fact_check_action
from .policy import PolicyThresholds, RailDecision, decide

__version__ = "0.1.0"

__all__ = [
    "wauldo_fact_check_action",
    "register",
    "decide",
    "RailDecision",
    "PolicyThresholds",
    "ALLOW",
    "ANNOTATE",
    "REFUSE",
]
