"""Per-model Anthropic pricing + cost computation.

Prices are USD per 1M tokens, published rates at the date this module was
last updated. Refresh from https://www.anthropic.com/pricing when a new
model lands or rates change.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

# Last reviewed: 2026-07-14 against the current Anthropic pricing catalog.
# Keys are exact model IDs as accepted by the Anthropic SDK.
# Fallback prefixes below handle versioned suffixes (e.g. "-20251001").


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1,000,000 tokens for each token class."""
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_per_mtok: Decimal      # 1.25× input for Anthropic
    cache_read_per_mtok: Decimal       # 0.10× input for Anthropic


def _p(in_: str, out: str) -> ModelPrice:
    inp = Decimal(in_)
    return ModelPrice(
        input_per_mtok=inp,
        output_per_mtok=Decimal(out),
        cache_write_per_mtok=inp * Decimal("1.25"),
        cache_read_per_mtok=inp * Decimal("0.10"),
    )


# Exact-match table. Stripping the date suffix handled by _resolve below.
MODEL_PRICES: dict[str, ModelPrice] = {
    # Claude 5 family
    "claude-sonnet-5":   _p("3",  "15"),   # intro $2/$10 through 2026-08-31; table carries the standard rate
    # Claude 4.x family — Opus dropped to $5/$25 from Opus 4.5 onward
    # (the old $15/$75 rate ended with Opus 4.1)
    "claude-haiku-4-5":  _p("1",  "5"),
    "claude-sonnet-4-6": _p("3",  "15"),
    "claude-sonnet-4-5": _p("3",  "15"),
    "claude-opus-4-8":   _p("5",  "25"),
    "claude-opus-4-7":   _p("5",  "25"),
    "claude-opus-4-6":   _p("5",  "25"),
    "claude-opus-4-5":   _p("5",  "25"),
    "claude-opus-4-1":   _p("15", "75"),
}


def _resolve(model: str) -> ModelPrice | None:
    """Return the price entry for a model id, ignoring -YYYYMMDD suffixes."""
    if not model:
        return None
    if model in MODEL_PRICES:
        return MODEL_PRICES[model]
    # Strip trailing -YYYYMMDD if present.
    parts = model.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        return MODEL_PRICES.get(parts[0])
    return None


def compute_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> Decimal:
    """Total USD for a single call. Returns Decimal("0") if the model is
    unknown — log + degrade gracefully rather than blocking the pipeline.
    """
    price = _resolve(model)
    if price is None:
        import logging
        logging.getLogger(__name__).warning(
            "pricing: unknown model %r — costing this call at $0; add it to MODEL_PRICES",
            model,
        )
        return Decimal("0")
    total = (
        Decimal(input_tokens) * price.input_per_mtok
        + Decimal(output_tokens) * price.output_per_mtok
        + Decimal(cache_write_tokens) * price.cache_write_per_mtok
        + Decimal(cache_read_tokens) * price.cache_read_per_mtok
    ) / Decimal(1_000_000)
    return total.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
