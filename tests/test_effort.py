"""Reasoning-effort selection for summarization LLM calls.

Effort is derived from the model family rather than config (see
pipeline/summarizer.py _EFFORT_BY_FAMILY), so these tests are the contract:
Opus thinks at max, Sonnet at high, and models without an effort parameter
must never receive one — sending it to Haiku 4.5 is a 400.
"""
import pytest

from pipeline import summarizer as sz
from pipeline.pricing import MODEL_PRICES


@pytest.mark.parametrize("model,expected", [
    (sz.OPUS, "max"),
    (sz.SONNET, "high"),
    ("claude-opus-4-7", "max"),
    ("claude-sonnet-4-6", "high"),
    (sz.HAIKU, None),
    ("claude-sonnet-4-5", None),
])
def test_default_effort_by_family(model, expected):
    assert sz._default_effort(model) == expected


def test_effort_kwargs_shape():
    assert sz._effort_kwargs(sz.OPUS, None) == {"output_config": {"effort": "max"}}
    assert sz._effort_kwargs(sz.SONNET, None) == {"output_config": {"effort": "high"}}


def test_explicit_effort_overrides_family_default():
    assert sz._effort_kwargs(sz.OPUS, "low") == {"output_config": {"effort": "low"}}


def test_no_effort_param_for_models_that_reject_it():
    # Empty dict so it can be splatted into the request unconditionally.
    assert sz._effort_kwargs(sz.HAIKU, None) == {}
    assert sz._effort_kwargs(sz.HAIKU, "high") == {}


def test_unknown_effort_level_is_dropped_not_sent():
    assert sz._effort_kwargs(sz.OPUS, "turbo") == {}


def test_default_models_are_priced():
    # An unpriced model silently costs every call at $0 (pricing.compute_cost).
    for key in ("document_model", "item_model", "meeting_model"):
        model = sz._DEFAULT_MODELS[key]
        assert model.rsplit("-", 1)[0] in MODEL_PRICES or model in MODEL_PRICES


def test_max_token_budgets_fit_under_the_model_cap():
    for key, model in (("document_max_tokens", sz._DEFAULT_MODELS["document_model"]),
                       ("item_max_tokens", sz._DEFAULT_MODELS["item_model"]),
                       ("meeting_max_tokens", sz._DEFAULT_MODELS["meeting_model"])):
        assert sz._DEFAULT_MAX_TOKENS[key] <= sz._model_output_cap(model)
