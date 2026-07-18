"""
pipeline/npc_combined_parser.py — Parse NPC combined PDFs (initial, supplemental, composite).

NPC meetings distribute materials as combined PDFs that bundle multiple documents
(agenda, minutes, presentations, memos, consent agenda items, etc.) into one file.
All variants use PDF bookmarks/TOC entries to define section boundaries.

Three variants in the meeting lifecycle:
  - Initial:      ~2 weeks before meeting, bare-bones (agenda + a few early materials)
  - Supplemental: ~1 week before, more materials, some sections still TBA
  - Composite:    day before meeting, comprehensive, may have nested bookmarks

Uses PyMuPDF (fitz) for bookmark extraction and text extraction.
"""
import logging
import re
from dataclasses import dataclass

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CombinedSection:
    bookmark_title: str       # Raw bookmark text
    level: int                # Bookmark depth (1=top-level, 2=sub-section)
    start_page: int           # 0-indexed
    end_page: int             # 0-indexed, exclusive
    section_type: str         # "agenda", "minutes", "consent_agenda", "presentation",
                              # "memo", "report", "notice", "sub_document", "tba"
    item_number: str | None   # e.g., "1", "2A", "5", "7"
    clean_title: str          # Without number prefix or date suffix
    parent_number: str | None # For nested bookmarks: "2A" child → parent "2"
    text: str                 # Extracted text (empty for TBA)
    is_tba: bool

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page


# ---------------------------------------------------------------------------
# Regexes for section classification
# ---------------------------------------------------------------------------

# Item number prefix: "5-AWP Update" or "2A-OP-2A Memo"
_ITEM_NUM_RE = re.compile(r"^(\d+[A-Za-z]?)\s*[-–—]\s*")

# Date suffix on bookmark titles: "Apr 9 '26 NPC", "Mar 5 '26 NPC"
_DATE_SUFFIX_RE = re.compile(
    r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+"
    r"'\d{2}\s+NPC\s*$",
    re.IGNORECASE,
)

# Section type patterns (checked in order — first match wins)
_TBA_RE = re.compile(r"\bTBA\b", re.IGNORECASE)
_MINUTES_RE = re.compile(r"\b(?:Prelim(?:inary)?\s+)?Minutes\b", re.IGNORECASE)
_CONSENT_RE = re.compile(r"\bConsent\s+Agenda\b", re.IGNORECASE)
_AGENDA_RE = re.compile(r"\bAgenda\b", re.IGNORECASE)
_NOTICE_RE = re.compile(r"\bNotice\b", re.IGNORECASE)
_MEMO_RE = re.compile(r"\bMemo\b", re.IGNORECASE)
_REPORT_RE = re.compile(r"\bReport\b", re.IGNORECASE)
_UPDATE_RE = re.compile(r"\bUpdate\b", re.IGNORECASE)
_PRESENTATION_RE = re.compile(r"\bPresentation\b", re.IGNORECASE)
_MATERIALS_RE = re.compile(r"\bMaterials\b", re.IGNORECASE)

# NPC agenda item line: "1." or "2A." — number may be alone on its line
_AGENDA_ITEM_START_RE = re.compile(r"^\s*(\d+[A-Za-z]?)\.\s*(.*?)$")

# Time slot: "10:00 a.m." or "10:00 – 10:30"
_TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?|[–—-]\s*\d{1,2}:\d{2}(?:\s*(?:a|p)\.?m\.?)?))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Bookmark extraction
# ---------------------------------------------------------------------------

def extract_bookmarks(pdf_bytes: bytes) -> list[dict]:
    """
    Extract PDF bookmarks/TOC entries using PyMuPDF.

    Returns list of dicts: {"level": int, "title": str, "page": int, "is_placeholder": bool}
    Pages are 0-indexed. Bookmarks with page=-1 (no destination) are placeholders (TBA).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        toc = doc.get_toc()  # [[level, title, page_1indexed], ...]
        total_pages = len(doc)
    finally:
        doc.close()

    bookmarks = []
    for level, title, page_1 in toc:
        is_placeholder = page_1 < 1
        page_0 = max(0, page_1 - 1)
        bookmarks.append({
            "level": level,
            "title": title.strip(),
            "page": page_0,
            "is_placeholder": is_placeholder,
        })

    logger.info("Extracted %d bookmark(s) from PDF (%d pages)", len(bookmarks), total_pages)
    return bookmarks


# ---------------------------------------------------------------------------
# Page range computation
# ---------------------------------------------------------------------------

def compute_page_ranges(
    bookmarks: list[dict],
    total_pages: int,
) -> list[dict]:
    """
    Compute page ranges for each bookmark section.

    Each bookmark's section runs from its page to one page before the next
    non-placeholder bookmark at the same or higher level. Placeholder
    bookmarks (page=-1 / is_placeholder=True) get zero-length ranges.

    Augments each bookmark dict with: start_page, end_page (exclusive).
    """
    result = []
    for i, bm in enumerate(bookmarks):
        # Placeholder bookmarks have no real page destination
        if bm.get("is_placeholder"):
            result.append({**bm, "start_page": 0, "end_page": 0})
            continue

        start = bm["page"]
        level = bm["level"]

        # Find the next non-placeholder bookmark that bounds this section
        end = total_pages
        for j in range(i + 1, len(bookmarks)):
            next_bm = bookmarks[j]
            if next_bm.get("is_placeholder"):
                continue  # skip placeholders when computing boundaries
            if next_bm["level"] <= level:
                end = next_bm["page"]
                break

        if end < start:
            end = start

        result.append({
            **bm,
            "start_page": start,
            "end_page": end,
        })

    return result


# ---------------------------------------------------------------------------
# Section classification
# ---------------------------------------------------------------------------

def classify_section(
    bookmark_title: str,
    page_count: int,
    level: int,
) -> tuple[str, str | None, str, str | None]:
    """
    Classify a bookmark title into a section type.

    Returns (section_type, item_number, clean_title, parent_number).
    """
    title = bookmark_title

    # Extract item number prefix: "5-AWP Update" → ("5", "AWP Update")
    item_number = None
    parent_number = None
    m = _ITEM_NUM_RE.match(title)
    if m:
        item_number = m.group(1)
        title = title[m.end():]
        # For compound numbers like "2A", the parent is "2"
        parent_match = re.match(r"^(\d+)[A-Za-z]", item_number)
        if parent_match:
            parent_number = parent_match.group(1)

    # Clean title: remove date suffix
    clean_title = _DATE_SUFFIX_RE.sub("", title).strip()
    # Remove "TBA" markers for cleaner title
    clean_title_no_tba = _TBA_RE.sub("", clean_title).strip().strip("()")
    if clean_title_no_tba:
        clean_title = clean_title_no_tba

    # Determine section type
    is_tba = bool(_TBA_RE.search(bookmark_title)) or page_count == 0

    if is_tba:
        section_type = "tba"
    elif level >= 2:
        section_type = "sub_document"
    elif _MINUTES_RE.search(bookmark_title):
        section_type = "minutes"
    elif _CONSENT_RE.search(bookmark_title):
        section_type = "consent_agenda"
    elif _NOTICE_RE.search(bookmark_title):
        section_type = "notice"
    elif _AGENDA_RE.search(bookmark_title):
        section_type = "agenda"
    elif _MEMO_RE.search(bookmark_title):
        section_type = "memo"
    elif _REPORT_RE.search(bookmark_title):
        section_type = "report"
    elif _UPDATE_RE.search(bookmark_title):
        section_type = "presentation"
    elif _PRESENTATION_RE.search(bookmark_title):
        section_type = "presentation"
    elif _MATERIALS_RE.search(bookmark_title):
        section_type = "presentation"
    else:
        section_type = "document"

    return section_type, item_number, clean_title, parent_number


# ---------------------------------------------------------------------------
# Text extraction for a page range
# ---------------------------------------------------------------------------

def extract_section_text(
    pdf_bytes: bytes,
    start_page: int,
    end_page: int,
) -> str:
    """
    Extract text from pages [start_page, end_page) of a PDF.

    Returns formatted text with [Page N] headers, consistent with
    summarizer.extract_text_pdf().
    """
    if start_page >= end_page:
        return ""

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = []
        for i in range(start_page, min(end_page, len(doc))):
            text = doc[i].get_text()
            if text.strip():
                pages.append(f"[Page {i + 1}]\n{text.strip()}")
    finally:
        doc.close()

    return "\n\n".join(pages)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_combined_pdf(pdf_bytes: bytes) -> list[CombinedSection]:
    """
    Parse an NPC combined PDF (initial, supplemental, or composite) into
    classified sections using PDF bookmarks.

    Returns a list of CombinedSection dataclasses ordered by position in PDF.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    bookmarks = extract_bookmarks(pdf_bytes)
    if not bookmarks:
        logger.warning("No bookmarks found in PDF — cannot parse sections")
        return []

    ranged = compute_page_ranges(bookmarks, total_pages)
    sections: list[CombinedSection] = []

    for bm in ranged:
        page_count = bm["end_page"] - bm["start_page"]
        section_type, item_number, clean_title, parent_number = classify_section(
            bm["title"], page_count, bm["level"],
        )

        # Extract text for non-TBA sections
        text = ""
        if page_count > 0:
            text = extract_section_text(pdf_bytes, bm["start_page"], bm["end_page"])

        sections.append(CombinedSection(
            bookmark_title=bm["title"],
            level=bm["level"],
            start_page=bm["start_page"],
            end_page=bm["end_page"],
            section_type=section_type,
            item_number=item_number,
            clean_title=clean_title,
            parent_number=parent_number,
            text=text,
            is_tba=(section_type == "tba"),
        ))

    logger.info(
        "Parsed %d section(s): %s",
        len(sections),
        ", ".join(f"{s.item_number or '?'}={s.section_type}" for s in sections),
    )
    return sections


# ---------------------------------------------------------------------------
# Agenda parsing from the agenda section text
# ---------------------------------------------------------------------------

def parse_agenda_section(pdf_bytes: bytes, start_page: int, end_page: int) -> list[dict]:
    """
    Parse the agenda page(s) from a combined PDF into structured agenda items.

    NPC agendas use numbered items like "1.", "2.", "2A." where the text may
    continue across multiple lines. Each item starts with "To approve...",
    "To receive...", "To consider...", or similar.

    Returns list of dicts matching the standard agenda item format:
        {item_id, title, prefix, presenter, time_slot, notes}
    """
    text = extract_section_text(pdf_bytes, start_page, end_page)
    if not text.strip():
        return []

    lines = text.split("\n")

    # First pass: find item start lines and collect their text blocks
    raw_items: list[tuple[str, list[str]]] = []  # (item_id, [text_lines])
    current_id: str | None = None
    current_lines: list[str] = []

    for line in lines:
        # Skip [Page N] headers and blank lines
        if line.startswith("[Page ") or not line.strip():
            continue

        m = _AGENDA_ITEM_START_RE.match(line)
        if m:
            # Save previous item
            if current_id is not None:
                raw_items.append((current_id, current_lines))
            current_id = m.group(1)
            rest = m.group(2).strip()
            current_lines = [rest] if rest else []
        elif current_id is not None:
            # Continuation line — append to current item
            stripped = line.strip()
            if stripped:
                current_lines.append(stripped)

    # Don't forget the last item
    if current_id is not None:
        raw_items.append((current_id, current_lines))

    # Second pass: build structured items
    items: list[dict] = []
    for item_id, text_lines in raw_items:
        full_text = " ".join(text_lines).strip()
        if not full_text:
            continue

        # Truncate to first sentence or two for a concise title
        # NPC items start with "To approve...", "To receive...", etc.
        # Take up to the first period that ends a sentence
        title = _extract_title(full_text)

        items.append({
            "item_id": item_id,
            "title": title,
            "prefix": None,
            "auto_sub": False,
            "presenter": None,
            "org": None,
            "vote_status": None,
            "wmpp_id": None,
            "time_slot": None,
            "initiative_codes": [],
            "notes": None,
        })

    logger.info("Parsed %d agenda item(s) from agenda section", len(items))
    return items


def _extract_title(text: str) -> str:
    """Extract a concise title from an NPC agenda item's full text.

    Takes the first sentence (up to the first period followed by a space
    or end of string). If the first sentence is very long, truncates at
    a reasonable length.
    """
    # Find first sentence boundary (period followed by space or two spaces)
    m = re.search(r"\.\s{2,}|\.\s+[A-Z]", text)
    if m:
        title = text[:m.start() + 1]
    else:
        title = text

    # Truncate overly long titles
    if len(title) > 200:
        # Try to break at a clause boundary
        for sep in [";", ",", " – ", " — "]:
            idx = title.find(sep, 80)
            if idx != -1 and idx < 200:
                title = title[:idx]
                break
        else:
            title = title[:200].rsplit(" ", 1)[0] + "..."

    return title.strip().rstrip(".")


# ---------------------------------------------------------------------------
# Fallback: build agenda from bookmark structure
# ---------------------------------------------------------------------------

def build_agenda_from_sections(sections: list[CombinedSection]) -> list[dict]:
    """
    Synthesize an agenda from the bookmark structure when no dedicated
    agenda section is found or parseable.

    Uses level-1 sections with item numbers as top-level agenda items.
    Level-2 sub-document sections are NOT added as separate agenda items;
    they are documents that belong to their parent's agenda item.
    Skips notice, agenda, and minutes sections.
    """
    items: list[dict] = []

    for s in sections:
        # Skip non-content sections and sub-documents
        if s.section_type in ("notice", "agenda", "minutes", "sub_document"):
            continue
        if not s.item_number:
            continue

        notes = None
        if s.is_tba:
            notes = "TBA — materials not yet available."

        items.append({
            "item_id": s.item_number,
            "title": s.clean_title,
            "prefix": None,
            "auto_sub": False,
            "presenter": None,
            "org": None,
            "vote_status": None,
            "wmpp_id": None,
            "time_slot": None,
            "initiative_codes": [],
            "notes": notes,
        })

    logger.info("Built %d agenda item(s) from bookmark structure", len(items))
    return items
