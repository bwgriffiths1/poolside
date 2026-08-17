# Making Poolside Summaries & Briefings More Accurate and Actionable

## Target environment — read first

This plan was researched from a session in `~/Documents/Analysis/Wine` but **all work happens in the poolside repo**: `/Users/bwgriffiths/Documents/Analysis/poolside/` (github.com/bwgriffiths1/poolside, prod = poolside.bwgriffiths.com on Railway).

**To execute:** start Claude Code in the poolside directory and point it at this plan. Recommended first step — copy the plan into the repo so it travels with the work:

```bash
cp /Users/bwgriffiths/.claude/plans/i-am-thinking-through-deep-ladybug.md "/Users/bwgriffiths/Documents/Analysis/poolside/Docs/summary-quality-roadmap.md"
```

Then kick off with e.g.: *"Execute Batch 1 of Docs/summary-quality-roadmap.md (PRs 1.1–1.5)."* Batches are independent PR groups — run them in separate sessions as convenient, in order: 1 → 2 → 3 → 4 → 5 → 6.

**Environment facts the executing session needs** (from poolside project memory; verify against `~/.claude/projects/-Users-bwgriffiths-Documents-Analysis-poolside/memory/MEMORY.md`):
- The main checkout is **shared by concurrent sessions** — the branch can switch under you. Never `git add -A`; ship each change from its own `git worktree`.
- `ANTHROPIC_API_KEY` is NOT in the live repo — it lives in `/Users/bwgriffiths/Documents/Analysis/poolside-legacy/.env`. Any script that calls the API (eval harness, live tests) must source it from there.
- Local dev DB: plain `localhost` URL; the repo `.env` has no `DATABASE_URL` — pass it explicitly for worktree uvicorn runs (`load_dotenv(override=True)` gotcha). Users table is `app_users`.
- Prompts and model config are **DB-overridable** (`prompt_overrides`, `app_config.model_config`) and the DB wins — reconcile overrides (Batch 1.5) before merging any prompt-file PR, against prod.
- Migrations: numbered SQL in `pipeline/migrations/`; latest is `020_…`; two files share `017` — start new ones at `021`, don't reuse numbers. Run schema.sql before migrations on a fresh DB.
- June A/B artifacts (blind docx + key + harness) are in `/Users/bwgriffiths/Documents/Analysis/poolside-legacy/ab_results/june_mc/` and `ab_test_june_mc.py`.
- Frontend verify: browser-pane polling and worktree `:8000`-serves-dist gotchas are documented in the project memory files; consult before browser verification.

## Context

Ben wants better summaries and briefing documents from **poolside** (`/Users/bwgriffiths/Documents/Analysis/poolside/`) — the app that scrapes ISO-NE/NEPOOL + PJM meeting materials and FERC eLibrary dockets, then produces Layer 1 per-doc summaries → Layer 2 agenda-item rollups → Layer 3 briefings (web/docx/email), plus per-filing FERC summaries and a docket state-of-play (SOP).

His three prompts: (1) explore multi-stage agentic approaches, e.g. dueling Claude+OpenAI summaries reconciled; (2) inject more context so takeaways land for less-familiar readers; (3) the FERC per-filing summaries are better than the ISO per-file ones — diagnose and close the gap. Overall goal: maximum accuracy and actionability.

**Decisions made with Ben (2026-08-17):** Claude-only multi-pass first, cross-vendor duel as a measured pilot behind a flag · audience = energy-literate colleagues who don't follow these committees · cost ceiling ~2–3× current (≤$10–15/meeting) · **retire the generated L2 (agenda-item) tier** — Ben uses the briefing + file-level summaries; the middle tier doesn't help him. Supporting evidence: L3 already builds from *leaf* summaries (`_collect_leaf_summaries`; parent rollups are UI-only except as fallback), the briefing already contains per-item sections mapped to their docs (`attach_briefing_docs`), and the FERC architecture Ben prefers is exactly this two-tier shape (per-filing + docket SOP, no middle).

## Diagnosis: why FERC per-file summaries beat ISO ones (verified in source)

Both run sonnet-5@high — model choice is not the cause:

1. **Wrong unit of work.** ISO L1 stuffs ALL of an agenda item's docs into ONE call (`pipeline/summarizer.py:1069-1086`) against `prompts/doc_summary_prompt.md`, written for a SINGLE document. FERC: one filing per call.
2. **One generic prompt vs six routed prompts.** FERC routes by documentClass + anchor role (`_PROMPT_BY_CLASS`, `docket_ingest.py:102-116`; initial/order prompts run opus-5@max). ISO gives a redline, a 40-slide deck, and a memo the identical template.
3. **Portfolio context missing exactly at ISO L1.** `general_context_prompt.md` is injected into every FERC call and ISO L2/L3 — but NOT L1 (bare `_load_prompt` at `summarizer.py:1897`; also `api/resummarize.py:94-97,149-152`).
4. **Thin metadata.** FERC gets 9 fields (parties, counsel, class, dates, cite). ISO L1's `_item_metadata_block` lacks even meeting date/committee/venue (`db.get_agenda_items` has no meeting join; `db.get_meeting` has the fields).
5. **No TLDR.** FERC requires `TLDR:` line 1 → `summary_versions.one_line` (`_split_tldr`, `docket_ingest.py:585-591`). ISO L1/L2/L3 write `one_line=None` — hence the dead briefing headline (`Briefing.tsx:364` renders it, always empty) and no scannable per-item line.
6. **Double compression bottleneck.** L2 prompts cap at 150–300 words regardless of source volume; L3's proportionality rule fights a cap set two layers down.
7. **Dead context stub.** 9 agenda-item prompts hardcode `[PRIOR CONTEXT] None available. (…future version…)` — never substituted (PJM's 5 newer prompts dropped it). `db.get_prior_meeting_briefings` exists but feeds L3 only.
8. **No input caps at ISO L1** (FERC: 150k chars/file, 400k/filing) → silent lost-in-the-middle on big items.
9. **Prompt age.** doc_summary/agenda_item prompts: one commit (2026-04-01), never iterated. FERC prompts: 2026-07-22/23, written with three months of lessons. The FERC prompts also demand reasoning-ready structure ("quote the operative verbs", "the seams commenters will work", "What to Watch").
10. **Extraction asymmetry.** Same extractor, different material: FERC = text-native prose PDFs. ISO = deck-dominated; python-pptx takes `shape.text` only (tables/notes/geometry lost); docx tables flattened after body. pdfplumber is already a dependency (used by `llm_agenda_parser.py:148-152` for agendas) but unused for document extraction. Images: max 8/item, ≤2 kept; `api/resummarize.py:163` hardcodes `extract_images=False` (re-runs silently lose images; Path A same bug via default).

Supporting facts: Anthropic-only today (zero OpenAI presence). No eval harness in the live repo; the June MC one-shot-vs-pipeline blind test (4 docx + key in `poolside-legacy/ab_results/june_mc/blind/`) was **never scored**. Edit-preserving regen exists only for FERC SOP + roundups ("preserve analyst edits" via `[PRIOR STATE OF PLAY]`, proven on ER26-925). `summary_versions.is_manual` edit history = free regression data. Prompt files are repo defaults; DB `prompt_overrides` win wholesale. Costs today: briefing $0.89–$2.35/meeting; one-shot Opus 4.8 $2.27, Fable $5.54; FERC sync $0.43–0.62.

## Strategy

Make the ISO side architecturally identical to the FERC side Ben prefers: **two tiers** — per-document summaries (class-routed, anchor-tiered, TLDR'd, context-injected) feeding one top-level briefing synthesized directly from agenda structure + doc summaries, with edit-preserving regeneration. The generated agenda-item middle tier is retired: it barely feeds L3 today (L3 walks to leaves), it exists mostly to fill the meeting-browser UI (which the briefing's own per-item sections can fill via the existing section→docs mapping), and it imposes a 150–300-word compression bottleneck between the files and the briefing. Add a draft→verify→revise stage for faithfulness; build measurement before the expensive changes; add reader-context features for the colleague audience; pilot the cross-vendor duel only where the eval proves it earns its cost.

On dueling summaries specifically: the value is decorrelated error detection. Sequence it — (a) same-model adversarial verify (cheap, catches most hallucinated numbers/attributions), (b) cross-family within Anthropic (Sonnet drafts, Opus verifies — already partly decorrelated), (c) cross-vendor duel with claim-set reconciliation (GPT-5.x second draft; reconciler treats both drafts as claim sets: agreements = high confidence, singletons verified against source, contradictions resolved against source; final text rewritten in house voice, unresolved conflicts flagged). Each step is config-gated and judged on the eval set before promotion.

## Implementation plan (batches ≈ PR groups)

### Batch 1 — "FERC-ize" ISO L1 (quick wins; ship first)
Plan-agent-verified file detail; all changes preserve summary_versions contract (new drafts, old rows untouched):

- **1.1 Context + metadata + image fix** (`pipeline/summarizer.py`, `api/resummarize.py`): new `_load_doc_summary_prompt()` prepending general_context (context BEFORE template — stable cacheable prefix); hoist `db.get_meeting` fetch, thread `meeting` into `_summarize_item_docs`/`_run_item_rollup`/`_run_meeting_briefing`; `_item_metadata_block(item, meeting)` adds Meeting/venue/date/location lines; extract `_resolve_meeting_folder()` helper; resummarize.py Paths A+B get `extract_images` from image config + real `meeting_folder`, and use the context-injected prompt loader.
- **1.2 Input caps** : `_MAX_CHARS_PER_DOC=150_000`, `_MAX_CHARS_PER_ITEM=600_000` (config-overridable, defaults-merged), FERC-style `…(truncated)` / `…(further documents omitted for length)` markers, log when fired.
- **1.3 TLDR everywhere**: move `_split_tldr` → `summarizer.split_tldr` (alias kept for docket_ingest imports); split before every ISO `create_summary_version` (L1 997-1010, L2 1480-1492, own-docs 2051-2065, L3 1721-1733; after `_replace_keep_images_inline`); add TLDR instruction to doc_summary + 14 agenda-item + 13 briefing prompts. Fixes dead briefing headline + gives scannable one_lines with zero frontend work. Backward compat: `split_tldr` returns `(None, text)` on non-compliance; adapters already fall back.
- **1.4 Kill dead stub (+ interim L2 cap loosening)**: delete the `[PRIOR CONTEXT]` stub from all 9 files. The L2 word-cap edit is now interim-only (the tier retires in Batch 3) — do the cheap version: swap `150–300 words` for proportionality language in the same PR, no separate measurement gate.
- **1.5 DB-override reconciliation (process, before merging prompt PRs)**: `GET /api/prompts` → for every edited slug with `overridden:true`, port edits into the override or DELETE stale overrides; check `model_config` override too; add a note to the PR template.
- Tests: extend the `tests/test_l2_own_docs.py` FakeDB pattern (fake LLM returns `TLDR: …\n\nBody`, assert split writes at all 4 sites, general-context presence in L1 prompt, metadata block contents, resummarize image flags, truncation markers + bounded prompt).

### Batch 2 — Measurement (before the expensive changes)
- **Score the June MC blind test** (exists, 20 minutes): Ben ranks the 4 blind docx; record verdict in the poolside memory + `ab_results/`. Directly answers TWO open questions: one-shot vs pipeline (arms c/d), and **Fable vs Opus at L3** (arm b = pure L3 model swap, same prompts/inputs).
- **Fable-for-the-top-tier evaluation**: make `opus-5@max` vs `fable-5@high` (and `@max` if supported) a standard eval-harness comparison for L3 briefing, FERC SOP, and (later) the verifier. Cost delta ≈ +$0.75–1.50/briefing (Fable $10/$50 vs Opus $5/$25 per MTok) — inside ceiling. No compatibility blockers: the `content[0].text` crash is fixed (`_first_text`, summarizer.py:765-769) and `claude-fable-` is in the effort table (currently `high` — decide whether to map it to `max`, one line). Enablement checklist: no `temperature` param (Fable rejects it), `meeting_max_tokens` headroom (thinking shares the budget), one prompt-retune pass (June showed Fable writes shorter: 3,676 vs 5,501 words on identical inputs). Flip `meeting_model`/`ferc_state_of_play_model` per-key wherever Fable measurably wins — it's a one-key `model_config` change via the existing UI.
- **`tools/eval/` harness** (CLI, no pipeline changes): `fetch_golden.py` (read-only prod pull, ~12 frozen cases: FERC initial/order/comment; ISO deck-heavy/memo/study/admin items; one full meeting; 2-3 multi-doc items — extracted text only, content-hashed); `fakedb.py` grown from test_l2_own_docs; `runner.py` executes real `run_meeting_summarization`/`summarize_filing` with monkeypatched db/prompts/config — prompt-sets addressable by git ref (pre-Batch-1 baselines replayable forever); `score.py` = mechanical checks (TLDR regex, `parse_briefing_markdown` round-trip, KEEP_IMAGE integrity) + opus-5 judge rubric (faithfulness, decision-relevant completeness, context-sufficiency for a non-follower, actionability, format) + pairwise mode with position swap; `blind.py` docx side-by-sides (legacy pattern); `mine_regressions.py` mines `is_manual=true` versions into curated regression YAML ("Ben corrected this once — don't regress"). ANTHROPIC_API_KEY sourcing documented (lives only in `poolside-legacy/.env`).

### Batch 3 — Two-tier restructure: per-document summaries → briefing (the big structural lever)
Replaces the old three-tier flow. Config-gated `summarization.two_tier.enabled` (default false) so legacy behavior is byte-identical until enabled per committee.

**Generation side:**
- Migration `021_document_class_roles.sql` (numbering: latest is 020; two 017s exist — don't reuse): `documents.doc_class/doc_class_source`; `item_documents.role` ('anchor' on the edge — docs are many-to-many).
- New `pipeline/doc_classify.py`: rules first (extension/filename regex → deck/memo/redline/study/minutes_admin), haiku fallback w/ sonnet-4-6 escalation (agenda-parse pattern, config.yaml:95-99); `compute_anchor` (presenter-org deck > largest deck > study > memo, deterministic); `assign_doc_roles` mirrors FERC `_assign_roles` incl. `supersede_auto_summaries("document", …)` on role change (manual/approved never touched).
- `summarizer.py`: `_run_doc_summary` = one doc → one call → `entity_type='document'` row (type exists, currently unread — clean surface) with TLDR + per-doc images; prompt routing `_DOC_PROMPT_BY_CLASS` → new `prompts/doc_{deck,memo,redline,study,admin,anchor}_prompt.md` (anchor = FERC-initial depth; admin = haiku brief tier). **No item-synthesis call** — the doc summaries ARE the lower tier.
- **L3 consumes agenda structure + per-doc summaries directly**: `_run_meeting_briefing` input becomes the agenda-structure block (exists) with each item's doc summaries nested under it (grouped by `item_documents`, anchor first, `### [filename]`-style headers). For a big MC meeting ≈ 40–50K input tokens — trivial for opus-5@max, and the 150–300-word bottleneck disappears structurally. Briefing prompt keeps the identical output contract (`## n — Title` sections, parser untouched); add "reconcile multiple decks addressing one item; keep distinctly-authored presentations in separate sub-items" language (already partly there).
- **Edit-preserving briefing regen** (port of the FERC SOP loop): regenerating a briefing injects the current version (incl. manual edits) as `[PRIOR BRIEFING — may contain analyst edits; preserve them where accurate]`. Makes incremental updates safe: new docs → summarize individually (naturally incremental) → regen briefing without losing Ben's edits. This is the two-tier answer to the staleness problem.
- Legacy fallback: meetings with no document rows (all history) collect leaf agenda_item summaries as today (`_collect_leaf_summaries` kept as fallback path).
- Estimator mirrored (per-doc call counting, no synthesis calls); KEEP_IMAGE refs flow from doc summaries into the briefing via existing `_collect_image_refs`.

**Read side:**
- Add `"document"` to `api/routes/summaries.py` `_ENTITY_TYPES` + parent_label (filename) so versions/edit/approve work via existing plumbing; DocumentPage-style reader for doc summaries with TLDR.
- Meeting-browser item rows: text comes from the briefing's section for that item (AST section→item mapping via `attach_briefing_docs` distribution) + anchor-doc TLDR as the item `one_line`; expandable per-doc summary cards. `agenda_item` entity stays for manual notes + legacy display; no new generation writes to it.
- Ask/search: index `document` summaries (better citation granularity; deep-link to doc reader); initiative timeline entries excerpt the briefing section or anchor TLDR instead of item summaries.
- resummarize.py collapses to: re-summarize selected docs (Path B per-doc) + optional briefing regen; Path A (L2) retired under the gate.
- 14 agenda-item prompt files stop being maintained (left in repo for override history); their TLDR edits from Batch 1 still apply while the gate is off.
- `clear_agenda_for_meeting` gains document-summary cleanup (existing orphan exposure, fix while in there).

**Rollout:** migration → classification-only backfill (eyeball doc_class in DB) → enable two_tier for one committee's next meeting → A/B against the three-tier output via Batch 2 (briefing quality + browser usability) → default on.
**Risk watch:** single point of synthesis means a bad L3 call has bigger blast radius — mitigated by Batch 4 verify targeting briefings, doc summaries as ground truth, and version restore. Verify L3 input stays comfortable on the biggest meetings (MRC ~45 docs ≈ 40–50K tokens; fine).

### Batch 4 — Verify stage (draft → verify → revise)
- **4.1** `_call_llm`/`_call_llm_multimodal` accept block lists (string path byte-identical); no pricing work needed (`_record_usage` + `compute_cost` already handle cache tokens; anthropic 0.117.0 has GA cache_control).
- **4.2** New `pipeline/verify.py`: `build_source_blocks` (general context + source with `cache_control`, breakpoint before stage instructions so verify+revise share one cached prefix); `run_verify` → strict-JSON findings `{type: unsupported|wrong_number|wrong_date|wrong_attribution|omission, severity, claim, evidence, fix}` (parse failure never blocks pipeline); `run_revise` constrained to flagged spans, preserves headings/KEEP_IMAGE/TLDR; config `summarization.verify: {enabled:false, surfaces:[briefing], model→item_model, revise_min_severity:material}`. Hooks: L3 briefing (source = the doc-summary corpus it synthesized from), Batch-3 anchor docs, FERC anchors. New prompts `verify_prompt.md`/`revise_prompt.md` (DB-overridable free).
- Findings storage: migration `022_summary_checks.sql` — `summary_versions.check_status` ('passed|revised|flagged|error') + `check_findings JSONB`; `_version_meta` exposes them; VersionHistory badge optional.
- Cost (sonnet verifier, briefing surface): verify ≈$0.22 + revise ≈$0.15 → **+$0.35–0.40/briefing** (cache-read 0.10× is what makes revise cheap). anchor_docs surface ≈ +$1.0–1.3/meeting — off by default. Rollout dark → one committee → widen on eval scores.

### Batch 5 — Context & actionability for the colleague audience
- **Initiative prior-context wiring** (the deferred stub replacement, retargeted for two-tier): for items tagged with an initiative (`entity_tags`/`tags.tag_type='initiative'`), inject ≤2k-char excerpts of the initiative briefs ("story so far") into the **briefing call's** `[PRIOR CONTEXT]` (alongside prior-meeting excerpts) and into **anchor-doc** prompts for tagged items (config-gated `summarization.initiative_context`); measured via Batch 2 before default-on. Longer term: initiative briefs auto-regen after each tagged meeting with FERC-SOP-style edit preservation (already versioned via the roundup pattern).
- **Briefing `## Background` section**: 2–4 orientation bullets for a non-follower (what this committee is deciding lately, which storylines this meeting advances), drawn from [PRIOR CONTEXT] + initiative briefs; known-heading branch in `api/briefing_parser.py` + docx eyebrow section in `pipeline/briefing.py`; PublicShare inherits.
- **First-use acronym expansion + orientation clause**: one paragraph added to `general_context_prompt.md` ("reader is energy-literate but does not follow this committee; expand acronyms on first use; give one orientation clause for any initiative named"). Free. Full glossary table/tooltips deferred (sketch: seed from `keyword_extraction_prompt` output, venue-scoped table, tooltip in reader, docx appendix).
- **Callout adoption**: L2/L3 prompts emit `> [!Decision]` for votes/decisions-requested and `> [!Risk]` for open design risks — the `> [!Label]` pipeline already round-trips through every renderer and the editor has the buttons; zero parser work. Ship behind the eval.
- **Email content**: `briefing_approved_email` + weekly digest include the parsed Key Takeaways bullets (fields already exist; mailer templates only). Docx gains the Decisions & next steps table (web-only today).

### Batch 6 — Duel pilot + one-shot revisit (evidence-gated)
- `pipeline/llm_providers.py`: thin OpenAI wrapper; `openai` dep + `OPENAI_API_KEY`; config `summarization.duel: {enabled:false, model, surfaces:[briefing, ferc_anchor]}`. Flow: second independent draft (GPT-5.x, same inputs) → opus-5 reconciler does claim-set diff against source → final revision in house voice; both arms stored as draft versions (provenance in VersionHistory), unresolved conflicts → `check_findings`. Adopt per-surface only where golden-set scores beat Claude-only verify by a real margin; cost +$2–4/dueled artifact.
- One-shot L3: if the June MC blind scoring (Batch 2) favors arm c/d, spec a follow-up to make L3 one-shot over the raw corpus (per-doc L1 stays regardless — it feeds the reader UI and Ask).

## Cost budget (vs ≤$10–15 ceiling)
Today $2–5/meeting → Batch 1 ≈ +0–5% · Batch 3 ≈ roughly cost-neutral to +$1–2: more L1 calls (each smaller; haiku admin tier; cached shared prefix) but **all L2 synthesis calls eliminated** and briefing input only grows ~$0.10–0.20 at opus input rates · Batch 4 briefing +$0.35–0.65, anchors +$1.0–1.3 opt-in · typical all-on total ≈ **$5–10/meeting**; duel pilot only on top where it earns it.

## Verification
- Per-PR unit tests on the FakeDB/mocked-LLM patterns above; golden-fixture pin that briefing parsing is unchanged for TLDR-less legacy briefings.
- `python -m tools.eval run/score/report` before/after each batch; git-ref prompt baselines for regression comparison; judge + cost per variant.
- Blind scoring for shape changes (L2 cap, Background section, callouts, duel) using `blind.py` docx side-by-sides.
- Live pilot per committee with `capture_usage` cost check; `check_status` distribution monitored after Batch 4.
- Process: prompt-override reconciliation (1.5) before merging any prompt PR; ship via git worktree (concurrent sessions share the main checkout); API key from `poolside-legacy/.env`; local dev DB = plain localhost URL.

## Out of scope for now
Staleness automation (`file_hash` population + auto-resummarize) — real accuracy issue, separate batch; pptx extraction upgrade (speaker notes, tables via pdfplumber/vision-native PDF input) — revisit after Batch 3 eval shows where extraction is the binding constraint; glossary table/tooltips; deadline aggregation → calendar.
