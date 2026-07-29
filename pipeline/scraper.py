"""
scraper.py — ISO-NE calendar and event-detail scrapers.

Calendar pages (static HTML) → requests + BeautifulSoup
Event detail pages (JS-rendered) → Playwright page object passed in by caller
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ISO_NE_BASE = "https://www.iso-ne.com"

# ---------------------------------------------------------------------------
# Event metadata lookup (from .ics link + page <li> elements)
# ---------------------------------------------------------------------------

def fetch_event_metadata(event_id: str) -> dict | None:
    """
    Fetch meeting metadata for an ISO-NE event: dates, committee, location.

    Fetches the event detail page once, then:
      1. Finds the "Add to My Calendar" .ics link and parses it for
         DTSTART/DTEND/SUMMARY (dates + committee title).
      2. Scrapes the page's <li> elements for Location (and as fallback
         for date/committee if the .ics is unavailable).

    Returns:
        {"start_date": date, "end_date": date|None,
         "committee": str|None, "location": str|None}
        or None if metadata cannot be determined.
    """
    url = f"{ISO_NE_BASE}/event-details?eventId={event_id}"
    try:
        resp = requests.get(
            url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch event page for %s: %s", event_id, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # --- Strategy 1: parse the .ics linked under "Add to My Calendar" --------
    ics_meta = _find_and_parse_ics(soup)

    # --- Strategy 2: scrape the <li> elements for metadata -------------------
    li_meta = _scrape_li_metadata(soup)

    # Merge: prefer .ics for dates/committee (structured), page for location
    if not ics_meta and not li_meta:
        return None

    result = {
        "start_date": (ics_meta or {}).get("start_date")
                      or (li_meta or {}).get("start_date"),
        "end_date":   (ics_meta or {}).get("end_date")
                      or (li_meta or {}).get("end_date"),
        "committee":  (ics_meta or {}).get("committee")
                      or (li_meta or {}).get("committee"),
        # .ics location is usually generic; prefer the page-scraped value
        "location":   (li_meta or {}).get("location")
                      or (ics_meta or {}).get("location"),
    }

    if not result.get("start_date"):
        return None
    return result


def _find_and_parse_ics(soup: BeautifulSoup) -> dict | None:
    """Find the 'Add to My Calendar' .ics link on the page and parse it."""
    # The link text is "Add to My Calendar" and href ends in .ics
    ics_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".ics"):
            ics_link = href
            break

    if not ics_link:
        return None

    try:
        from icalendar import Calendar

        full_url = ics_link if ics_link.startswith("http") else ISO_NE_BASE + ics_link
        resp = requests.get(
            full_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
        )
        resp.raise_for_status()

        cal = Calendar.from_ical(resp.content)
        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")
            summary = str(component.get("SUMMARY", ""))
            location = str(component.get("LOCATION", ""))

            if not dtstart:
                continue

            start = dtstart.dt
            if isinstance(start, datetime):
                start = start.date()

            end = None
            if dtend:
                end_val = dtend.dt
                if isinstance(end_val, datetime):
                    end_val = end_val.date()
                if end_val > start:
                    end = end_val

            # Filter out generic location placeholders
            if location and "refer to" in location.lower():
                location = None

            return {
                "start_date": start,
                "end_date": end,
                "committee": summary.strip() or None,
                "location": location.strip() if location else None,
            }
    except Exception as exc:
        logger.warning("Failed to parse .ics: %s", exc)

    return None


def _scrape_li_metadata(soup: BeautifulSoup) -> dict | None:
    """Extract date, committee, and location from the page's <li> elements."""
    result: dict = {}

    for li in soup.find_all("li"):
        text = li.get_text(" ", strip=True)
        if len(text) > 300:
            continue

        # Date: "Date: Tue Mar 10, 2026 9:30AM - 4:30PM"
        if text.startswith("Date:") and "start_date" not in result:
            m = re.search(
                r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
                r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})",
                text,
            )
            if m:
                try:
                    result["start_date"] = datetime.strptime(
                        m.group(1), "%b %d, %Y"
                    ).date()
                    result["end_date"] = None
                except ValueError:
                    pass

        # Committee: "Committee(s): Markets Committee"
        if "Committee(s)" in text and "committee" not in result:
            cm = re.search(r"Committee\(s\)[:\s]+(.+)", text)
            if cm:
                result["committee"] = cm.group(1).strip()

        # Location: "Location: DoubleTree Hotel, Westborough, MA"
        if text.startswith("Location:") and "location" not in result:
            lm = re.search(r"Location[:\s]+(.+)", text)
            if lm:
                result["location"] = lm.group(1).strip()

    return result if result else None

# How many consecutive calendar-days of the same normalized title
# are allowed to be grouped as one multi-day meeting.
MAX_MEETING_SPAN_DAYS = 7


# ---------------------------------------------------------------------------
# Calendar scraper
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    """Strip parenthetical notes like '(VIRTUAL)' and collapse whitespace."""
    title = re.sub(r"\(.*?\)", "", title)
    return " ".join(title.split()).lower()


def _parse_date(text: str) -> Optional[date]:
    """
    Parse plain-text dates like 'April 14, 2026' or 'April 14, 2026  9:30AM'.
    Returns a date object, or None on failure.
    """
    text = text.strip()
    # Take the first line, then strip any trailing time portion (e.g. "10:00AM")
    date_part = re.split(r"\s+\d{1,2}:\d{2}", text.split("\n")[0])[0].strip().rstrip(",")
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_part, fmt).date()
        except ValueError:
            continue
    logger.warning("Could not parse date from: %r", text)
    return None


def scrape_calendar(committee: dict, lookahead_days: int) -> list[dict]:
    """
    Fetch the committee calendar page (static HTML) and return a list of
    grouped meeting dicts within the lookahead window.

    Each meeting dict:
        {
            "primary_event_id": str,
            "all_event_ids": [str, ...],
            "title": str,               # raw title of first day
            "committee_name": str,
            "committee_short": str,
            "dates": [date, ...],       # sorted
            "detail_urls": [str, ...],  # one per day
            "location": str,            # from first day
        }
    """
    url = committee["url"]
    logger.info("Fetching calendar: %s", url)

    resp = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
        timeout=30,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="results-table")
    if not table:
        logger.warning("No results-table found on %s", url)
        return []

    tbody = table.find("tbody")
    if not tbody:
        logger.warning("No tbody in results-table on %s", url)
        return []

    today = date.today()
    cutoff = today + timedelta(days=lookahead_days)
    # Keep recent-past rows so an in-progress multi-day meeting still groups
    # with its earlier days. Filtering strictly at `today` shifted the group's
    # first row — and therefore primary_event_id — forward each morning of a
    # multi-day meeting, which discovery then treated as a brand-new meeting.
    earliest = today - timedelta(days=MAX_MEETING_SPAN_DAYS)

    raw_rows = []
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue

        # --- title + eventId ---
        a_tag = tds[0].find("a", class_="doc-widget-doc-title")
        if not a_tag:
            continue
        title = a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        m = re.search(r"eventId=(\d+)", href)
        if not m:
            continue
        event_id = m.group(1)
        detail_url = urljoin(ISO_NE_BASE, href)

        # --- location ---
        loc_span = tds[0].find("span", class_="event-widget-location-info")
        location = loc_span.get_text(strip=True) if loc_span else ""
        location = re.sub(r"^Location:\s*", "", location)

        # --- date ---
        date_span = tds[1].find("span", class_="date-and-time")
        if not date_span:
            continue
        date_text = date_span.get_text(" ", strip=True)
        event_date = _parse_date(date_text)
        if event_date is None:
            continue

        # Apply lookahead filter (past-but-recent rows survive for grouping)
        if event_date < earliest or event_date > cutoff:
            continue

        raw_rows.append({
            "event_id": event_id,
            "title": title,
            "norm_title": _normalize_title(title),
            "detail_url": detail_url,
            "date": event_date,
            "location": location,
        })

    if not raw_rows:
        logger.info("No upcoming events found within %d days on %s", lookahead_days, url)
        return []

    meetings = group_calendar_rows(raw_rows, committee, today)

    logger.info(
        "Found %d meeting(s) for %s within %d-day window",
        len(meetings), committee["name"], lookahead_days,
    )
    return meetings


def group_calendar_rows(raw_rows: list[dict], committee: dict, today: date) -> list[dict]:
    """
    Group calendar rows (one per day per ISO event) into meeting dicts.

    Rows with the same normalized title within MAX_MEETING_SPAN_DAYS of the
    group's first date form one multi-day meeting. Rows may include recent
    past days; groups whose last day is already over are dropped, but a group
    still in progress keeps its past days — so primary_event_id stays the
    first day's event ID for the whole life of the meeting.
    """
    raw_rows = sorted(raw_rows, key=lambda r: r["date"])

    meetings = []
    used = set()

    for i, row in enumerate(raw_rows):
        if i in used:
            continue
        group = [row]
        used.add(i)

        for j in range(i + 1, len(raw_rows)):
            if j in used:
                continue
            candidate = raw_rows[j]
            # Same normalized title AND within MAX_MEETING_SPAN_DAYS of the group's first date
            if (
                candidate["norm_title"] == row["norm_title"]
                and (candidate["date"] - group[0]["date"]).days <= MAX_MEETING_SPAN_DAYS
            ):
                group.append(candidate)
                used.add(j)

        dates = [r["date"] for r in group]
        if dates[-1] < today:
            continue  # meeting already ended — not an upcoming meeting

        event_ids = [r["event_id"] for r in group]
        detail_urls = [r["detail_url"] for r in group]

        meetings.append({
            "primary_event_id": event_ids[0],
            "all_event_ids": event_ids,
            "title": group[0]["title"],
            "committee_name": committee["name"],
            "committee_short": committee["short"],
            "dates": dates,
            "detail_urls": detail_urls,
            "location": group[0]["location"],
        })

    return meetings


def folder_name_for_meeting(meeting: dict) -> str:
    """
    Build the folder name for a meeting, e.g.:
        'MC March 10-12 2026'   (multi-day)
        'MC April 14 2026'      (single-day)
    """
    short = meeting["committee_short"]
    dates = meeting["dates"]
    if len(dates) == 1:
        d = dates[0]
        return f"{short} {d.strftime('%B %-d %Y')}"
    else:
        first, last = dates[0], dates[-1]
        if first.month == last.month and first.year == last.year:
            return f"{short} {first.strftime('%B')} {first.day}-{last.day} {first.year}"
        else:
            return (
                f"{short} {first.strftime('%B %-d')}-"
                f"{last.strftime('%B %-d %Y')}"
            )


# ---------------------------------------------------------------------------
# Event detail scraper — JSON API (primary) + Playwright fallback
# ---------------------------------------------------------------------------

_DOCS_API_URL = "https://www.iso-ne.com/api/1/services/documents.json"
_API_ROWS = 200   # generous page size; paginate if total exceeds this


def fetch_event_docs(event_id: str, session: Optional[requests.Session] = None) -> list[dict]:
    """
    Fetch all document links for an ISO-NE event via the JSON API.

    `event_id` may be a bare numeric ID ("160113") or a full/partial URL
    containing eventId=NNNN — the numeric ID is extracted automatically.

    Uses `start`/`rows` pagination so meetings with many attachments are
    fully covered.  Returns [] on failure (caller should fall back to
    Playwright DOM scraping).

    Each returned dict: {filename, url}
    """
    # Accept full URLs or bare IDs — always extract just the numeric portion
    m = re.search(r"eventId=(\d+)", event_id)
    event_id = m.group(1) if m else re.sub(r"\D", "", event_id)
    if not event_id:
        logger.warning("fetch_event_docs: could not extract numeric event ID from %r", event_id)
        return []

    sess = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": f"{ISO_NE_BASE}/event-details?eventId={event_id}",
    }

    all_docs: list[dict] = []
    start = 0

    while True:
        params: list = [
            ("type", "doc"),
            ("type", "ceii"),
            ("crafterSite", "iso-ne"),
            ("searchable", "true"),
            ("includeVersions", "false"),
            ("events_key", event_id),
            ("q", "*"),
            ("source", "docLibraryWidget"),
            ("start", start),
            ("rows", _API_ROWS),
            ("sort", "normalized_document_title_s asc"),
        ]
        try:
            resp = sess.get(_DOCS_API_URL, params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Documents API failed for event %s: %s", event_id, exc)
            return []

        documents = data.get("documents") or []
        total = int(data.get("total") or 0)

        for doc in documents:
            # The API stores the download path in 'path' (e.g. /static-assets/documents/…)
            # 'file_o.item.value' holds the same path; 'url' does not exist.
            raw_path = (
                doc.get("path")
                or (doc.get("file_o") or {}).get("item", {}).get("value")
                or doc.get("url")
                or ""
            )
            if not raw_path:
                continue
            full_url = raw_path if raw_path.startswith("http") else ISO_NE_BASE + raw_path
            filename = full_url.split("/")[-1].split("?")[0]
            if filename:
                all_docs.append({"filename": filename, "url": full_url})

        start += len(documents)
        if not documents or start >= total:
            break

    logger.info(
        "API: found %d/%d document(s) for event %s", len(all_docs), total, event_id
    )
    return all_docs


async def scrape_event_detail(detail_url: str, page) -> list[dict]:
    """
    Return download links for all documents on an ISO-NE event detail page.

    Tries the JSON API first (fast, handles pagination).  Falls back to
    Playwright DOM scraping if the API returns nothing.

    Each returned dict: {filename, url}
    Caller is responsible for the Playwright browser/context lifecycle.
    """
    logger.info("Scraping event detail: %s", detail_url)

    # ── Primary: JSON API ────────────────────────────────────────────────────
    m = re.search(r"eventId=(\d+)", detail_url)
    if m:
        docs = fetch_event_docs(m.group(1))
        if docs:
            return docs
        logger.info("API returned nothing for %s — falling back to Playwright", detail_url)

    # ── Fallback: Playwright DOM scraping ────────────────────────────────────
    await page.goto(detail_url, wait_until="networkidle", timeout=30000)

    try:
        await page.wait_for_selector(
            'a[href*="/static-assets/documents/"]',
            timeout=8000,
        )
    except Exception:
        logger.info("No document links found on %s (materials not yet posted)", detail_url)
        return []

    links = await page.eval_on_selector_all(
        'a[href*="/static-assets/documents/"]',
        "els => els.map(e => e.href)",
    )

    seen_urls: set[str] = set()
    docs = []
    for href in links:
        if href in seen_urls:
            continue
        seen_urls.add(href)
        filename = href.split("/")[-1].split("?")[0]
        if filename:
            docs.append({"filename": filename, "url": href})

    logger.info("Playwright: found %d document link(s) on %s", len(docs), detail_url)
    return docs
