"""Roundup report bodies live in summary_versions (entity_type='roundup').

Pins the storage contract introduced with the roundup editor:

  * run_monthly_roundup stores the LLM output as a summary version and
    approves it — it must NOT write report_md (legacy read-only column);
  * the summaries routes accept the 'roundup' entity so the full-page
    editor / version history / restore work on roundups;
  * the Word export renders the current body with the Key Takeaways
    treatment and the roundup cover/footer furniture.

No Postgres, no threads, no LLM — pipeline.db is faked per test.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest
from fastapi import HTTPException

import pipeline.roundup as roundup_mod
import pipeline.roundup_docx as roundup_docx_mod
import api.routes.summaries as summaries_routes

SAMPLE_ROUNDUP_MD = """## Key Takeaways

- MC advanced the capacity auction reform tariff language to a June vote.
- RC endorsed the related accreditation change 9–2.

## Executive Summary

The month belonged to capacity auction reform, which cleared two committees
in parallel and heads to the Participants Committee next.

## Cross-Committee Workstreams

### Capacity Auction Reform (CAR)

**Status: tariff language out for stakeholder comment; MC vote expected July.**

The design package moved at MC (Jun 10) and RC (Jun 17).

**Next:** MC vote July 8.

## Committee Roundup

### Markets Committee (MC) — Jun 10–11

One compact paragraph about the meeting.

## Looking Ahead

| Date | Committee/Venue | Item | Action |
|---|---|---|---|
| Jul 8 | MC | CAR tariff language | Vote |
"""


class RoundupRow(dict):
    """monthly_roundups row shape the pipeline functions expect."""


def _fake_row(**over) -> dict:
    row = {
        "id": 7,
        "venue_id": 1,
        "venue_short": "ISO-NE",
        "venue_name": "ISO New England",
        "month": date(2026, 6, 1),
        "status": "complete",
        "report_md": SAMPLE_ROUNDUP_MD,
        "model_id": "claude-opus-5",
        "created_by": "ben@example.com",
    }
    row.update(over)
    return row


# ── run_monthly_roundup stores a version, not report_md ─────────────────


class FakePipelineDB:
    def __init__(self):
        self.updates: list[dict] = []
        self.versions: list[dict] = []
        self.approved: list[int] = []

    def get_monthly_roundup(self, rid):
        return _fake_row(id=rid, status="draft")

    def update_monthly_roundup(self, rid, **fields):
        self.updates.append({"id": rid, **fields})

    def set_roundup_meetings(self, rid, ids):
        self.provenance = (rid, list(ids))

    def create_summary_version(self, **kw):
        row = {"id": 501, "version": len(self.versions) + 1, **kw}
        self.versions.append(row)
        return row

    def approve_summary_version(self, version_id):
        self.approved.append(version_id)


def test_run_monthly_roundup_stores_version(monkeypatch):
    fake = FakePipelineDB()
    monkeypatch.setattr(roundup_mod, "db", fake)
    monkeypatch.setattr(
        roundup_mod, "collect_roundup_inputs",
        lambda r: ([{"id": 12, "type_short": "MC", "meeting_date": "2026-06-10",
                     "detailed": "briefing body"}], None),
    )
    monkeypatch.setattr(roundup_mod, "build_roundup_prompt",
                        lambda r, b, p: "PROMPT")
    monkeypatch.setattr(roundup_mod, "load_model_config", lambda: {})
    monkeypatch.setattr(
        roundup_mod, "call_llm",
        lambda client, model, prompt, max_tokens, label: SAMPLE_ROUNDUP_MD,
    )

    ok = roundup_mod.run_monthly_roundup(7, client=object())
    assert ok is True

    # Stored as a summary version, then approved.
    assert len(fake.versions) == 1
    v = fake.versions[0]
    assert v["entity_type"] == "roundup"
    assert v["entity_id"] == 7
    assert v["detailed"] == SAMPLE_ROUNDUP_MD
    assert v["is_manual"] is False
    assert fake.approved == [501]

    # The completing row update carries telemetry; no update anywhere
    # writes report_md (legacy read-only column).
    completing = [u for u in fake.updates if u.get("status") == "complete"]
    assert len(completing) == 1
    assert completing[0]["cost_usd"] == 0.0
    assert not any("report_md" in u for u in fake.updates)


# ── summaries routes accept the roundup entity ──────────────────────────


class FakeRoutesDB:
    def __init__(self, exists=True):
        self.exists = exists
        self.saved = None

    def get_monthly_roundup(self, rid):
        return _fake_row(id=rid) if self.exists else None

    def get_current_summary(self, entity_type, entity_id):
        assert entity_type == "roundup"
        return {"one_line": None, "detailed": SAMPLE_ROUNDUP_MD,
                "version": 3, "status": "approved", "is_manual": True,
                "created_at": None, "created_by": "ben@example.com"}

    def save_manual_summary(self, **kw):
        self.saved = kw
        return {"id": 99, "version": 4, "status": "approved"}


def test_get_summary_roundup(monkeypatch):
    fake = FakeRoutesDB()
    monkeypatch.setattr(summaries_routes, "db", fake)
    out = summaries_routes.get_summary("roundup", 7)
    assert out["entity_type"] == "roundup"
    assert out["roundup_id"] == 7
    assert out["meeting_id"] is None and out["docket_id"] is None
    assert "ISO-NE Roundup — June 2026" == out["parent_label"]
    assert out["detailed"] == SAMPLE_ROUNDUP_MD


def test_get_summary_roundup_404(monkeypatch):
    monkeypatch.setattr(summaries_routes, "db", FakeRoutesDB(exists=False))
    with pytest.raises(HTTPException) as exc:
        summaries_routes.get_summary("roundup", 7)
    assert exc.value.status_code == 404


def test_save_summary_roundup(monkeypatch):
    fake = FakeRoutesDB()
    monkeypatch.setattr(summaries_routes, "db", fake)
    out = summaries_routes.save_summary(
        "roundup", 7, body={"detailed": "# Edited"},
        user={"email": "ben@example.com"},
    )
    assert out["is_manual"] is True
    assert fake.saved["entity_type"] == "roundup"
    assert fake.saved["entity_id"] == 7
    assert fake.saved["created_by"] == "ben@example.com"


# ── Word export ──────────────────────────────────────────────────────────


class FakeDocxDB:
    def __init__(self, body=SAMPLE_ROUNDUP_MD):
        self.body = body

    def get_monthly_roundup(self, rid):
        return _fake_row(id=rid, report_md=self.body)

    def get_roundup_meetings(self, rid):
        return [
            {"id": 12, "type_short": "MC", "type_name": "Markets Committee",
             "meeting_date": date(2026, 6, 10), "end_date": date(2026, 6, 11)},
            {"id": 13, "type_short": "RC", "type_name": "Reliability Committee",
             "meeting_date": date(2026, 6, 17), "end_date": None},
        ]


def _docx_text(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_roundup_docx_renders(monkeypatch):
    monkeypatch.setattr(roundup_docx_mod, "db", FakeDocxDB())
    blob, filename = roundup_docx_mod.generate_roundup_docx_bytes(7)
    assert filename == "Roundup_ISO-NE_2026-06.docx"
    xml = _docx_text(blob)
    # Cover + eyebrows + a workstream head + provenance chips.
    assert "June 2026" in xml
    assert "ISO-NE Monthly Roundup" in xml
    assert "KEY TAKEAWAYS" in xml
    assert "CROSS-COMMITTEE WORKSTREAMS" in xml
    assert "Capacity Auction Reform (CAR)" in xml
    assert "MC Jun 10–11" in xml and "RC Jun 17" in xml
    # The Looking Ahead table made it through as a Word table.
    assert "<w:tbl>" in xml
    # Takeaway rows render with the numbered-gutter treatment (01, 02).
    assert ">01<" in xml and ">02<" in xml


def test_roundup_docx_missing_body(monkeypatch):
    monkeypatch.setattr(roundup_docx_mod, "db", FakeDocxDB(body=""))
    with pytest.raises(ValueError):
        roundup_docx_mod.generate_roundup_docx_bytes(7)
