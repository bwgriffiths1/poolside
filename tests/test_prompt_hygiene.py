"""Prompt hygiene guards — dead scaffolding must not creep back in.

The agenda-item prompts carried two pieces of legacy scaffolding: a
hardcoded '[PRIOR CONTEXT] None available…' stub that was never substituted
(prior-context injection is live at briefing level only), and a fixed
'Target 150–300 words.' quota that compressed heavyweight items and padded
thin ones. These pin their removal; pjm_lc's 80–200 cap is deliberate (LC
substance happens in closed session — padding is the failure mode there)
and must survive.
"""
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def _agenda_prompts():
    files = sorted(PROMPTS.glob("*agenda_item_prompt.md"))
    assert len(files) >= 14
    return files


def test_no_dead_prior_context_stub():
    for p in _agenda_prompts():
        assert "will be populated with prior-meeting context" not in \
            p.read_text(encoding="utf-8"), p.name


def test_no_fixed_word_quota():
    for p in _agenda_prompts():
        assert "Target 150–300 words" not in p.read_text(encoding="utf-8"), p.name


def test_pjm_lc_keeps_deliberate_cap():
    text = (PROMPTS / "pjm_lc_agenda_item_prompt.md").read_text(encoding="utf-8")
    assert "80–200 words" in text
