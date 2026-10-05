"""Cost and usage meters: real OpenAI spend (judge, simulated Maya) and Claude Code's API-equivalent cost.

OpenAI prices are per million tokens. Override with EA_OPENAI_PRICES='{"model": [input, output], ...}'
when OpenAI's price list changes.
"""

import json
import os
import threading
from dataclasses import dataclass, field

# Per million tokens (input, output). Checked against OpenAI's pricing page at build time; see the decisions log.
OPENAI_PRICES: dict[str, tuple[float, float]] = {
    "gpt-6.1-sol": (2.50, 10.00),
    "gpt-6-luna": (0.15, 0.60),
}
FALLBACK_PRICE = (2.50, 10.00)


def openai_prices() -> dict[str, tuple[float, float]]:
    prices = dict(OPENAI_PRICES)
    override = os.environ.get("EA_OPENAI_PRICES")
    if override:
        try:
            prices.update({k: tuple(v) for k, v in json.loads(override).items()})
        except (ValueError, TypeError):
            pass
    return prices


class CostCapReached(Exception):
    pass


@dataclass
class CostMeter:
    max_openai_usd: float = float(os.environ.get("EA_MAX_COST_USD", "5.00"))
    openai_usd: float = 0.0
    openai_calls: int = 0
    claude_api_equivalent_usd: float = 0.0
    claude_turns: int = 0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_openai(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p_in, p_out = openai_prices().get(model, FALLBACK_PRICE)
        cost = input_tokens / 1e6 * p_in + output_tokens / 1e6 * p_out
        with self._lock:
            self.openai_usd += cost
            self.openai_calls += 1
            m = self.by_model.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0})
            m["calls"] += 1
            m["input_tokens"] += input_tokens
            m["output_tokens"] += output_tokens
            m["usd"] += cost
        return cost

    def add_claude(self, usd: float, turns: int) -> None:
        with self._lock:
            self.claude_api_equivalent_usd += usd
            self.claude_turns += turns

    def over_cap(self) -> bool:
        return self.openai_usd >= self.max_openai_usd

    def check(self) -> None:
        if self.over_cap():
            raise CostCapReached(f"OpenAI spend ${self.openai_usd:.2f} reached the cap ${self.max_openai_usd:.2f}")

    def as_dict(self) -> dict[str, object]:
        return {
            "openai_usd": round(self.openai_usd, 4),
            "openai_calls": self.openai_calls,
            "openai_by_model": self.by_model,
            "claude_api_equivalent_usd": round(self.claude_api_equivalent_usd, 4),
            "claude_turns": self.claude_turns,
            "max_openai_usd": self.max_openai_usd,
        }
