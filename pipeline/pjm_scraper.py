"""PJM committee-page scraper.

PJM committee pages (e.g. /committees-and-groups/cifp-rbp) are fully
server-rendered Sitecore HTML — no JSON API. Each page carries a
"Meeting Materials" section: per-year accordion containers whose
`h4.ui-accordion-header` headers read "M.D.YYYY - Title" and whose panels
hold `tr.meetingMaterial` rows (posted date, title, /-/media/ file link,
stable Sitecore media GUID). One accordion entry == one meeting; multi-day
meetings (e.g. CIFP 4.16 + 4.17) appear as separate entries with re-posted
materials and are deliberately kept separate.

HTTP shape follows pipeline/ferc_client.py: one Session (cookie
persistence), browser UA, paced + retried GETs. PJM serves plain requests
fine today, but the site sits behind Imperva-class fronting, so pacing and
a real UA are load-bearing.

Markup quirks the parser must survive (pinned by tests/fixtures/pjm_cifp_rbp.html):
  - each material row nests a duplicate <a> (the format badge) inside the
    title <a> — dedupe by href;
  - the materials section sits under an unclosed <link> tag, so selectors
    must target classes/ids directly, never walk fixed parent chains.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from pipeline.agenda_parser import item_id_to_prefix

logger = logging.getLogger(__name__)

PJM_BASE = "https://www.pjm.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_PACE_SECONDS = 2.0
_TIMEOUT = 60
_RETRIES = 4
_BACKOFF_BASE = 3.0  # 3s, 6s, 12s, 24s (capped at 30)


class PjmClient:
    """Paced, retried GET client for pjm.com (FercClient shape, GET-only)."""

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update(_HEADERS)
        self._last_request = 0.0

    def _pace(self) -> None:
        wait = self._last_request + _PACE_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, url: str) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            self._pace()
            try:
                resp = self.session.get(url, timeout=_TIMEOUT)
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code} from {url}")
                resp.raise_for_status()  # 4xx: no point retrying
                return resp
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status is not None and 400 <= status < 500:
                    raise
                last_exc = exc
            except requests.RequestException as exc:
                last_exc = exc
            backoff = min(_BACKOFF_BASE * (2 ** attempt), 30.0)
            logger.warning(
                "PJM GET failed (attempt %d/%d): %s — retrying in %.0fs",
                attempt + 1, _RETRIES, last_exc, backoff,
            )
            time.sleep(backoff)
        raise RuntimeError(f"PJM GET failed after {_RETRIES} attempts: {url}") from last_exc


def fetch_committee_page(url: str, client: PjmClient | None = None) -> str:
    """Fetch a PJM committee page and return its HTML."""
    client = client or PjmClient()
    return client.get(url).text


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------

# "4.16.2026 - Critical Issue Fast Path ..." (separator dash may be -, – or —,
# and PJM sometimes omits the surrounding spaces)
_HEADER_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*[-–—]*\s*(.*)$", re.DOTALL)

_DATE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")


def _parse_pjm_date(text: str) -> Optional[date]:
    """Parse PJM's M.D.YYYY date format ("4.16.2026")."""
    m = _DATE_RE.match(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def filename_from_media_url(url: str) -> str:
    """Last path segment of a /-/media/ URL, query stripped, unquoted, lowercased."""
    path = urlparse(url).path
    return unquote(path.rstrip("/").rsplit("/", 1)[-1]).lower()


def pjm_external_id(slug: str, meeting_date: date) -> str:
    """Namespaced external id, e.g. "pjm-cifp-rbp-20260416".

    The pjm- namespace is load-bearing: discovery's external-id lookup is
    global across venues, and ISO-NE ids are bare numerics.
    """
    return f"pjm-{slug}-{meeting_date:%Y%m%d}"


def _row_title_and_link(row) -> tuple[str, str | None]:
    """Title text + href from a tr.meetingMaterial row.

    The title anchor nests a duplicate anchor (the PDF/DOC badge) inside
    itself, so take the first /-/media/ anchor and prefer its direct text
    node over get_text() (which would append the badge label).
    """
    anchor = row.select_one('a[href*="/-/media/"]')
    if anchor is None:
        return "", None
    direct = anchor.find(string=True, recursive=False)
    title = (direct or "").strip()
    if not title:
        badge = anchor.find("i")
        badge_text = badge.get_text(strip=True) if badge else ""
        title = anchor.get_text(strip=True)
        if badge_text and title.endswith(badge_text):
            title = title[: -len(badge_text)].strip()
    return title, anchor.get("href")


def parse_committee_page(html: str, committee_url: str) -> dict:
    """Parse a PJM committee page into meetings + upcoming entries. Pure.

    Returns:
      {
        "meetings": [
          {"date": date, "title": str,
           "documents": [{"filename", "url", "title", "posted_date",
                          "media_id", "ext"}, ...]},
          ...
        ],
        "upcoming": [{"date": date, "title", "location", "time_text"}, ...],
      }
    """
    soup = BeautifulSoup(html, "html.parser")
    base = committee_url or PJM_BASE
    meetings: list[dict] = []

    for header in soup.find_all("h4", class_="ui-accordion-header"):
        m = _HEADER_RE.match(header.get_text(" ", strip=True))
        if not m:
            continue
        try:
            mdate = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        title = re.sub(r"\s+", " ", m.group(4)).strip()

        panel = header.find_next_sibling("div")
        documents: list[dict] = []
        seen_hrefs: set[str] = set()
        if panel is not None:
            for row in panel.select("tr.meetingMaterial"):
                doc_title, href = _row_title_and_link(row)
                if not href or href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                url = urljoin(base, href)
                date_span = row.select_one('span[id$="_date"]')
                posted = _parse_pjm_date(date_span.get_text(strip=True)) if date_span else None
                checkbox = row.select_one("input[data-media-id]")
                filename = filename_from_media_url(url)
                documents.append({
                    "filename": filename,
                    "url": url,
                    "title": doc_title or filename,
                    "posted_date": posted,
                    "media_id": checkbox.get("data-media-id") if checkbox else None,
                    "ext": ("." + filename.rsplit(".", 1)[-1]) if "." in filename else "",
                })
        meetings.append({"date": mdate, "title": title, "documents": documents})

    upcoming: list[dict] = []
    for row in soup.select("#upcomingMeetingTbl tr.grid-row"):
        name_el = row.select_one(".meetingName")
        date_el = row.select_one(".pjmGridCellDate")
        udate = _parse_pjm_date(date_el.get_text(strip=True)) if date_el else None
        if name_el is None or udate is None:
            continue
        loc_el = row.select_one(".meetingLocation")
        time_el = row.select_one(".pjmGridCellTime")
        upcoming.append({
            "date": udate,
            "title": name_el.get_text(strip=True),
            "location": loc_el.get_text(strip=True) if loc_el else "",
            "time_text": time_el.get_text(" ", strip=True) if time_el else "",
        })

    return {"meetings": meetings, "upcoming": upcoming}


# ---------------------------------------------------------------------------
# Doc → agenda-item mapping (deterministic)
# ---------------------------------------------------------------------------

# "20260416-item-03---cifp---rbp-draft-work-plan.pdf" → "3"
_ITEM_NO_RE = re.compile(r"(?:^|[-_ ])item[-_ ]*0*(\d+)", re.IGNORECASE)


def pjm_item_number_from_filename(filename: str) -> str | None:
    """Extract the agenda-item number from PJM's item-NN filename convention."""
    m = _ITEM_NO_RE.search(filename or "")
    return m.group(1) if m else None


def map_pjm_docs_to_agenda_items(doc_rows: list[dict], agenda_items: list[dict]) -> dict[str, list]:
    """Deterministic PJM doc→item mapping via the item-NN filename token.

    Same contract as agenda_parser.map_docs_to_agenda_items: buckets keyed
    by item PREFIX (e.g. "a03"), with unmatched rows under "other" for the
    LLM fallback pass.
    """
    by_number: dict[str, str] = {}
    for item in agenda_items:
        raw_id = str(item.get("item_id") or "").strip()
        if not raw_id:
            continue
        prefix = item.get("prefix") or item_id_to_prefix(raw_id)
        if not prefix:
            continue
        by_number[raw_id.lstrip("0") or "0"] = prefix

    buckets: dict[str, list] = {"other": []}
    for row in doc_rows:
        number = pjm_item_number_from_filename(row.get("filename", ""))
        prefix = by_number.get(number) if number else None
        if prefix:
            buckets.setdefault(prefix, []).append(row)
        else:
            buckets["other"].append(row)
    return buckets


# ---------------------------------------------------------------------------
# Refresh fetcher (pipeline/refresh.py venue registry contract)
# ---------------------------------------------------------------------------

def _committee_for_meeting(meeting: dict, config: dict) -> dict | None:
    type_short = (meeting.get("type_short") or "").upper()
    for committee in (config.get("pjm") or {}).get("committees", []):
        if (committee.get("short") or "").upper() == type_short:
            return committee
    return None


def fetch_docs_for_meeting(
    meeting: dict,
    config: dict,
    session: requests.Session | None = None,
) -> list[dict]:
    """Current document list for one PJM meeting: [{"filename", "url"}].

    Re-scrapes the committee page and returns the materials of the accordion
    entry whose date matches the meeting. Missing committee config or a
    date with no accordion entry yet (upcoming meeting) → [].
    """
    committee = _committee_for_meeting(meeting, config)
    if committee is None:
        logger.warning(
            "No pjm.committees config entry for type_short=%r — cannot refresh",
            meeting.get("type_short"),
        )
        return []

    meeting_date = meeting.get("meeting_date")
    html = fetch_committee_page(committee["url"], client=PjmClient(session=session))
    parsed = parse_committee_page(html, committee["url"])
    for entry in parsed["meetings"]:
        if entry["date"] == meeting_date:
            return [{"filename": d["filename"], "url": d["url"]} for d in entry["documents"]]
    return []
