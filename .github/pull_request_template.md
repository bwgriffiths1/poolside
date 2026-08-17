## What

## Why

## Checklist
- [ ] `python -m pytest tests/ -q` passes locally (CI runs ruff + web build only — no pytest)
- [ ] If this PR edits `prompts/*.md`: prod `prompt_overrides` reconciled — update or clear the
      DB override for each edited slug so the repo change actually takes effect.
      (`pjm_cifp-rbp_*` slugs cannot have overrides; file edits always take effect there.)
