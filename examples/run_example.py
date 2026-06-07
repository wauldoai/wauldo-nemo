"""Minimal end-to-end example: wire the Wauldo output rail into NeMo.

Requires the `nemo` extra and a running Wauldo API key:

    pip install -e '.[nemo,dev]'
    export WAULDO_API_KEY=tig_live_...
    export OPENAI_API_KEY=sk-...
    python examples/run_example.py
"""

import asyncio

from nemoguardrails import LLMRails, RailsConfig

from wauldo_nemo import PolicyThresholds, register


async def main() -> None:
    config = RailsConfig.from_path("examples/config")
    rails = LLMRails(config)

    # Tighten the rail: refuse anything the server only marks "review",
    # and refuse when the hallucination rate exceeds 0.4.
    register(rails, thresholds=PolicyThresholds(strict=True, max_hallucination_rate=0.4))

    response = await rails.generate_async(
        messages=[{"role": "user", "content": "When was Rust first released?"}]
    )
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
