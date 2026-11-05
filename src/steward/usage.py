"""What each model call cost, in tokens, dollars and seconds.

The gate makes one judge call per proposed write (plus one drafter call for a
drafted description), so the price of governance is the price of these calls.
Every call can record a CallStats; the eval reports cost per judgement and
latency percentiles, and the CLI writes each call's stats into the ledger next
to the verdict it paid for.

Cost comes from the response's own usage block, never from an estimate:

  input_tokens                  uncached input, at the input rate
  cache_creation_input_tokens   cache writes: 1.25x input (5-minute TTL), 2x (1-hour)
  cache_read_input_tokens       cache reads, at the model's cache-read rate
  output_tokens                 output, at the output rate -- thinking tokens included

A model missing from PRICES, or a response served at another speed or service
tier (fast mode, priority, batch), gets cost None ("unpriced"), not a guess. A call
that raised (an API error, an unparseable verdict) is still recorded, as
unmeasured: its wall time, but no usage, because none came back.

Latency is end-to-end wall time for the call, including the SDK's own retries.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

PER_MILLION = 1_000_000
# Anthropic first-party API list prices, USD per million tokens:
# (input, output, cache read). As of 2026-10-06; check the pricing page before
# trusting a dollar figure from a later date.
PRICES_AS_OF = "2026-10-06"
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
}
CACHE_WRITE_5M = 1.25  # x the input rate
CACHE_WRITE_1H = 2.0


STANDARD_TIER = "standard"


@dataclass(frozen=True)
class CallStats:
    model: str
    latency_s: float
    input_tokens: int = 0  # uncached input only, as the API reports it
    output_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_read_tokens: int = 0
    measured: bool = True  # False: the call raised, so no usage came back
    tier: str = STANDARD_TIER  # "fast", "priority", "batch": priced differently

    @property
    def prompt_tokens(self) -> int:
        """Every input token: uncached, cache writes and cache reads."""
        return (self.input_tokens + self.cache_write_5m_tokens + self.cache_write_1h_tokens
                + self.cache_read_tokens)

    @property
    def cost_usd(self) -> float | None:
        prices = PRICES.get(self.model)
        if prices is None or not self.measured or self.tier != STANDARD_TIER:
            return None
        input_rate, output_rate, cache_read_rate = prices
        return (self.input_tokens * input_rate
                + self.cache_write_5m_tokens * input_rate * CACHE_WRITE_5M
                + self.cache_write_1h_tokens * input_rate * CACHE_WRITE_1H
                + self.cache_read_tokens * cache_read_rate
                + self.output_tokens * output_rate) / PER_MILLION

    def to_dict(self) -> dict:
        return {"model": self.model, "latency_s": round(self.latency_s, 3),
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_write_5m_tokens": self.cache_write_5m_tokens,
                "cache_write_1h_tokens": self.cache_write_1h_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "measured": self.measured, "tier": self.tier,
                "cost_usd": None if self.cost_usd is None else round(self.cost_usd, 6)}


def _count(obj: Any, name: str) -> int:
    value = getattr(obj, name, None)
    return value if isinstance(value, int) else 0


def call_stats(response: Any, model: str, latency_s: float) -> CallStats:
    """CallStats from a Messages API response. The model is the one that served
    the response when the response says so (a fallback can change it)."""
    usage = getattr(response, "usage", None)
    served = getattr(response, "model", None)
    # The total is authoritative; the breakdown says how much of it was 1-hour.
    # Without one, every write was at the default 5-minute TTL.
    total = _count(usage, "cache_creation_input_tokens")
    write_1h = min(_count(getattr(usage, "cache_creation", None), "ephemeral_1h_input_tokens"), total)
    write_5m = total - write_1h
    speed, service_tier = getattr(usage, "speed", None), getattr(usage, "service_tier", None)
    tier = ("fast" if speed == "fast"
            else service_tier if isinstance(service_tier, str) else STANDARD_TIER)
    return CallStats(
        model=served if isinstance(served, str) else model,
        latency_s=latency_s,
        input_tokens=_count(usage, "input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
        cache_read_tokens=_count(usage, "cache_read_input_tokens"),
        tier=tier,
    )


def _percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with at least q of the data at or below it."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 1))  # ceil(n * q), at least 1
    return ordered[int(rank) - 1]


def summarize(stats: list[CallStats]) -> dict:
    """Totals and per-call figures for a run. Cost covers priced calls only, and
    says how many calls it leaves out (unpriced, or failed with no usage), so a
    partial figure is never read as the total. Latency covers every call."""
    if not stats:
        return {"calls": 0}
    priced = [s.cost_usd for s in stats if s.cost_usd is not None]
    unmeasured = sum(not s.measured for s in stats)
    latencies = [s.latency_s for s in stats]
    return {
        "calls": len(stats),
        "unmeasured_calls": unmeasured,
        "unpriced_calls": len(stats) - len(priced) - unmeasured,
        "total_cost_usd": round(sum(priced), 6) if priced else None,  # display rounds further
        "mean_cost_usd": round(statistics.mean(priced), 6) if priced else None,
        "prompt_tokens": sum(s.prompt_tokens for s in stats),
        "output_tokens": sum(s.output_tokens for s in stats),
        "latency_p50_s": round(_percentile(latencies, 0.50), 2),
        "latency_p95_s": round(_percentile(latencies, 0.95), 2),
    }


def format_summary(summary: dict) -> str:
    """One line for a run's model spend."""
    if not summary.get("calls"):
        return "model calls      0"
    cost = ("no cost" if summary["total_cost_usd"] is None
            else f"${summary['total_cost_usd']:.4f} total, ${summary['mean_cost_usd']:.4f} per priced call")
    left_out = [f"{summary[key]} {label}" for key, label in (
        ("unpriced_calls", "unpriced"), ("unmeasured_calls", "failed with no usage returned"))
        if summary[key]]
    excluded = f" ({', '.join(left_out)}: not in the cost)" if left_out else ""
    return (f"model calls      {summary['calls']}: {cost}{excluded}; "
            f"{summary['prompt_tokens']:,} prompt / {summary['output_tokens']:,} output tokens; "
            f"latency p50 {summary['latency_p50_s']}s, p95 {summary['latency_p95_s']}s")
