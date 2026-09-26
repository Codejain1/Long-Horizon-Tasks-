"""Models for the harness: a scripted mock for dry runs, and litellm priced from our config.

Both put normalised token usage in ``message["extra"]["usage"]`` and the cost (from the
config prices) in ``message["extra"]["cost"]``, which mini-SWE-agent's cost cap reads.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Any

from horizon.bench.config import Prices

SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git diff --cached"


@dataclass
class Usage:
    input: int = 0  # uncached input tokens
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def cost(self, prices: Prices) -> float:
        return (self.input * prices.input + self.output * prices.output + self.cache_write * prices.cache_write
                + self.cache_read * prices.cache_read) / 1e6

    def __add__(self, other: Usage) -> Usage:
        return Usage(*(a + b for a, b in zip(asdict(self).values(), asdict(other).values())))


def usage_from_litellm(usage: dict[str, Any] | None) -> Usage:
    """litellm's Anthropic usage: prompt_tokens includes cache reads and writes, which are also
    reported separately. Uncached input = prompt - cache_read - cache_write (floored at 0)."""
    usage = usage or {}
    cache_read = int(usage.get("cache_read_input_tokens")
                     or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    return Usage(input=max(0, prompt - cache_read - cache_write), output=int(usage.get("completion_tokens") or 0),
                 cache_write=cache_write, cache_read=cache_read)


def message_usage(message: dict) -> Usage:
    u = (message.get("extra") or {}).get("usage")
    return Usage(**u) if u else Usage()


# --- dry run -----------------------------------------------------------------------------------

def mock_behaviour(instance_id: str) -> str:
    """Deterministic per task: most tasks "solve", every 4th keeps exploring until a cap stops it."""
    return "loop" if int(hashlib.sha256(instance_id.encode()).hexdigest(), 16) % 4 == 0 else "solve"


class MockModel:
    """Scripted stand-in for claude-sonnet-5. Makes no network calls.

    Token usage imitates a SWE-bench trajectory: a large, growing prompt (mostly cache reads)
    and short outputs, so the cost cap and step cap both get exercised.
    """

    def __init__(self, prices: Prices, behaviour: str = "solve", model_name: str = "mock/claude-sonnet-5"):
        from minisweagent.models.utils.actions_text import format_observation_messages

        self._format = format_observation_messages
        self.prices = prices
        self.behaviour = behaviour
        self.model_name = model_name
        self.n = 0
        self.observation_template = (
            "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>")

    def _command(self) -> str:
        if self.behaviour == "solve":
            script = ["ls", "git status --short", "printf 'dry-run fix\\n' >> README.md", SUBMIT]
            return script[min(self.n - 1, len(script) - 1)]
        return "ls" if self.n % 2 else "git log --oneline -1"

    def query(self, messages: list[dict], **kwargs) -> dict:
        self.n += 1
        usage = Usage(input=1_500, output=350, cache_write=1_500, cache_read=6_000 + 12_000 * (self.n - 1))
        command = self._command()
        return {
            "role": "assistant",
            "content": f"THOUGHT: dry-run step {self.n}.\n\n```mswea_bash_command\n{command}\n```",
            "extra": {"actions": [{"command": command}], "usage": asdict(usage), "cost": usage.cost(self.prices),
                      "timestamp": time.time()},
        }

    def format_message(self, **kwargs) -> dict:
        return dict(kwargs)

    def format_observation_messages(self, message: dict, outputs: list[dict], template_vars: dict | None = None):
        return self._format(outputs, observation_template=self.observation_template, template_vars=template_vars)

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def serialize(self) -> dict:
        return {"info": {"config": {"model": {"model_name": self.model_name, "behaviour": self.behaviour},
                                    "model_type": "horizon.bench.models.MockModel"}}}


# --- real runs ---------------------------------------------------------------------------------

def make_priced_litellm_model(model_name: str, prices: Prices, base: dict | None = None):
    """mini-SWE-agent's LitellmModel with cost computed from our config prices.

    ``base`` is the ``model`` section of mini-SWE-agent's swebench.yaml (templates, model_kwargs).

    Imported lazily: dry runs never import litellm or touch the network.
    """
    from minisweagent.models.litellm_model import LitellmModel

    class PricedLitellmModel(LitellmModel):
        def _calculate_cost(self, response) -> dict[str, Any]:
            usage = usage_from_litellm(response.model_dump().get("usage"))
            out: dict[str, Any] = {"cost": usage.cost(prices), "usage": asdict(usage)}
            try:  # litellm's own figure, kept only as a cross-check
                import litellm

                out["litellm_cost"] = litellm.cost_calculator.completion_cost(response, model=model_name)
            except Exception:
                out["litellm_cost"] = None
            return out

    kwargs = {k: v for k, v in (base or {}).items() if k not in ("model_name", "model_class")}
    return PricedLitellmModel(**{"set_cache_control": "default_end", **kwargs, "model_name": model_name,
                                 "cost_tracking": "ignore_errors"})
