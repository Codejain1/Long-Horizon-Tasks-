"""Domain adapters: each domain's meaning (state, risk, outcomes) for Horizon's general core."""

from horizon.adapters.base import DomainAdapter, Outcome
from horizon.adapters.coding import CodingAdapter
from horizon.adapters.runner import RunnerAdapter

ADAPTERS: dict[str, DomainAdapter] = {"coding": CodingAdapter(), "runner": RunnerAdapter()}


def get_adapter(domain: str) -> DomainAdapter:
    try:
        return ADAPTERS[domain]
    except KeyError:
        raise ValueError(f"no adapter for domain {domain!r}; known: {sorted(ADAPTERS)}") from None


__all__ = ["ADAPTERS", "CodingAdapter", "RunnerAdapter", "DomainAdapter", "Outcome", "get_adapter"]
