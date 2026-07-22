#!/usr/bin/env python3
"""
demo.py — render the sample briefing to a .docx through the REAL exporter.

Runs fully offline: it parses `sample_briefing.md` with the same
`api.briefing_parser.parse_briefing_markdown` the app uses, then calls
`pipeline.briefing.render_briefing_docx`. No database, no ANTHROPIC_API_KEY,
no network. The sample deliberately contains no inline images, so the image
code paths (which need the DB) are never touched.

This is the token → .docx bridge: once you've dialled the design in with the
tuner (index.html) and pasted the tokens into `pipeline/brand.py`, re-run this
to see the change in an actual Word document.

    python tools/briefing-design/demo.py            # writes demo.docx
    python tools/briefing-design/demo.py --open      # also try to open it

If LibreOffice (`soffice`) is on PATH it will also emit demo.pdf + page PNGs
for a quick look; otherwise it prints how to view demo.docx.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]           # tools/briefing-design -> tools -> repo root
sys.path.insert(0, str(REPO_ROOT))

from api.briefing_parser import parse_briefing_markdown   # noqa: E402
from pipeline.briefing import render_briefing_docx         # noqa: E402


# Metadata the app would normally pull from the DB — supplied by hand here.
META = {
    "title": "Markets Committee",
    "subtitle": "NEPOOL",
    "headline": "Winter 2026 retrospective and the next step in CAR-SA seasonal accreditation.",
    "generated_at": "July 21, 2026",
    "model": "claude-opus-4-8",
}
COMMITTEE = "Markets Committee"
MEETING_DATES = ["2026-07-21"]
MATERIALS_URL = "https://www.iso-ne.com/event-details?eventId=163991"
WEBEX_URL = "https://iso-newengland.webex.com/webappng/sites/iso-newengland/meeting/home"


def build_docx() -> Path:
    md = (HERE / "sample_briefing.md").read_text(encoding="utf-8")
    briefing = parse_briefing_markdown(md, META)
    data = render_briefing_docx(
        briefing,
        COMMITTEE,
        MEETING_DATES,
        materials_url=MATERIALS_URL,
        webex_url=WEBEX_URL,
    )
    out = HERE / "demo.docx"
    out.write_bytes(data)
    print(f"✓ wrote {out.relative_to(REPO_ROOT)}  ({len(data):,} bytes)")
    print(
        f"  parsed: {len(briefing.tldr)} takeaways · "
        f"{len(briefing.executive_summary)} exec blocks · "
        f"{len(briefing.sections)} sections"
    )
    return out


def try_render(docx_path: Path) -> None:
    """Best-effort: convert to PDF + page PNGs if LibreOffice is installed."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        print(
            "\nNo LibreOffice on PATH — open demo.docx in Word/Pages to view, or\n"
            "  brew install --cask libreoffice\n"
            "to enable automatic PDF + PNG rendering here."
        )
        return
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(HERE), str(docx_path)],
        check=True,
    )
    pdf = docx_path.with_suffix(".pdf")
    print(f"✓ wrote {pdf.relative_to(REPO_ROOT)}")
    if shutil.which("pdftoppm"):
        subprocess.run(
            ["pdftoppm", "-png", "-r", "130", str(pdf), str(HERE / "demo-page")],
            check=True,
        )
        pages = sorted(HERE.glob("demo-page-*.png"))
        print(f"✓ wrote {len(pages)} page image(s): {', '.join(p.name for p in pages)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--open", action="store_true", help="open demo.docx after building")
    args = ap.parse_args()

    docx_path = build_docx()
    try_render(docx_path)

    if args.open:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(docx_path)], check=False)


if __name__ == "__main__":
    main()
