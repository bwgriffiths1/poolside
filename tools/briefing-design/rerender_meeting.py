#!/usr/bin/env python3
"""
rerender_meeting.py — re-render a real stored briefing through the new
editorial exporter, byte-for-byte the way the app's Download .docx route does
(api/routes/briefings.py::export_briefing_docx), including real chart images
resolved from the database.

    DATABASE_URL=postgresql://localhost/meeting_summaries \\
        python tools/briefing-design/rerender_meeting.py 104

Writes rerendered_<external_id>.docx next to this script.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/meeting_summaries")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline import db, venue_links                       # noqa: E402
from pipeline.briefing import render_briefing_docx          # noqa: E402
from api import adapters, briefing_parser                   # noqa: E402

HERE = Path(__file__).resolve().parent


def _resolve_meeting_id(arg: str) -> int:
    """Accept an internal id, or `ext:<external_id>` to look up by the stable
    ISO-NE event id (internal ids differ between local dev and prod)."""
    if arg.startswith("ext:"):
        ext = arg[4:]
        with db._conn() as c, c.cursor() as cur:
            cur.execute("select id from meetings where external_id=%s", (ext,))
            row = cur.fetchone()
        if not row:
            sys.exit(f"no meeting with external_id {ext}")
        return row[0]
    return int(arg)


def main() -> None:
    meeting_id = _resolve_meeting_id(sys.argv[1] if len(sys.argv) > 1 else "104")

    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        sys.exit(f"meeting {meeting_id} not found")
    summary = db.get_current_summary("meeting", meeting_id)
    if not summary or not summary.get("detailed"):
        sys.exit(f"meeting {meeting_id} has no briefing")

    # Same resolve + parse + attach path as export_briefing_docx.
    md = adapters.resolve_image_refs(summary["detailed"])
    briefing = briefing_parser.parse_briefing_markdown(md, {
        "title": meeting.get("type_name") or "Committee",
        "generated_at": str(summary.get("created_at", "")),
        "model": summary.get("model") or summary.get("created_by") or "",
    })
    adapters.attach_briefing_docs(briefing, meeting_id)

    venue_short = meeting.get("venue_short")
    data = render_briefing_docx(
        briefing,
        committee=meeting.get("type_name") or "Committee",
        meeting_dates=[str(meeting.get("meeting_date", ""))],
        materials_url=venue_links.materials_url(venue_short, meeting.get("external_id")),
        webex_url=venue_links.webex_url(venue_short),
    )

    ext = meeting.get("external_id") or meeting_id
    out = HERE / f"rerendered_{ext}.docx"
    out.write_bytes(data)
    print(f"✓ wrote {out.name}  ({len(data):,} bytes)")
    print(f"  {meeting.get('type_name')} · {meeting.get('meeting_date')}")
    print(f"  {len(briefing.tldr)} takeaways · "
          f"{len(briefing.executive_summary)} exec blocks · "
          f"{len(briefing.sections)} sections")


if __name__ == "__main__":
    main()
