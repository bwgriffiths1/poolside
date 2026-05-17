# Handoff: Poolside — Meeting Intelligence

A web-native redesign of the Streamlit-based ISO-NE / NYISO meeting briefing app.

---

## About the design files

The files in this bundle are **design references created in HTML/React-via-Babel** — clickable prototypes showing intended look and behavior. **They are not production code to copy directly.**

The task is to **recreate these designs in the target codebase's environment** (presumably a modern React app: Next.js, Vite + React Router, Remix, etc.) using its established patterns, libraries, and design system. If no environment exists yet, Next.js + TypeScript + Tailwind (or vanilla CSS-vars) is a reasonable default.

The Streamlit code in the existing `streamlit-oauth-demo` repo defines the **data model and backend logic** to wire to — it should not influence the front-end implementation choices.

## Fidelity

**High-fidelity.** All colors, typography, spacing, interactions, and copy are final and should be matched pixel-faithfully. Three aesthetic variants are included (Editorial / Minimal / Terminal) but **Editorial is the canonical direction** — ship that first.

---

## Scope

Four screens are designed:

1. **Overview** — Calendar/list of meetings with status pills, filters, KPI strip
2. **Meeting Detail** — Agenda items, documents, per-item summaries, summarize runner, edit mode
3. **Briefing Reader** *(the hero)* — Editorial magazine-style rendering of the AI briefing
4. **Add Meeting** — Two-mode ingest flow (auto-scrape or manual) with live job log

A persistent left sidebar holds top-level navigation. The branding mark is **"Poolside."** (lowercase, terracotta period). User chip lives at the bottom of the sidebar.

---

## Design tokens

All tokens are defined in `styles.css` as CSS custom properties. Three palette variants exist (Editorial / Minimal / Terminal) — Editorial is the canonical one below.

### Color (Editorial)

| Token            | Hex       | Use                                   |
| ---------------- | --------- | ------------------------------------- |
| `--bg`           | `#f6f4ef` | Page background (warm cream)          |
| `--bg-elev`      | `#fbf9f4` | Cards, surfaces above bg              |
| `--bg-sunk`      | `#efece4` | Nested/sunken surfaces, table headers |
| `--ink`          | `#1a1815` | Primary text (warm near-black)        |
| `--ink-soft`     | `#4a4640` | Secondary text                        |
| `--muted`        | `#8a847a` | Tertiary text, metadata               |
| `--muted-soft`   | `#b5afa3` | Disabled, scrollbar hover             |
| `--border`       | `#e3ddd1` | Card borders, dividers                |
| `--border-soft`  | `#ece6da` | Internal/nested dividers              |
| `--accent`       | `#c4633a` | Terracotta — links, CTAs, active nav  |
| `--accent-soft`  | `#e5b89e` | Accent borders, gradients             |
| `--accent-tint`  | `#f4e4d8` | Accent backgrounds (callouts, hover)  |
| `--success`      | `#4d7c4e` | Approved votes, complete states       |
| `--warn`         | `#b88a2c` | "Materials Posted" status             |
| `--info`         | `#4a6b8f` | "Scheduled" status                    |
| `--danger`       | `#a44a3c` | Errors, negative deltas               |

### Typography

Three faces from Google Fonts:

```html
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,300..700;1,6..72,300..700&family=Geist:wght@300..700&family=Geist+Mono:wght@300..600&display=swap" rel="stylesheet">
```

| Token          | Family                              | Use                                                                     |
| -------------- | ----------------------------------- | ----------------------------------------------------------------------- |
| `--font-sans`  | **Geist**, Helvetica Neue, system-ui| UI: buttons, labels, body, tables                                       |
| `--font-serif` | **Newsreader**, Iowan, Georgia      | Briefing body, page titles, large numerals, italics for one-liners      |
| `--font-mono`  | **Geist Mono**, JetBrains Mono      | Metadata, dates, IDs, eyebrows, tags, KPIs in mono, code-like content   |

**Type scale (Editorial):**

- Briefing title: Newsreader 400 · 44px · -0.02em · 1.05
- Briefing headline (subtitle): Newsreader 400 italic · 22px · 1.4
- Page title: Newsreader 400 · 38px · -0.015em · 1.05
- Section H2 (briefing): Newsreader 400 · 28px · -0.015em · 1.15
- Stat block num: Newsreader 400 · 28px · -0.02em · 1
- KPI num: Newsreader 400 · 36px · -0.02em · 1
- Body (briefing): Newsreader 400 · 17px · 1.65 · color `--ink-soft`
- Body (UI): Geist 400 · 14px · 1.45
- Eyebrow/meta: Geist Mono 400 · 10.5–11.5px · uppercase · 0.06–0.1em letter-spacing · `--muted`
- Drop cap: Newsreader 400 · 4.2em float-left · color `--accent`

### Spacing scale

CSS variables — note: `--pad-3` to `--pad-8` change with density.

| Comfortable (default) | Compact | Spacious |
| --------------------- | ------- | -------- |
| `--pad-1` 4px         | 4px     | 4px      |
| `--pad-2` 8px         | 8px     | 8px      |
| `--pad-3` 12px        | 8px     | 16px     |
| `--pad-4` 16px        | 12px    | 22px     |
| `--pad-5` 24px        | 16px    | 32px     |
| `--pad-6` 32px        | 22px    | 48px     |
| `--pad-7` 48px        | 48px    | 48px     |
| `--pad-8` 64px        | 64px    | 64px     |

### Radius / shape

- `--radius-sm` 4px (tags, badges, table cells)
- `--radius`    6px (buttons, cards, inputs)
- `--radius-lg` 10px (the hero briefing card)
- Pills: `border-radius: 999px` (status pills)
- Avatar: 50% (circle)

### Misc constants

- Sidebar width: 232px
- Topbar height: 56px (`--header-h`)
- Default row height: 44px (`--row-h`)
- Sticky topbar uses `backdrop-filter: saturate(140%) blur(8px)` over a 92% bg color-mix
- Briefing TOC is sticky at `top: calc(var(--header-h) + 16px)` with `max-height: calc(100vh - 88px)` and its own scroll

---

## Screens

### 1. Overview (`/overview`)

**Purpose:** Pick up where you left off — see what's on the calendar, which meetings need attention, jump into any of them.

**Layout (left→right, top→bottom):**

1. Topbar: breadcrumb "OVERVIEW", actions: `[Refresh calendars]` `[+ Add meeting]`
2. Page header:
   - Eyebrow mono caps: "Meeting calendar · ISO-NE, NYISO"
   - Title (serif 38px): "Good morning, Ben."
   - Subtitle (14.5px muted): personalized sentence with counts
3. **KPI strip** — 4-column grid, 1px border seam, 1px outer border. Each cell: mono uppercase label, large serif number, muted sub.
   - "Upcoming · 5 · next 30 days"
   - "Materials ready · 2 · awaiting briefing"
   - "Summarized · 4 · this month"
   - "Hours saved · ~64 · vs. manual briefing time"
4. **Filter bar** — left: two segmented controls (Venue: All / ISO-NE / NYISO · Status: All / Scheduled / Materials / Summarized / Updated). Right: view toggle (List / Cards).
5. **Upcoming section** — section header (serif 22px) + meta count. Then list or card grid.
6. **Recent section** — same shape as Upcoming.

**Meeting row (list view)** — grid: `60px 110px 1fr 130px 220px 130px 20px` gap 16px:

| Col            | Content                                                                                         |
| -------------- | ----------------------------------------------------------------------------------------------- |
| Date block     | Mono month "MAY" 10px / Newsreader day "12" 26px, right-bordered                                |
| Venue stack    | Vertical: `<venue-tag>` (black bg, white text, mono) over `<type-tag>` (border, mono)           |
| Title block    | Title line "Markets Committee" 14px 500 / meta line "Holyoke, MA · May 12–13, 2026" 12px muted  |
| Stats          | "47 docs" / "14 items" — mono number, sans label, muted                                         |
| Tags           | Up to 2 `<Tag>` chips (bg-sunk, mono 11px) + "+1" overflow                                      |
| Status pill    | `<Pill status="summarized">` — dot + uppercase mono label                                       |
| Chevron        | `→` 14px, shifts +2px on hover, terracotta                                                      |

Hover: row bg `--bg`, chevron color `--accent` and translateX(2px).

**Status pill states** — all have `border-radius: 999px`, mono 10.5px uppercase, bg `--bg-elev`, border `--border`. Differentiated by the dot color:

- `scheduled`  — dot `--info`     "Scheduled"
- `materials`  — dot `--warn`     "Materials Posted"
- `summarized` — dot `--success`  "Summarized"
- `updated`    — dot `--accent`   "Updated"

**Card view** — `repeat(auto-fill, minmax(260px, 1fr))` grid, 16px gap. Each card: venue+type top-left, status pill top-right, title (15px 500), date (Newsreader 18px), location (muted 11.5px), stats row, tag chips at bottom. Hover: border darkens to `--muted-soft`, `translateY(-1px)`.

**Click anywhere on a row/card** → navigate to Meeting Detail.

---

### 2. Meeting Detail (`/meeting/:id`)

**Purpose:** Inspect a meeting's agenda + documents, edit per-item metadata/summaries, kick off summarization, jump to the briefing.

**Layout:**

1. Topbar: breadcrumb `Overview / ISO-NE · MC / Markets Committee — May 2026`. Actions: `[Open briefing]` `[Summarize ★]` (primary).
2. **Meeting head** — 2-col grid, gap 32px, bottom-bordered:
   - Left: eyebrow with venue-tag + type-tag + external_id, title (serif 38px), meta row (calendar icon + date · globe + location · status pill), italicized **headline** serif 19px under the meta.
   - Right: 3 **stat blocks** in a connected pill-card. Each block: serif 28px number, mono uppercase label. "14 agenda items / 47 documents / 9/14 summarized" (slash in muted color).
3. **Tag rail** — "Topics" mono label + chips + `[+]` button.
4. **Briefing card** *(hero CTA)* — gradient bg `--accent-tint` → `--bg-elev`, accent border, radius 10px, 32px padding. Left side: "Meeting briefing · v2" eyebrow, large serif headline of the briefing, then meta (word count · reading time · model · timestamp). Right side: `[↓ Download .docx]` `[Read briefing →]` (accent). Hover: translateY(-1px), soft shadow.
5. **Summary runner** *(collapsible)* — appears when user clicks Summarize. Contains briefing-style toggle, extract-images checkbox, force-rerun checkbox, cost/time estimate, `[▶ Run]` accent button. Cost format: "~$2.40 · ~3 min".
6. **Agenda section header.**
7. **Agenda list** — accordion-style.

**Agenda item row** — grid: `20px 50px 1fr 180px 160px 32px`:

| Col              | Content                                                              |
| ---------------- | -------------------------------------------------------------------- |
| Chevron          | `>` collapsed / `v` expanded, muted                                  |
| Item number      | Mono 12px muted — "1", "3", "3.1", etc.                              |
| Title + oneline  | Title 14px 500 / **italic serif** one-line summary 14px ink-soft     |
| Presenter        | Right-aligned text-xs muted "C. Cardamone · ISO-NE"                  |
| Vote pill        | Mono caps 10px, color-coded (approved=green, discussion=info)        |
| Summary state    | 18px circle: green-check if summarized, dashed muted circle if not   |

**Sub-items** (depth=1) get `padding-left: 24px * depth` and bg `--bg-sunk`.

**Expanded item body** (`padding: 0 24px 16px calc(24px + 70px)`):

- Doc table — rows with: lock/doc icon · filename truncated · uppercase mono ext tag · `[external link]` `[download]` icon buttons. Subtle alternating: top border on each row.
- Summary section: "SUMMARY" eyebrow, version meta ("v2 · approved · May 12 18:42"), one-line in serif 15px italic, action row `[✎ Edit]` `[↻ Re-run]` ... `[Open in briefing]`.
- **Edit mode**: form replaces summary view. Title input + presenter input on row 1, then one-line input, then detailed textarea (5 rows, serif font, line-height 1.55). Actions: `[✓ Save changes]` (accent) `[Cancel]` ... muted "Saving creates v3 (approved)" hint.

---

### 3. Briefing Reader (`/briefing/:id`) — **HERO**

**Purpose:** Read the finalized briefing as a publishable document. Sticky TOC. Edit mode toggles to a markdown editor (not implemented in prototype — text-area swap).

**Layout** — page width 1180px max, padding 32px 48px 64px, 2-col grid:

```
[ TOC 220px ] [ gap 48px ] [ article max-width 720px ]
```

**Sticky TOC (`.b-toc`)** at `top: calc(var(--header-h) + 16px)`:

- "On this page" eyebrow
- Vertical list of section links, each with:
  - 1px left border (full height per item)
  - Active item: border-left `--accent`, ink color, 500 weight
  - Item number (mono 10.5px muted) + label
- Footer meta below border: reading time + model id

Scroll-spy: highlights the section whose top is ≤140px from viewport top. On click, smooth-scrolls the `.main` container to `el.offsetTop - 80`.

**Article structure:**

1. **Header**
   - Eyebrow: venue-tag + type-tag + "ISO New England · Holyoke, MA · Hybrid"
   - **Title** — Newsreader 400 · 44px · `text-wrap: balance`
   - **Headline** — Newsreader italic 22px ink (NOT ink-soft)
   - Meta row (geist mono 12px muted, 4 items): generated timestamp · word count · model · version
2. **Key takeaways block** (`.briefing-tldr`)
   - Card with `--bg-elev` bg, `border-left: 3px solid --accent`
   - "KEY TAKEAWAYS" eyebrow in accent
   - Ordered list of 4 takeaways, each: "01" mono num + serif 15.5px text, separated by `--border-soft` rules
3. **Intro paragraph** with **drop cap** — first letter `4.2em` float-left in `--accent`, padding `6px 10px 0 0`. Self-clearing after.
4. **Agenda-anchored sections** — for each agenda item that was summarized:
   - Section header: 50px accent num + h2 title + (optional vote pill below) + `[external]` icon link to Meeting page
   - Body — paragraphs, h3 subheadings, callouts, data tables (see below)
   - **Next steps** sub-card if present
5. **Decisions & next steps** roll-up table: Item · Decision · Outcome · Next, monoshorthands in col 1, green positive deltas
6. **Source documents** grid — 2-col, each item: ext badge in `--bg-sunk` + filename + "Item 3 · Capacity Accreditation…" + external icon
7. **Footer** — bottom border, left: "Generated by Poolside · {model} · {timestamp}", right: `[↻ Regenerate]` `[✎ Edit markdown]` `[✓ Approve & publish]`

**Body blocks** (rendered from a typed block array):

```js
{ kind: 'p',       text: '...' }
{ kind: 'h',       text: '...' }       // h3
{ kind: 'callout', label: 'Position', text: '...' }
{ kind: 'data',    title: '...', rows: [['Header'...], ['Row'...]] }
```

**Callout** (`.b-callout`) — 2-col grid `90px 1fr`, gap 16px, bg `--accent-tint`, accent left border 3px, radius 6px. Label mono caps accent 600. Body **italic serif 16px**.

**Data table** (`.b-table`) — full width, mono uppercase 10.5px headers in muted, body `--ink` with `--border-soft` row dividers. First column 500 weight. Numeric columns `.num` right-aligned. Last-column delta auto-colored: starts with `+` → `--success`, starts with `-` → `--danger`. Caption (figcaption) is mono caps uppercase 11px muted.

---

### 4. Add Meeting (`/add`)

**Purpose:** Pull new meetings into Poolside — either by scraping ISO calendars or by manual URL/upload.

**Layout:**

1. Topbar: "ADD MEETING" + `[✕ Cancel]`.
2. Page header — eyebrow "Pipeline · Ingest", title "Add meetings to Poolside.", subtitle explaining the two modes.
3. **Mode segmented control**: `[↻ Scrape calendars]` `[+ Add manually]`.

**Auto mode** — three vertical steps in a `220px 1fr` grid (step head left, body right). Each step has a numbered "01/02/03" bubble (active=accent, done=green check).

- **Step 1 — Sources**: 2-col grid of venue cards. Each card: checkbox + venue name + "7 active committees · last scraped 2 hours ago". Then a row of controls: Lookahead (`<select>` 30/60/90/180), Auto-ingest documents (segmented On/Off), Auto-parse agenda (segmented On/Off).
- **Step 2 — Preview**: header row "_ Venue Committee Date Source Status_" (mono caps muted, `--bg-sunk` bg). Below: scrape rows, grid `32px 70px 1fr 130px 110px 90px`. Checked rows get `--accent-tint` bg. "Already in database" rows are 0.55 opacity and disabled. New rows show a `NEW` badge (mono caps, accent bg, white text).
- **Step 3 — Ingest**: starts as a CTA card "Ready to ingest 3 meetings · ~270s · free" with a large `[▶ Run ingest]` accent button. After click, swaps to a **terminal-style log pane** (dark `#0c0d10` bg, mono 12.5px, max-height 320px, blinking accent cursor). Line types:
  - `→` step lines in `--accent-soft` warm tone
  - `  ` indented detail lines in muted
  - `✓` success lines in green bold
  - Live cursor `█` appended while running
  - When complete, swaps to a footer with `[Run another]` ... `[View in Overview →]`

**Manual mode** — single step "Manual add":
- Meeting URL input + helper text
- Row of 3 selects: Venue, Committee, Date
- Dropzone (dashed border): "drag PDFs / PPTX / DOCX here" + `[Browse files]`
- Submit row: `[Cancel]` ... `[▶ Ingest]` (accent)

**Recent ingest jobs** (below either mode) — table-style list inside a card. Grid `110px 1fr 180px 130px 80px`. Each row: mono job-id muted · label + meta · started timestamp mono · complete pill · `[Open →]` ghost button.

---

## Interactions & behavior

- **Hash routing** — `#/overview`, `#/meeting/:id`, `#/briefing/:id`, `#/add`. On navigation, the main scroll resets to top.
- **Sidebar active state** — left 2px accent bar inset 6px from top/bottom of the item; bg `--bg-elev`.
- **Breadcrumb clickability** — non-final crumbs are buttons styled as muted text, click navigates.
- **Briefing scroll-spy** — listens to `.main` scroll, computes which section's top is ≤140px from viewport top. TOC item highlights with a left-border in `--accent`.
- **Briefing TOC click** — smooth-scrolls `.main` to `target.offsetTop - 80`.
- **Agenda accordion** — clicking an item head toggles expansion. Headline item ("3" in mock) is open by default.
- **Meeting Detail → Edit mode** per agenda item — clicking `[✎ Edit]` swaps the summary block for the edit form. Form has its own draft state seeded from item values. Save calls back up; Cancel discards.
- **Add Meeting log animation** — lines append every 280ms via `setInterval`. When the last line lands, the panel transitions from "running" to "complete" state (~300ms).
- **Status pill colors** are conveyed by a 6px dot inside the pill, not by changing the pill background or border. Keep this — it's deliberate; the pills read as a calm legend, not as severity warnings.
- **Hover transitions** are 80–120ms ease for color/border, 100ms for transforms. Don't go longer — feels sluggish for an analyst tool.
- **No animation on initial load**. No motion on route change. Subtle hover only.

## State

State the developer needs to model (per screen):

- **Overview**: `view: 'list'|'card'`, `venueFilter`, `statusFilter`. Server data: meetings list.
- **Meeting**: `expandedAgendaItemIds: Set`, `editingItemId: number|null`, `showSummaryRunner: bool`, runner config (`briefingStyle`, `extractImages`, `forceRerun`). Server: meeting + agenda items + documents + per-item summaries.
- **Briefing**: `activeSection: string` (driven by scroll-spy), `editMode: bool`. Server: briefing markdown + sections + sources.
- **Add**: `mode: 'auto'|'manual'`, `selectedSources: Set<string>` (key = "venue|committee|date"), `running: bool`, `completed: bool`, `logLines: string[]`. Server: scraped preview list + recent ingest jobs.

## Backend wiring

The existing `pipeline/db_new.py` and `pipeline/auth.py` modules define the backend contract — the new front end should consume the same database via a thin REST/RPC layer. Suggested endpoints:

```
GET    /api/meetings?past_days=60&future_days=90       → Overview list
GET    /api/meetings/:id                               → Meeting detail header
GET    /api/meetings/:id/agenda                        → Agenda items + docs
GET    /api/meetings/:id/briefing                      → Briefing markdown + metadata
PATCH  /api/agenda-items/:id                          → Save metadata
PATCH  /api/agenda-items/:id/summary                  → Save manual summary (new version)
POST   /api/meetings/:id/summarize                     → Kick off summarization (returns job id)
GET    /api/jobs/:id/stream                            → SSE for live log lines
POST   /api/ingest/scrape                              → Returns preview list
POST   /api/ingest/run                                 → Returns job id
GET    /api/venues/calendars                           → Source list for Add Meeting
```

## Files in this handoff

| File                         | Purpose                                                |
| ---------------------------- | ------------------------------------------------------ |
| `index.html`                 | Prototype entrypoint — loads React via Babel inline    |
| `app.jsx`                    | Routing + tweaks panel wiring                          |
| `data.js`                    | Mock data (`MOCK_DATA.meetings`, `meeting101`, `briefing101`, `recentIngests`) — use these structures as your TypeScript types/zod schemas |
| `components.jsx`             | Sidebar, Topbar, Icon, Pill, Tag, IconBtn             |
| `screens/overview.jsx/.css`  | Overview screen                                        |
| `screens/meeting.jsx/.css`   | Meeting Detail screen                                  |
| `screens/briefing.jsx/.css`  | Briefing Reader screen                                 |
| `screens/add.jsx/.css`       | Add Meeting screen                                     |
| `styles.css`                 | Design tokens, base elements, sidebar, topbar, pills, buttons, inputs |
| `tweaks-panel.jsx`           | (Prototype-only — not part of the production app)      |

## Assets

No external images or icons. All icons are inline SVGs in `components.jsx` (`Icon` component) — a minimal line-icon set: calendar, list, doc, book, plus, search, edit, download, settings, library, check, x, chev-r/d, arrow-r/l, filter, lock, external, play, spark, refresh, tag, users, dot, globe. **Replace with the codebase's existing icon library** (Lucide, Heroicons, custom) rather than re-shipping these.

Fonts are loaded from Google Fonts — for a production app, **self-host** Newsreader, Geist, and Geist Mono (preload the WOFF2s, FOIT-disable with `font-display: swap`).

## Notes for the implementer

- The serif typography is load-bearing. **Don't substitute Inter or Roboto for the briefing body** — Newsreader is what makes the briefing feel like a document instead of a SaaS feed.
- The terracotta accent (`#c4633a`) is the only chromatic color — almost everything else is warm grays. Resist the urge to add gradients, secondary accents, or status-color backgrounds.
- The status pills use a **dot for color, not the pill** — visual calm. Keep this.
- The drop-cap, italic headline subtitle, italic one-line summaries, and `text-wrap: balance` on display titles are all editorial signals. Worth the effort.
- The TOC's full-height left-border-per-item pattern (vs. underlining the active item) is a deliberate Stripe Press / Linear-docs reference — gives the rail visual structure even when nothing is active.
- The Add Meeting log pane is **dark** even in Editorial — it reads as a terminal/build output, not as part of the editorial chrome. Don't try to harmonize it with the cream theme.
