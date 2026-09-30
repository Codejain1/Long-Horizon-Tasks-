"""Domain adapters: each domain's meaning (state, risk, outcomes) for Horizon's general core."""

from horizon.adapters.base import DomainAdapter, Outcome
from horizon.adapters.coding import CodingAdapter

ADAPTERS: dict[str, DomainAdapter] = {"coding": CodingAdapter()}


def get_adapter(domain: str) -> DomainAdapter:
    try:
        return ADAPTERS[domain]
    except KeyError:
        raise ValueError(f"no adapter for domain {domain!r}; known: {sorted(ADAPTERS)}") from None


__all__ = ["ADAPTERS", "CodingAdapter", "DomainAdapter", "Outcome", "get_adapter"]
