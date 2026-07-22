"""FERC eLibrary docket endpoints.

A docket is a user-tracked FERC proceeding (pipeline/docket_ingest.py owns
the crawl; pipeline/docket_brief.py the state-of-play). Long work runs as
docket_jobs daemon threads (api/services/docket_jobs.py) polled from the
frontend, jobs.py-style.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from pipeline import db
from pipeline.docket_ingest import author_orgs, normalize_docket_number
from pipeline.ferc_client import FercClient, FercClientError, docinfo_url, filelist_url

from ..auth import current_user
from ..services import docket_jobs as jobs_service

log = logging.getLogger("poolside.dockets")

router = APIRouter(prefix="/api", tags=["dockets"])


def _iso(v: Any) -> Any:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


def _serialize_job(row: dict | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    if out.get("cost_usd") is not None:
        out["cost_usd"] = float(out["cost_usd"])
    for k in ("started_at", "finished_at"):
        out[k] = _iso(out.get(k))
    return out


def _docket_row(row: dict) -> dict[str, Any]:
    out = dict(row)
    for k in ("last_crawled_at", "created_at", "latest_filed_date",
              "brief_generated_at"):
        if k in out:
            out[k] = _iso(out[k])
    return out


def _filing_row(f: dict, files: list[dict]) -> dict[str, Any]:
    return {
        "id": f["id"],
        "accession_number": f["accession_number"],
        "category": f.get("category"),
        "document_class": f.get("document_class"),
        "document_type": f.get("document_type"),
        "description": f.get("description"),
        "sub_docket": f.get("sub_docket"),
        "filed_date": _iso(f.get("filed_date")),
        "issued_date": _iso(f.get("issued_date")),
        "posted_date": _iso(f.get("posted_date")),
        "comments_due_date": _iso(f.get("comments_due_date")),
        "response_due_date": _iso(f.get("response_due_date")),
        "ferc_cite": f.get("ferc_cite"),
        "filing_parties": f.get("filing_parties") or [],
        "treatment": f.get("treatment"),
        "is_docless": f.get("is_docless"),
        "summary_one_line": f.get("summary_one_line"),
        "summary_detailed": f.get("summary_detailed"),
        "summary_status": f.get("summary_status"),
        "elibrary_url": docinfo_url(f["accession_number"]),
        "filelist_url": filelist_url(f["accession_number"]),
        "files": [{
            "id": x["id"],
            "file_desc": x.get("file_desc"),
            "orig_file_name": x.get("orig_file_name"),
            "file_type": x.get("file_type"),
            "file_size": x.get("file_size"),
            "page_count": x.get("page_count"),
            "included": x.get("included"),
            "has_content": x.get("has_content"),
        } for x in files],
    }


def _intervenors(filings: list[dict]) -> list[dict]:
    """Deduped intervenor roster, chronological."""
    seen: set[str] = set()
    out: list[dict] = []
    for f in sorted(filings, key=lambda r: str(r.get("filed_date") or "")):
        if f.get("document_class") != "Intervention":
            continue
        for org in author_orgs(f.get("filing_parties")):
            if org not in seen:
                seen.add(org)
                out.append({"org": org, "date": _iso(f.get("filed_date"))})
    return out


def _brief_payload(docket_id: int) -> dict[str, Any] | None:
    """Current state-of-play version + staleness vs newest filing summary."""
    cur_sum = db.get_current_summary("docket", docket_id)
    if cur_sum is None:
        return None
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT MAX(sv.created_at) AS latest
                     FROM summary_versions sv
                     JOIN docket_filings df
                       ON df.id = sv.entity_id
                    WHERE sv.entity_type = 'docket_filing'
                      AND sv.status != 'superseded'
                      AND df.docket_id = %s""",
                (docket_id,),
            )
            latest_filing_summary = (cur.fetchone() or {}).get("latest")
    stale = bool(latest_filing_summary
                 and cur_sum.get("created_at")
                 and latest_filing_summary > cur_sum["created_at"])
    return {
        "summary_id": cur_sum["id"],
        "version": cur_sum.get("version"),
        "status": cur_sum.get("status"),
        "detailed": cur_sum.get("detailed"),
        "is_manual": cur_sum.get("is_manual"),
        "created_at": _iso(cur_sum.get("created_at")),
        "created_by": cur_sum.get("created_by"),
        "stale": stale,
    }


# ── CRUD ────────────────────────────────────────────────────────────────

class CreateDocketBody(BaseModel):
    docket_number: str
    title: str | None = None
    notes: str | None = None


@router.post("/dockets", status_code=202)
def create_docket(
    body: CreateDocketBody,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Track a docket and kick off its initial sync. Idempotent on the
    number: re-adding an existing docket returns it (and starts a sync
    only if none is running)."""
    try:
        number = normalize_docket_number(body.docket_number)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    docket = db.create_docket(
        number, title=body.title or None, notes=body.notes or None,
        created_by=user.get("email") or "system")
    job = jobs_service.start_docket_job(
        docket["id"], mode="sync", created_by=user.get("email") or "system")
    return {"docket": _docket_row(docket), "job": job}


@router.get("/dockets")
def list_dockets(_: dict = Depends(current_user)) -> list[dict[str, Any]]:
    return [_docket_row(r) for r in db.list_dockets()]


@router.get("/dockets/{docket_id}")
def get_docket(docket_id: int, _: dict = Depends(current_user)) -> dict[str, Any]:
    docket = db.get_docket(docket_id)
    if not docket:
        raise HTTPException(status_code=404, detail="Docket not found")
    filings = db.list_docket_filings(docket_id)
    files_by_filing: dict[int, list[dict]] = {}
    for x in db.list_docket_filing_files(docket_id):
        files_by_filing.setdefault(x["filing_id"], []).append(x)
    return {
        **_docket_row(docket),
        "brief": _brief_payload(docket_id),
        "filings": [_filing_row(f, files_by_filing.get(f["id"], []))
                    for f in filings],
        "intervenors": _intervenors(filings),
    }


class UpdateDocketBody(BaseModel):
    title: str | None = None
    notes: str | None = None
    auto_refresh: bool | None = None


@router.patch("/dockets/{docket_id}")
def update_docket(
    docket_id: int,
    body: UpdateDocketBody,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    if not db.get_docket(docket_id):
        raise HTTPException(status_code=404, detail="Docket not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields:
        db.update_docket(docket_id, **fields)
    return _docket_row(db.get_docket(docket_id))


@router.delete("/dockets/{docket_id}")
def delete_docket(docket_id: int, _: dict = Depends(current_user)) -> dict[str, Any]:
    if not db.delete_docket(docket_id):
        raise HTTPException(status_code=404, detail="Docket not found")
    return {"deleted": True}


# ── Jobs ────────────────────────────────────────────────────────────────

@router.post("/dockets/{docket_id}/sync", status_code=202)
def sync_docket(
    docket_id: int,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Crawl for new filings, summarize them, refresh the state of play."""
    job = jobs_service.start_docket_job(
        docket_id, mode="sync", created_by=user.get("email") or "system")
    if job is None:
        raise HTTPException(status_code=404, detail="Docket not found")
    return job


@router.post("/dockets/{docket_id}/state-of-play", status_code=202)
def generate_state_of_play(
    docket_id: int,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Regenerate the state of play alone (no crawl)."""
    job = jobs_service.start_docket_job(
        docket_id, mode="brief", created_by=user.get("email") or "system")
    if job is None:
        raise HTTPException(status_code=404, detail="Docket not found")
    return job


@router.get("/dockets/{docket_id}/active-job")
def get_active_job(
    docket_id: int,
    _: dict = Depends(current_user),
) -> dict[str, Any] | None:
    job_id = jobs_service.active_job_id(docket_id)
    return _serialize_job(jobs_service.get_job(job_id)) if job_id else None


@router.get("/docket-jobs/{job_id}")
def get_docket_job(job_id: int, _: dict = Depends(current_user)) -> dict[str, Any]:
    row = jobs_service.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(row)


@router.get("/dockets/{docket_id}/docx")
def download_docket_docx(
    docket_id: int,
    _: dict = Depends(current_user),
) -> Response:
    """The docket briefing as .docx: state of play + one page per
    substantive filing with eLibrary links. Rendered from the DB — instant."""
    from pipeline.docket_docx import generate_docket_docx_bytes

    try:
        data, filename = generate_docket_docx_bytes(docket_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(
        content=data,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_MIME_BY_EXT = {
    "pdf": "application/pdf",
    "docx": ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"),
    "txt": "text/plain",
}


@router.get("/dockets/files/{file_row_id}/download")
def download_filing_file(
    file_row_id: int,
    _: dict = Depends(current_user),
) -> Response:
    """Passthrough download of one eLibrary file.

    We deliberately store no bytes (only extracted text), so this re-fetches
    from FERC on each click — expect a 15-60s wait before the download
    starts; the client's retry budget rides out the origin's 520 streaks."""
    row = db.get_docket_filing_file(file_row_id)
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        data = FercClient().download_file(row["file_id"])
    except FercClientError as e:
        raise HTTPException(status_code=502,
                            detail=f"FERC download failed: {e}")
    ext = (row.get("file_type") or "pdf").lower()
    filename = row.get("orig_file_name") or f"{row['accession_number']}.{ext}"
    return Response(
        content=data,
        media_type=_MIME_BY_EXT.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/docket-jobs/{job_id}/cancel")
def cancel_docket_job(job_id: int, _: dict = Depends(current_user)) -> dict[str, Any]:
    row = jobs_service.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] in ("complete", "failed", "cancelled"):
        return {"job_id": job_id, "status": row["status"], "changed": False}
    changed = jobs_service.request_cancel(job_id)
    return {
        "job_id": job_id,
        "status": "cancelling" if changed else row["status"],
        "changed": changed,
    }
