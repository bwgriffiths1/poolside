"""Parse stored briefing Markdown into the typed block AST the frontend expects.

The current production briefings (see prompts/*_briefing_prompt.md) emit:

  ## Executive Summary / Highlights
  3-5 sentence intro...

  ---

  ## Agenda Item Summaries

  ### Item 3 — Capacity Accreditation Phase 2 Design
  Paragraphs of body text.

  **Next Steps**
  - Bullet A
  - Bullet B

No callout or pipe-table conventions are emitted today. This parser handles
what's there now: paragraphs, h3 subheadings inside sections, and Next Steps.
Add callout/data-table support to the prompts later, then extend this parser.
"""
from __future__ import annotations

import re
from typing import Any

from . import schemas


# Match section heads at either h2 or h3 level. Production prompts vary:
#   ### Item 3 — Title         (h3 + "Item" prefix + em-dash)
#   ### Item 3: Title          (h3 + "Item" prefix + colon — meeting 2 style)
#   ## 2 — Title               (h2 + bare number + em-dash — meeting 10 style)
#   ### Items 8–9: ...         (plural "Items", range)
# Capture the item id (e.g. "3", "1.A", "8-9") and the title.
_SECTION_HEAD = re.compile(
    r"^#{2,3}\s+(?:Items?\s+)?([\d\.A-Za-z\-–—]+)\s*[:—–\-]\s*(.+)$",
    re.IGNORECASE,
)
_H3 = re.compile(r"^###\s+(.+)$")
_H4 = re.compile(r"^####\s+(.+)$")
_NEXT_STEPS = re.compile(r"^(?:####\s+)?(?:\*\*)?Next Steps(?:\*\*)?:?\s*$", re.IGNORECASE)
_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _parse_pipe_row(line: str) -> list[str] | None:
    m = _TABLE_ROW.match(line)
    if not m:
        return None
    return [c.strip() for c in m.group(1).split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.match(r"^:?-+:?$", c) for c in cells) and len(cells) > 0


def parse_briefing_markdown(md: str, meta: dict[str, Any]) -> schemas.Briefing:
    """Parse a stored briefing markdown into the typed Briefing shape.

    `meta` carries db-side metadata: title, subtitle, headline, generated_at,
    model, word_count, reading_time.
    """
    lines = md.splitlines()
    i = 0
    n = len(lines)

    takeaways: list[str] = []  # from a dedicated "## Key Takeaways" section
    exec_blocks: list[dict[str, Any]] = []  # from "## Executive Summary"
    sections: list[schemas.BriefingSection] = []
    cur_section: dict[str, Any] | None = None
    cur_body: list[dict[str, Any]] = []
    cur_next: list[str] | None = None
    cur_top_id: str | None = None  # item_id of the most recent depth-0 section
    in_executive = False
    in_takeaways = False
    in_agenda = False

    def flush_section() -> None:
        nonlocal cur_section, cur_body, cur_next
        if cur_section is None:
            return
        sections.append(schemas.BriefingSection(
            id=cur_section["id"],
            kind="agenda",
            item_id=cur_section["item_id"],
            depth=cur_section.get("depth", 0),
            title=cur_section["title"],
            vote=cur_section.get("vote"),
            body=[_block_from_dict(b) for b in cur_body],
            next_steps=cur_next,
        ))
        cur_section = None
        cur_body = []
        cur_next = None

    def open_section(head_line: str, item_id: str, title: str) -> None:
        """Start a new agenda section, computing depth from heading level and
        item-id lineage. `## n — Title` is depth 0 (top-level item / group
        header); `### n.x — Title` nested under its matching `## n` parent is
        depth 1. An h3 with no matching parent falls back to depth 0 so older
        flat briefings are unchanged."""
        nonlocal cur_section, cur_body, cur_next, cur_top_id
        is_sub = head_line.startswith("### ")
        if is_sub and cur_top_id is not None and item_id.startswith(cur_top_id + "."):
            depth = 1
        else:
            depth = 0
            cur_top_id = item_id
        cur_section = {
            "id": _slug(f"item-{item_id}-{title}"),
            "item_id": item_id,
            "title": title,
            "depth": depth,
        }
        cur_body = []
        cur_next = None

    while i < n:
        line = lines[i].rstrip()

        # Section head match (h2 or h3 with numeric / item-id prefix) takes
        # precedence over the ## category-switch logic. Some briefings emit
        # each agenda item as `## 2 — Title` rather than `### Item 2: Title`,
        # which would otherwise be misread as a new top-level section.
        sec_match = _SECTION_HEAD.match(line) if (line.startswith("## ") or line.startswith("### ")) else None
        if sec_match:
            in_executive = False
            in_agenda = True
            if cur_section is not None:
                flush_section()
            open_section(line, sec_match.group(1), sec_match.group(2).strip())
            i += 1
            continue

        if line.startswith("## "):
            heading = line[3:].strip().lower()
            in_takeaways = "takeaway" in heading
            in_executive = "summary" in heading or "highlights" in heading
            in_agenda = "agenda" in heading
            if cur_section is not None:
                flush_section()
            i += 1
            continue

        # Dedicated "## Key Takeaways" section (prompts emit this as of
        # 2026-07): each bullet is one takeaway. Preferred over the
        # executive-summary scrape below, which stays as the fallback for
        # briefings stored before the prompt change.
        if in_takeaways:
            if line.startswith("- ") or line.startswith("* "):
                takeaways.append(line[2:].strip())
            i += 1
            continue

        if in_executive:
            # Capture the Executive Summary as rendered blocks. Bold standalone
            # lines (**Key Developments**) become sub-headings; contiguous
            # bullets collapse into one paragraph block; everything else is prose.
            stripped = line.strip()
            if not stripped or stripped == "---":
                i += 1
                continue
            if (stripped.startswith("**") and stripped.endswith("**")
                    and stripped.count("**") == 2):
                exec_blocks.append({"kind": "h", "text": stripped.strip("*").strip()})
                i += 1
                continue
            if stripped.startswith("- ") or stripped.startswith("* "):
                bullets: list[str] = []
                while i < n:
                    ls = lines[i].strip()
                    if ls.startswith("- ") or ls.startswith("* "):
                        bullets.append("• " + ls[2:].strip())
                        i += 1
                        continue
                    if not ls:
                        i += 1
                        break
                    break
                if bullets:
                    exec_blocks.append({"kind": "p", "text": "\n".join(bullets)})
                continue
            para = [stripped]
            i += 1
            while i < n:
                nx = lines[i].strip()
                if (not nx or nx.startswith("#") or nx.startswith("- ")
                        or nx.startswith("* ") or nx == "---"
                        or (nx.startswith("**") and nx.endswith("**") and nx.count("**") == 2)):
                    break
                para.append(nx)
                i += 1
            exec_blocks.append({"kind": "p", "text": " ".join(para).strip()})
            continue

        if in_agenda:
            m = _SECTION_HEAD.match(line)
            if m:
                if cur_section is not None:
                    flush_section()
                open_section(line, m.group(1), m.group(2).strip())
                i += 1
                continue

            if cur_section is not None:
                # Inside a section: collect paragraphs, h3, h4, bullets, next steps, tables.
                if _NEXT_STEPS.match(line):
                    cur_next = []
                    i += 1
                    while i < n:
                        ln = lines[i].rstrip()
                        if ln.startswith("- ") or ln.startswith("* "):
                            cur_next.append(ln[2:].strip())
                            i += 1
                            continue
                        if not ln.strip():
                            i += 1
                            continue
                        break
                    continue

                # h4 → sub-heading inside the section (also map to "h" block)
                h4m = _H4.match(line)
                if h4m:
                    cur_body.append({"kind": "h", "text": h4m.group(1).strip()})
                    i += 1
                    continue

                # h3 inside section (rare — most h3s are section heads)
                h3m = _H3.match(line)
                if h3m and not _SECTION_HEAD.match(line):
                    cur_body.append({"kind": "h", "text": h3m.group(1).strip()})
                    i += 1
                    continue

                # Pipe table
                trow = _parse_pipe_row(line)
                if trow is not None:
                    rows: list[list[str]] = [trow]
                    i += 1
                    while i < n:
                        nxt = _parse_pipe_row(lines[i].rstrip())
                        if nxt is None:
                            break
                        if not _is_separator_row(nxt):
                            rows.append(nxt)
                        i += 1
                    if len(rows) >= 2:
                        cur_body.append({"kind": "data", "title": "", "rows": rows})
                    continue

                # Bullet list — collect contiguous bullets into one paragraph block
                if line.lstrip().startswith("- ") or line.lstrip().startswith("* "):
                    bullets: list[str] = []
                    while i < n:
                        ln = lines[i]
                        stripped = ln.lstrip()
                        if stripped.startswith("- ") or stripped.startswith("* "):
                            bullets.append("• " + stripped[2:].rstrip())
                            i += 1
                            continue
                        if not ln.strip():
                            i += 1
                            break
                        break
                    if bullets:
                        cur_body.append({"kind": "p", "text": "\n".join(bullets)})
                    continue

                # Horizontal rule → stop the current section's paragraph
                if line.strip() == "---":
                    i += 1
                    continue

                # Paragraph (collect until blank line, h, table, bullets, or hr)
                if line.strip():
                    para = [line]
                    i += 1
                    while i < n:
                        nx = lines[i]
                        nxs = nx.lstrip()
                        if not nx.strip():
                            break
                        if nxs.startswith("#"):
                            break
                        if nxs.startswith("- ") or nxs.startswith("* "):
                            break
                        if nxs.startswith("|"):
                            break
                        if nx.strip() == "---":
                            break
                        para.append(nx.rstrip())
                        i += 1
                    cur_body.append({"kind": "p", "text": " ".join(para).strip()})
                    continue

        i += 1

    if cur_section is not None:
        flush_section()

    return schemas.Briefing(
        title=meta.get("title", ""),
        subtitle=meta.get("subtitle", ""),
        headline=meta.get("headline", ""),
        generated_at=meta.get("generated_at", ""),
        model=meta.get("model", ""),
        word_count=meta.get("word_count", _word_count(md)),
        reading_time=meta.get("reading_time", max(1, _word_count(md) // 250)),
        tldr=takeaways[:5],
        executive_summary=[_block_from_dict(b) for b in exec_blocks],
        sections=sections,
    )


def _word_count(md: str) -> int:
    return len(re.findall(r"\w+", md))


def _block_from_dict(d: dict[str, Any]) -> schemas.BriefingBlock:
    kind = d["kind"]
    if kind == "p":
        return schemas.BriefingBlockP(kind="p", text=d["text"])
    if kind == "h":
        return schemas.BriefingBlockH(kind="h", text=d["text"])
    if kind == "callout":
        return schemas.BriefingBlockCallout(kind="callout", label=d["label"], text=d["text"])
    if kind == "data":
        return schemas.BriefingBlockData(kind="data", title=d.get("title", ""), rows=d["rows"])
    raise ValueError(f"unknown block kind: {kind}")
