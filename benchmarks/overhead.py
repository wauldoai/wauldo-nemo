"""Measure the rail's LOCAL overhead — everything wauldo-nemo does around the
verification API call (context resolve + policy decide + payload build + shadow
+ structured log), with a zero-network fake client. This is the number an
engineer asks for before adding a dependency: what does the adapter itself cost,
excluding the API round-trip.

    python benchmarks/overhead.py [N]
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

from wauldo import ClaimResult, FactCheckResponse

from wauldo_nemo import RailConfig, wauldo_fact_check_action

CTX = "Rust was first released in 2010 by Mozilla Research."


class _FakeClient:
    """Returns a fixed verdict instantly — isolates local overhead from I/O."""

    async def fact_check(self, text, source_context, mode="lexical"):
        return FactCheckResponse(
            verdict="rejected",
            action="block",
            hallucination_rate=1.0,
            mode="lexical",
            total_claims=1,
            supported_claims=0,
            confidence=0.3,
            claims=[
                ClaimResult(
                    text=text,
                    claim_type="Fact",
                    supported=False,
                    confidence=0.3,
                    confidence_label="very_low",
                    verdict="rejected",
                    action="block",
                    reason="numerical_mismatch",
                    evidence=CTX,
                )
            ],
            processing_time_ms=0,
        )

    async def close(self):
        pass


async def _run(n: int) -> list[float]:
    client = _FakeClient()
    cfg = RailConfig()
    # warmup
    for _ in range(50):
        await wauldo_fact_check_action("Rust shipped in 1999.", CTX, client=client, config=cfg)
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        await wauldo_fact_check_action("Rust shipped in 1999.", CTX, client=client, config=cfg)
        samples.append((time.perf_counter() - t0) * 1e6)  # µs
    return samples


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    s = sorted(asyncio.run(_run(n)))
    p = lambda q: s[min(len(s) - 1, int(q * len(s)))]  # noqa: E731
    print(f"wauldo-nemo local overhead (n={n}, fake client, no network):")
    print(f"  mean {statistics.mean(s):6.1f} µs")
    print(f"  p50  {p(0.50):6.1f} µs")
    print(f"  p95  {p(0.95):6.1f} µs")
    print(f"  p99  {p(0.99):6.1f} µs")
    print("→ this is the adapter cost; the Wauldo API round-trip is separate.")


if __name__ == "__main__":
    main()
