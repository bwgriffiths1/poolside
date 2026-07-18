"""Cost-math tests for pipeline/pricing.py."""
from decimal import Decimal

from pipeline.pricing import MODEL_PRICES, compute_cost


def test_exact_model_costing():
    # opus 4.x current rate: $5/M in, $25/M out
    cost = compute_cost("claude-opus-4-7", input_tokens=1_000_000, output_tokens=100_000)
    assert cost == Decimal("5") + Decimal("2.5")


def test_date_suffix_resolves_to_base_model():
    dated = compute_cost("claude-haiku-4-5-20251001", input_tokens=2_000_000)
    base = compute_cost("claude-haiku-4-5", input_tokens=2_000_000)
    assert dated == base == Decimal("2")


def test_unknown_model_costs_zero_not_crash():
    assert compute_cost("claude-nonexistent-9", input_tokens=1_000_000) == Decimal("0")


def test_cache_token_multipliers():
    # cache write = 1.25× input rate, cache read = 0.10× input rate
    p = MODEL_PRICES["claude-sonnet-4-6"]
    assert p.cache_write_per_mtok == p.input_per_mtok * Decimal("1.25")
    assert p.cache_read_per_mtok == p.input_per_mtok * Decimal("0.10")
    cost = compute_cost(
        "claude-sonnet-4-6", cache_write_tokens=1_000_000, cache_read_tokens=1_000_000
    )
    assert cost == Decimal("3.75") + Decimal("0.30")


def test_quantized_to_four_places():
    cost = compute_cost("claude-haiku-4-5", input_tokens=333)
    assert cost == Decimal("0.0003")
    assert -cost.as_tuple().exponent == 4


def test_opus_legacy_rate_still_on_41():
    """Opus 4.1 keeps the old $15/$75; 4.5+ moved to $5/$25 — a prior review
    found these rows 3x wrong, so pin them."""
    assert MODEL_PRICES["claude-opus-4-1"].input_per_mtok == Decimal("15")
    assert MODEL_PRICES["claude-opus-4-5"].input_per_mtok == Decimal("5")
