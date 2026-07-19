"""Deep dive endpoints — cross-meeting, document-centric special reports.

Wires the long-dormant pipeline/deep_dive.py runner to the web app. A report
is a hand-picked set of documents (possibly spanning meetings) synthesized by
one multimodal LLM call. Status lives on the deep_dive_reports row
(monthly_roundups pattern): the UI polls GET /api/deep-dives/{id} while
status == 'generating'. No jobs table.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from pipeline import db

from .. import adapters
from ..auth import current_user

log = logging.getLogger("poolside.deep_dives")

router = APIRouter(prefix="/api/deep-dives", tags=["deep-dives"])


def _iso(v: Any) -> Any:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


def _source_row(d: dict) -> dict[str, Any]:
    return {
        "document_id": d["id"],
        "filename": d.get("filename"),
        "file_type": d.get("file_type"),
        "meeting_id": d.get("meeting_id"),
        "meeting_date": _iso(d.get("meeting_date")),
        "end_date": _iso(d.get("end_date")),
        "meeting_title": d.get("meeting_title"),
        "type_short": d.get("type_short"),
        "type_name": d.get("type_name"),
        "venue": d.get("venue_short"),
    }


def _report_row(row: dict, *, sources: list[dict] | None = None,
                with_body: bool = True) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "title": row.get("title"),
        "status": row.get("status"),
        "model_id": row.get("model_id"),
        "config": row.get("config") or {},
        "error_message": row.get("error_message"),
        "created_by": row.get("created_by"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if with_body:
        out["report_md"] = (
            adapters.resolve_image_refs(row.get("report_md") or "") or None
        )
    if sources is not None:
        out["sources"] = [_source_row(d) for d in sources]
        out["source_count"] = len(sources)
    return out


def _run_deep_dive_job(report_id: int) -> None:
    """Daemon-thread entry point. run_deep_dive owns status transitions;
    this wrapper only catches catastrophic failures (import errors etc.)."""
    try:
        from pipeline.deep_dive import run_deep_dive

        run_deep_dive(report_id)
    except Exception as e:  # pragma: no cover — belt and braces
        log.exception("deep dive job %s crashed: %s", report_id, e)
        try:
            db.update_deep_dive_report(report_id, status="error",
                                       error_message=str(e))
        except Exception:
            log.exception("failed to record crash for deep dive %s", report_id)


def _claim_and_spawn(report_id: int) -> None:
    """Admission-guarded thread launch; a no-op when a live claim holds."""
    if db.claim_deep_dive_report(report_id) is None:
        return
    threading.Thread(
        target=_run_deep_dive_job,
        args=(report_id,),
        name=f"deep-dive-{report_id}",
        daemon=True,
    ).start()


@router.get("")
def list_reports() -> list[dict[str, Any]]:
    """Newest-first report list, bodies omitted, source counts attached."""
    rows = db.list_deep_dive_reports()
    out = []
    for r in rows:
        sources = db.get_deep_dive_documents(r["id"])
        out.append(_report_row(r, sources=sources, with_body=False))
    return out


@router.get("/{report_id}")
def get_report(report_id: int) -> dict[str, Any]:
    row = db.get_deep_dive_report(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Deep dive not found")
    sources = db.get_deep_dive_documents(report_id)
    return _report_row(row, sources=sources)


class CreateDeepDiveBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    document_ids: list[int] = Field(min_length=1)
    max_images: int = Field(default=20, ge=0, le=40)
    comparison_mode: bool = True


@router.post("", status_code=202)
def create_report(
    body: CreateDeepDiveBody = Body(...),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Create the report + document links and kick off generation.
    Poll GET /api/deep-dives/{id} while status == 'generating'."""
    # Validate the documents exist before creating anything; a typo'd id
    # otherwise surfaces minutes later as a mid-run failure.
    missing = [d for d in body.document_ids if db.get_document(d) is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown document id(s): {missing}",
        )

    created_by = (user.get("email") if isinstance(user, dict) else None) or "unknown"
    row = db.create_deep_dive_report(
        title=body.title.strip(),
        config={"max_images": body.max_images,
                "comparison_mode": body.comparison_mode},
        created_by=created_by,
    )
    for seq, doc_id in enumerate(body.document_ids):
        db.add_deep_dive_document(row["id"], doc_id, seq=seq)

    _claim_and_spawn(row["id"])

    fresh = db.get_deep_dive_report(row["id"]) or row
    sources = db.get_deep_dive_documents(row["id"])
    return _report_row(fresh, sources=sources)


@router.post("/{report_id}/rerun", status_code=202)
def rerun_report(report_id: int) -> dict[str, Any]:
    """Regenerate in place. Re-posting for an in-flight report returns the
    running row untouched (claim guard)."""
    row = db.get_deep_dive_report(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Deep dive not found")
    if not db.get_deep_dive_documents(report_id):
        raise HTTPException(status_code=400,
                            detail="No documents linked to this report")

    _claim_and_spawn(report_id)

    fresh = db.get_deep_dive_report(report_id) or row
    sources = db.get_deep_dive_documents(report_id)
    return _report_row(fresh, sources=sources)


@router.delete("/{report_id}")
def delete_report(report_id: int) -> dict[str, Any]:
    row = db.get_deep_dive_report(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Deep dive not found")
    db.delete_deep_dive_report(report_id)
    return {"deleted": True, "report_id": report_id}
