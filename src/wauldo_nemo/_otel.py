"""Optional OpenTelemetry span around a verification call.

OTel is an *optional* dependency (``pip install 'wauldo-nemo[otel]'``). When it
isn't installed, ``span()`` is a zero-overhead no-op context manager — the rails
never import opentelemetry at module load and never fail if it's absent.

When it is installed, the fact-check shows up as a ``wauldo.fact_check`` span
inside the caller's generation trace (Jaeger / Honeycomb / any OTLP backend),
with the verdict as span attributes.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

try:  # pragma: no cover - import guard, exercised by the [otel] extra
    from opentelemetry import trace as _trace

    _TRACER: Any = _trace.get_tracer("wauldo_nemo")
except Exception:  # noqa: BLE001 - any import failure → disabled, never crash
    _TRACER = None


class _NoopSpan:
    """Matches the subset of the OTel span API we use."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D102
        pass


@contextmanager
def span(name: str) -> Iterator[Any]:
    """Yield an OTel span if opentelemetry is installed, else a no-op object.

    Both branches expose ``set_attribute(key, value)`` so callers don't branch.
    """
    if _TRACER is None:
        yield _NoopSpan()
        return
    with _TRACER.start_as_current_span(name) as otel_span:  # pragma: no cover
        yield otel_span
