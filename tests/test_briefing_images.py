"""Image embedding in the Word export.

Briefing markdown can reference an image three ways: the summarizer's raw
`<!-- image_id:N -->` marker, its web-resolved `![](/api/images/N)` form
(both backed by document_images), and `![](/api/editor-images/N)` for a
screenshot pasted into the editor (backed by editor_images).

The third form used to match none of the exporter's patterns, so a pasted
screenshot was neither embedded nor stripped — it survived into the document
as the literal text `![pasted](/api/editor-images/12)`. These tests pin all
three forms down to real embedded bytes.
"""
import base64
import io
import zipfile
import struct

import pytest

from api.briefing_parser import parse_briefing_markdown
from pipeline import briefing as briefing_mod
from pipeline.briefing import render_briefing_docx


def _png(width: int = 40, height: int = 30) -> bytes:
    """A minimal but structurally valid PNG of the requested dimensions.

    python-docx parses the IHDR to size the figure, so the header has to be
    real; the pixel data does not.
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        import zlib
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    import zlib
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def fake_images(monkeypatch):
    """Stub both image tables so no DB is needed.

    The two PNGs differ in size on purpose: OOXML dedupes byte-identical
    media parts, so identical stubs would collapse into one and hide a
    missing embed.
    """
    doc_png = _png(40, 30)
    editor_png = _png(50, 30)

    monkeypatch.setattr(
        briefing_mod,
        "_fetch_image_record",
        lambda image_id: {
            "id": image_id,
            "image_b64": base64.b64encode(doc_png).decode(),
            "description": f"Figure {image_id}",
        },
    )
    monkeypatch.setattr(
        briefing_mod,
        "_fetch_editor_image_record",
        lambda image_id: {
            "id": image_id,
            "mime_type": "image/png",
            "data": editor_png,
        },
    )
    return doc_png, editor_png


def _render(md: str) -> tuple[str, zipfile.ZipFile]:
    b = parse_briefing_markdown(md, {"title": "Markets Committee"})
    blob = render_briefing_docx(b, "Markets Committee", ["2025-11-04"])
    zf = zipfile.ZipFile(io.BytesIO(blob))
    return zf.read("word/document.xml").decode(), zf


def _media_files(zf: zipfile.ZipFile) -> list[str]:
    return [n for n in zf.namelist() if n.startswith("word/media/")]


BODY = """## 1 Market Rule Changes

Some framing text before the figure.

{ref}

And the discussion that followed.
"""


def test_pasted_screenshot_is_embedded_not_left_as_markdown(fake_images):
    """The reported bug: the export showed a stub instead of the photo."""
    xml, zf = _render(BODY.format(ref="![pasted](/api/editor-images/12)"))

    assert "/api/editor-images/12" not in xml, "image reference leaked as text"
    assert "![pasted]" not in xml
    assert len(_media_files(zf)) == 1, "screenshot bytes not embedded"


def test_document_figure_still_embeds(fake_images):
    xml, zf = _render(BODY.format(ref="![figure 7](/api/images/7)"))

    assert "/api/images/7" not in xml
    assert len(_media_files(zf)) == 1
    # The editorial redesign renders captions upper-cased for the label
    # feel (_embed_image_bytes: caption.upper()) — assert case-blind.
    assert "figure 7" in xml.lower(), "caption dropped"


def test_summarizer_marker_still_embeds(fake_images):
    xml, zf = _render(BODY.format(ref="<!-- image_id:9 -->"))

    assert "image_id:9" not in xml
    assert len(_media_files(zf)) == 1


def test_both_kinds_coexist_in_one_briefing(fake_images):
    md = """## 1 Market Rule Changes

![figure 7](/api/images/7)

![pasted](/api/editor-images/12)
"""
    xml, zf = _render(md)

    assert len(_media_files(zf)) == 2
    assert "/api/images/7" not in xml
    assert "/api/editor-images/12" not in xml


def test_surrounding_prose_survives(fake_images):
    """Consuming the reference must not eat the text around it."""
    xml, _ = _render(BODY.format(ref="![pasted](/api/editor-images/12)"))

    assert "Some framing text before the figure." in xml
    assert "And the discussion that followed." in xml


def test_missing_editor_image_degrades_quietly(monkeypatch):
    """A deleted row should drop the figure, not emit the raw markdown."""
    monkeypatch.setattr(
        briefing_mod, "_fetch_editor_image_record", lambda image_id: None
    )
    xml, zf = _render(BODY.format(ref="![pasted](/api/editor-images/404)"))

    assert "/api/editor-images/404" not in xml
    assert _media_files(zf) == []
    assert "And the discussion that followed." in xml


def test_storage_backed_figure_embeds(monkeypatch, tmp_path):
    """A row whose bytes moved to object storage (storage_key set, no
    image_b64) must embed via the real FS driver — the docx path's
    dual-read."""
    from pipeline import storage as storage_mod

    for var in ("POOLSIDE_STORAGE_BUCKET", "POOLSIDE_STORAGE_ACCESS_KEY",
                "POOLSIDE_STORAGE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("POOLSIDE_STORAGE_DIR", str(tmp_path))
    storage_mod._reset_for_tests()
    try:
        key = storage_mod.image_key(7, 3, 0)
        storage_mod.put_image(key, _png(40, 30))
        monkeypatch.setattr(
            briefing_mod,
            "_fetch_image_record",
            lambda image_id: {"id": image_id, "storage_key": key,
                              "description": "Offloaded figure"},
        )
        xml, zf = _render(BODY.format(ref="![figure 7](/api/images/7)"))
        assert len(_media_files(zf)) == 1, "storage-backed figure not embedded"
        assert "offloaded figure" in xml.lower()
    finally:
        storage_mod._reset_for_tests()


def test_tall_screenshot_is_capped_to_page_height(fake_images, monkeypatch):
    """A very tall screenshot must not run off the page."""
    tall = _png(width=40, height=400)
    monkeypatch.setattr(
        briefing_mod,
        "_fetch_editor_image_record",
        lambda image_id: {"id": image_id, "mime_type": "image/png", "data": tall},
    )
    xml, _ = _render(BODY.format(ref="![pasted](/api/editor-images/12)"))

    # <wp:extent cy="..."/> is the rendered height in EMU (914400 per inch).
    import re
    cy = [int(v) for v in re.findall(r'<wp:extent[^>]*cy="(\d+)"', xml)]
    assert cy, "no figure rendered"
    assert max(cy) <= briefing_mod._MAX_IMG_H * 914400 + 1
