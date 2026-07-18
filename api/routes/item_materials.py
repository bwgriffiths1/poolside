"""Manually attach materials to a specific agenda item.

Lets a user drop an overlooked but relevant document onto a section — either
by URL (a link to a memo hosted somewhere) or by uploading a file — so it
feeds that item's summary. The heavy lifting is already done elsewhere:

  - We create a `documents` row (flagged `manual`) and assign it to the item
    via the existing item_documents link.
  - We extract the document's text NOW and cache it in `documents.raw_content`,
    so `resummarize_agenda_item` (which reads get_documents_for_item →
    _get_text_for_doc) picks it up with no summarizer changes.
  - Uploaded bytes are retained in `document_files` so the file stays
    downloadable.

Text extraction reuses pipeline/summarizer's extractors for pdf/docx/pptx/zip
and reads txt/md/csv directly. Unsupported types still attach (and stay
downloadable / linkable) but carry no auto-extracted text — the response says
so, so the UI can warn that re-summarizing won't see their contents.
"""
from __future__ import annotations

import base64
import logging
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response

from pipeline import db
from pipeline import summarizer
from ..auth import current_user

log = logging.getLogger("poolside.item_materials")

router = APIRouter(tags=["item-materials"])

MAX_BYTES = 25 * 1024 * 1024  # 25 MB
# Types we can pull text from. pdf/docx/pptx/zip go through the summarizer's
# extractors; the plaintext family is read directly.
_PLAINTEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".log", ".text"}
_BINARY_EXTS = {".pdf", ".docx", ".pptx", ".zip"}


def _ext_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix


def _extract_text(raw: bytes, filename: str) -> str:
    """Best-effort plain-text extraction from file bytes. Returns "" when the
    type isn't extractable (never raises for that case)."""
    ext = _ext_of(filename)
    if ext in _PLAINTEXT_EXTS:
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return ""
    if ext in _BINARY_EXTS:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=True) as tmp:
            tmp.write(raw)
            tmp.flush()
            try:
                return summarizer.extract_text(Path(tmp.name)) or ""
            except Exception as e:
                log.warning("extract_text failed for %s: %s", filename, e)
                return ""
    return ""


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = Path(path).name if path else ""
    return name or "linked-document"


@router.post("/api/agenda-items/{item_id}/materials")
def add_material(
    item_id: int,
    body: dict[str, Any] = Body(...),
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    """Attach a document to an agenda item.

    Body (one of two modes):
      URL mode:    { "url": "https://…", "filename"?: "memo.pdf" }
      Upload mode: { "filename": "memo.pdf", "data_b64": "…", "mime_type"?: … }
    """
    item = db.get_agenda_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Agenda item not found")
    meeting_id = item["meeting_id"]

    url = (body.get("url") or "").strip()
    data_b64 = body.get("data_b64")

    # ---- URL mode -----------------------------------------------------------
    if url and not data_b64:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(status_code=400, detail="URL must be http(s)")
        filename = (body.get("filename") or "").strip() or _filename_from_url(url)
        ext = _ext_of(filename)

        raw_content = ""
        # Fetch once to (a) validate reachability and (b) cache text. A fetch
        # failure is non-fatal: we still attach the link, and the summarizer
        # will retry the source_url at summarize time.
        try:
            resp = requests.get(url, timeout=30, stream=True)
            resp.raise_for_status()
            chunks, total = [], 0
            for chunk in resp.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("linked file exceeds 25 MB")
                chunks.append(chunk)
            raw = b"".join(chunks)
            raw_content = _extract_text(raw, filename)
        except Exception as e:
            log.warning("URL fetch/extract failed for %s: %s", url, e)

        doc = db.add_manual_document(
            meeting_id=meeting_id,
            filename=filename,
            file_type=ext or None,
            source_url=url,
            raw_content=raw_content or None,
        )
        db.assign_document_to_item(item_id, doc["id"])
        return {
            "document": _doc_out(doc),
            "extracted_chars": len(raw_content),
            "summarizable": bool(raw_content),
        }

    # ---- Upload mode --------------------------------------------------------
    filename = (body.get("filename") or "").strip()
    mime_type = (body.get("mime_type") or "").strip() or "application/octet-stream"
    if not filename:
        raise HTTPException(status_code=400, detail="filename required")
    if not isinstance(data_b64, str) or not data_b64:
        raise HTTPException(status_code=400, detail="url or data_b64 required")

    if "," in data_b64 and data_b64.lstrip().startswith("data:"):
        data_b64 = data_b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(data_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad base64: {e}")
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (25 MB max)")

    ext = _ext_of(filename)
    raw_content = _extract_text(raw, filename)

    doc = db.add_manual_document(
        meeting_id=meeting_id,
        filename=filename,
        file_type=ext or None,
        source_url=None,
        raw_content=raw_content or None,
    )
    db.store_document_file(doc["id"], mime_type, raw)
    db.assign_document_to_item(item_id, doc["id"])
    return {
        "document": _doc_out(doc),
        "extracted_chars": len(raw_content),
        "summarizable": bool(raw_content),
    }


@router.get("/api/documents/{doc_id}/file")
def download_document_file(doc_id: int) -> Response:
    """Stream the retained bytes of an uploaded document."""
    row = db.get_document_file(doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No stored file for this document")
    doc = db.get_document(doc_id)
    filename = (doc or {}).get("filename") or f"document-{doc_id}"
    raw = bytes(row["data"]) if isinstance(row["data"], memoryview) else row["data"]
    return Response(
        content=raw,
        media_type=row["mime_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/api/documents/{doc_id}")
def delete_manual_document(
    doc_id: int,
    _: dict = Depends(current_user),
) -> dict[str, bool]:
    """Delete a manually-added document. Scraped documents are protected —
    use the assignment controls / danger zone for those."""
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.get("manual"):
        raise HTTPException(
            status_code=403,
            detail="Only manually-added documents can be deleted here.",
        )
    db.delete_document(doc_id)
    return {"deleted": True}


def _doc_out(doc: dict) -> dict[str, Any]:
    return {
        "id": doc["id"],
        "filename": doc.get("filename") or "",
        "type": (doc.get("file_type") or "").lstrip("."),
        "source_url": doc.get("source_url"),
        "manual": bool(doc.get("manual")),
    }
