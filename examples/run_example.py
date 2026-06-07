"""Run the Wauldo rails inside NeMo Guardrails against the live API.

⚠️ Wauldo is the OUTPUT RAIL, not the main generation LLM (its guard rejects
NeMo's dialog meta-prompts with 403 security_violation). Keep a standard
provider as `main`.

`verify_only()` drives the rails through NeMo's real runtime (action
dispatcher) on fixed inputs — needs only `WAULDO_API_KEY`, no generation LLM.
`full_pipeline()` is the production shape (OpenAI main + Wauldo output rail).

    pip install 'wauldo-nemo[nemo]'
    export WAULDO_API_KEY=tig_live_...
    python examples/run_example.py
"""

import asyncio
import os

from nemoguardrails import LLMRails, RailsConfig

from wauldo_nemo import RailConfig, register

CONFIG = "examples/config"
CONTEXT = "Rust was first released in 2010 by Mozilla Research. The capital of France is Paris."


async def verify_only() -> None:
    os.environ.setdefault("OPENAI_API_KEY", "sk-not-used-by-verify-only")
    rails = LLMRails(RailsConfig.from_path(CONFIG))
    register(rails, config=RailConfig())

    cases = [
        ("Rust was first released in 1999 by Google.", "hallucinated"),
        ("Rust was first released in 2010 by Mozilla Research.", "grounded"),
    ]
    for answer, label in cases:
        result, _ = await rails.runtime.action_dispatcher.execute_action(
            "wauldo_fact_check", {"bot_message": answer, "source_context": CONTEXT}
        )
        print(f"[{label}] decision={result['decision']} "
              f"verdict={result['verdict']} halluc={result['hallucination_rate']:.2f}")
        for c in result["claims"]:
            if not c["supported"]:
                print(f"    ✗ {c['text']!r}  evidence={c['evidence']!r} reason={c['reason']}")


async def full_pipeline() -> None:
    rails = LLMRails(RailsConfig.from_path(CONFIG))
    register(rails, config=RailConfig(timeout=8.0))
    response = await rails.generate_async(
        messages=[{"role": "user", "content": "When was Rust first released?"}]
    )
    print("full_pipeline →", response)


async def main() -> None:
    await verify_only()
    key = os.environ.get("OPENAI_API_KEY", "")
    if key.startswith("sk-") and key != "sk-not-used-by-verify-only":
        await full_pipeline()
    else:
        print("(set OPENAI_API_KEY to also run the full generate→gate pipeline)")


if __name__ == "__main__":
    asyncio.run(main())
