"""Cost model — cache-aware USD from a `Usage` + `Price`, shared by eval + history.

Lifted out of `eval/runner.py` so the eval leaderboard and the history record
price a completion the same way. `cost` returns the total; `cost_breakdown`
returns the four per-rate buckets it sums — **the four buckets sum to `cost`**.
"""

from __future__ import annotations

from tinytalk.config import Price
from tinytalk.provider.base import Usage


def cost_breakdown(usage: Usage, price: Price) -> dict[str, float]:
    """Per-rate USD buckets — `fresh`/`cached`/`write` input + `output`.

    Cached and cache-write tokens are subsets of `prompt_tokens` billed at their
    own rates; those rates fall back to the plain input rate when unset. The four
    values sum to `cost(usage, price)`.
    """
    cached_rate = price.cached_input_per_mtok or price.input_per_mtok
    write_rate = price.cache_write_per_mtok or price.input_per_mtok
    fresh = max(usage.prompt_tokens - usage.cached_prompt_tokens - usage.cache_write_tokens, 0)
    return {
        "fresh": fresh * price.input_per_mtok / 1e6,
        "cached": usage.cached_prompt_tokens * cached_rate / 1e6,
        "write": usage.cache_write_tokens * write_rate / 1e6,
        "output": usage.completion_tokens * price.output_per_mtok / 1e6,
    }


def cost(usage: Usage, price: Price) -> float:
    """Cache-aware cost; cached/cache-write rates fall back to the input rate when unset."""
    return round(sum(cost_breakdown(usage, price).values()), 6)
