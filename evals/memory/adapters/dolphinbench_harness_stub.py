"""Starter DolphinBench harness — replace memory hooks with horizon.factmem ingestion.

Copy to dolphinbench repo as my_harness.py after `python -m harness.runner init`.
See https://dolphinbench.ai/run/guide/
"""

from __future__ import annotations

# The participant runner imports this module by name from run.yaml.
# Implement: identity(), run_agent(), freeze(), verify_checkpoint(), total_cost_usd(phase).


def identity() -> dict:
    return {"memory": "horizon-factmem-stub", "note": "Replace with real Horizon ingestion before submit."}


async def run_agent(request, tools, call_app):
    raise NotImplementedError("Wire Cursor/OpenAI agent + factmem here")


async def freeze(persona: str) -> dict:
    raise NotImplementedError("Persist factmem snapshot per persona")


async def verify_checkpoint(persona: str, checkpoint: dict) -> None:
    raise NotImplementedError("Verify read-only memory at test time")


def total_cost_usd(phase: str) -> float:
    return 0.0
