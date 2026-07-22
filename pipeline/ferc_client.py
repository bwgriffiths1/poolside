"""FERC eLibrary API client.

Thin wrapper over the JSON API behind https://elibrary.ferc.gov — the same
endpoints the eLibrary Angular SPA calls (verified live 2026-07-22; endpoint
map cross-checked against github.com/4very/ferc-elibrary-api):

  POST Search/AdvancedSearch            docket listing, paged
  GET  Document/GetDocInfoFromP8/{acc}  full per-filing metadata
  GET  File/GetFileListFromP8/{acc}     per-file metadata + download GUIDs
  POST File/DownloadP8File              raw file bytes

Operational notes, learned the hard way:
  - The API is SLOW: 15-60s per call is normal. Callers must run inside a
    background job, never a request handler.
  - Cloudflare fronts the origin and throws transient 520s; plain curl gets
    black-holed entirely. `requests` with a browser-ish UA works reliably.
  - Filings are immutable once posted, so per-accession metadata only ever
    needs to be fetched once.
"""
from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://elibrary.ferc.gov/eLibrarywebapi/api"

# Full page size the SPA itself uses; totalHits drives the paging loop.
PAGE_SIZE = 100

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}

# Seconds between consecutive API calls — polite pacing for a federal site.
_PACE_SECONDS = 2.0
_TIMEOUT = 120
# FERC's origin 520s in streaks across ALL endpoints when it has a bad
# hour (observed live: search, metadata and downloads all affected, while
# eLibrary's own SPA just re-fires requests until one lands). Everything
# runs inside background jobs, so patience is cheap and correct.
_RETRIES = 5
_DOWNLOAD_RETRIES = 6  # downloads are the flakiest (largest payloads)
_BACKOFF_BASE = 5.0  # 5s, 10s, 20s, 40s, 60s (capped)


class FercClientError(Exception):
    """Raised when the eLibrary API keeps failing after retries."""


class FercClient:
    def __init__(self, session: requests.Session | None = None,
                 pace_seconds: float = _PACE_SECONDS):
        self.session = session or requests.Session()
        self.session.headers.update(_HEADERS)
        self.pace_seconds = pace_seconds
        self._last_call = 0.0

    # ── plumbing ─────────────────────────────────────────────────────────

    def _pace(self) -> None:
        wait = self._last_call + self.pace_seconds - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _request(self, method: str, path: str, *, json_body=None,
                 expect_json: bool = True, retries: int = _RETRIES):
        """One API call with pacing + retry/backoff on 5xx and transport
        errors. 4xx returns are surfaced immediately (they're deterministic)."""
        url = f"{BASE_URL}/{path}"
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._pace()
            try:
                resp = self.session.request(
                    method, url, json=json_body, timeout=_TIMEOUT)
                self._last_call = time.monotonic()
                if resp.status_code >= 500:
                    raise FercClientError(
                        f"HTTP {resp.status_code} from {path}")
                resp.raise_for_status()
                return resp.json() if expect_json else resp.content
            except (FercClientError, requests.RequestException) as exc:
                self._last_call = time.monotonic()
                last_exc = exc
                if attempt < retries - 1:
                    delay = min(_BACKOFF_BASE * (2 ** attempt), 60)
                    logger.warning("FERC %s attempt %d/%d failed (%s); "
                                   "retrying in %.0fs", path, attempt + 1,
                                   retries, exc, delay)
                    time.sleep(delay)
        raise FercClientError(f"{path} failed after {retries} attempts: {last_exc}")

    # ── endpoints ────────────────────────────────────────────────────────

    def search_docket(self, docket_number: str) -> list[dict]:
        """Every filing on a docket family (bare number matches all
        sub-dockets), as raw searchHit dicts. Pages through until
        totalHits is exhausted."""
        hits: list[dict] = []
        page = 1
        while True:
            payload = {
                "searchText": "*",
                "searchFullText": True,
                "searchDescription": True,
                "dateSearches": [],
                "docketSearches": [{"docketNumber": docket_number,
                                    "subDocketNumbers": []}],
                "resultsPerPage": PAGE_SIZE,
                "curPage": page,
                "sortBy": "",
                "groupBy": "NONE",
                "idolResultID": "",
                "allDates": False,
                "availability": None,
                "affiliations": [],
                "categories": [],
                "libraries": [],
                "accessionNumber": None,
                "eFiling": False,
                "classTypes": [],
            }
            data = self._request("POST", "Search/AdvancedSearch",
                                 json_body=payload)
            if not data.get("success", False):
                raise FercClientError(
                    f"AdvancedSearch errored for {docket_number}: "
                    f"{data.get('errorMessage')}")
            batch = data.get("searchHits") or []
            hits.extend(batch)
            total = int(data.get("totalHits") or 0)
            if len(hits) >= total or not batch:
                return hits
            page += 1

    def get_doc_info(self, accession_number: str) -> dict | None:
        """Full metadata for one accession (eLcAffiliation, eLcDocket,
        Comments_Due_Date, Ferc_Cite, ...). Returns the first DataList row."""
        data = self._request(
            "GET", f"Document/GetDocInfoFromP8/{accession_number}")
        rows = data.get("DataList") or []
        return rows[0] if rows else None

    def get_file_list(self, accession_number: str) -> list[dict]:
        """Per-file rows for one accession: ID (download GUID),
        FileDescription, Orig_File_Name, File_Size_Num, Page_Count, ..."""
        data = self._request(
            "GET", f"File/GetFileListFromP8/{accession_number}")
        return data.get("DataList") or []

    def download_file(self, file_id: str, accession_number: str = "",
                      file_type: str = "") -> bytes:
        """Raw bytes for one file.

        The payload mirrors the eLibrary SPA byte-for-byte: only fileidLst
        is populated. FERC's origin 520s intermittently on large files —
        their own UI just retries until one lands (observed 520,520,200 in
        the wild) — so downloads get a deeper retry budget than metadata
        calls. accession_number/file_type are accepted for call-site
        clarity but the API ignores them."""
        payload = {
            "FileType": "",
            "accession": "",
            "fileid": 0,
            "FileIDAll": "",
            "fileidLst": [file_id],
            "Islegacy": False,
        }
        return self._request("POST", "File/DownloadP8File",
                             json_body=payload, expect_json=False,
                             retries=_DOWNLOAD_RETRIES)


def docinfo_url(accession_number: str) -> str:
    """Public eLibrary page for a filing — where UI rows link out to."""
    return (f"https://elibrary.ferc.gov/eLibrary/docinfo?"
            f"accession_number={accession_number}")


def filelist_url(accession_number: str) -> str:
    """Public eLibrary file-list page for a filing."""
    return (f"https://elibrary.ferc.gov/eLibrary/filelist?"
            f"accession_number={accession_number}")
