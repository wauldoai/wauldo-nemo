"""Two ways to run the Wauldo output rail inside NeMo Guardrails.

⚠️ Design rule — Wauldo is the OUTPUT RAIL (verification), never the main
generation LLM. Wauldo is a *guarded* product: its prompt-injection classifier
rejects NeMo's internal Colang dialog meta-prompts (e.g. `generate_user_intent`)
with `403 security_violation`. So keep a standard generation model as `main`
(OpenAI/Anthropic/local) and let Wauldo gate its output.

1. `verify_only()` — drives the rail through NeMo's real runtime (action
   dispatcher) on a fixed (answer, context). Needs only `WAULDO_API_KEY`; no
   generation LLM. This is the fastest way to confirm the integration.

2. `full_pipeline()` — the production shape: a real `generate_async()` loop with
   OpenAI as `main` and Wauldo as the output rail. Needs `OPENAI_API_KEY`, and a
   context source for the rail (a NeMo knowledge base populating
   `$relevant_chunks`, or your retriever wired into that variable).

    pip install 'wauldo-nemo[nemo]'
    export WAULDO_API_KEY=tig_live_...
    python examples/run_example.py            # runs verify_only
    OPENAI_API_KEY=sk-... python examples/run_example.py   # also runs full_pipeline
"""

import asyncio
import os

from nemoguardrails import LLMRails, RailsConfig

from wauldo_nemo import PolicyThresholds, register

CONFIG = "examples/config"
CONTEXT = "Rust was first released in 2010 by Mozilla Research. The capital of France is Paris."


async def verify_only() -> None:
    """Run the rail against the live Wauldo API without any generation LLM."""
    # LLMRails loads config.yml (main: openai) but never calls it here; a dummy
    # key just lets the client construct. The dispatcher path below is the only
    # thing that runs, and it talks to Wauldo, not OpenAI.
    os.environ.setdefault("OPENAI_API_KEY", "sk-not-used-by-verify-only")

    rails = LLMRails(RailsConfig.from_path(CONFIG))
    register(rails, thresholds=PolicyThresholds())

    cases = [
        ("Rust was first released in 1999 by Google.", "hallucinated"),
        ("Rust was first released in 2010 by Mozilla Research.", "grounded"),
    ]
    for answer, label in cases:
        result, status = await rails.runtime.action_dispatcher.execute_action(
            "wauldo_fact_check", {"bot_message": answer, "context": CONTEXT}
        )
        print(f"[{label}] decision={result['decision']} "
              f"verdict={result['verdict']} halluc={result['hallucination_rate']:.2f}")


async def full_pipeline() -> None:
    """Production shape: OpenAI generates, Wauldo gates the output."""
    rails = LLMRails(RailsConfig.from_path(CONFIG))
    register(rails, thresholds=PolicyThresholds(strict=True, max_hallucination_rate=0.4))

    response = await rails.generate_async(
        messages=[{"role": "user", "content": "When was Rust first released?"}]
    )
    print("full_pipeline →", response)


async def main() -> None:
    await verify_only()
    if os.environ.get("OPENAI_API_KEY", "").startswith("sk-") and \
            os.environ["OPENAI_API_KEY"] != "sk-not-used-by-verify-only":
        await full_pipeline()
    else:
        print("(set OPENAI_API_KEY to also run the full generate→gate pipeline)")


if __name__ == "__main__":
    asyncio.run(main())
