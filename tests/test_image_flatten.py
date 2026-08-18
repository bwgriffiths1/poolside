"""Transparent slide/PDF graphics must land on WHITE, not black.

Two failure modes, both observed on real briefing figures:

  * PDF images carry transparency in a separate soft mask; extract_image
    returns the raw base stream, so the transparent void fills BLACK
    (white-boxes-on-black flowcharts). The extractor must rebuild the
    pixmap with its mask.
  * Genuinely alpha-carrying images (PPTX PNGs) pass through to the
    reader / docx / multimodal call, where dark line art over undefined
    background is illegible. _img_to_png_bytes must flatten onto white.
"""
import io

import fitz
import pytest
from PIL import Image

import pipeline.summarizer as summarizer


def _rgba_png_bytes(size=300, box=(100, 100, 200, 200)) -> bytes:
    """Transparent canvas with an opaque dark square in the middle —
    the shape of typical slide line-art."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            img.putpixel((x, y), (20, 20, 20, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_flatten_rgba_onto_white():
    out, w, h = summarizer._img_to_png_bytes(_rgba_png_bytes())
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB"
    assert img.getpixel((5, 5)) == (255, 255, 255)      # was transparent
    assert img.getpixel((150, 150)) == (20, 20, 20)     # content preserved


def test_flatten_palette_transparency_onto_white():
    src = Image.open(io.BytesIO(_rgba_png_bytes())).convert(
        "P", palette=Image.ADAPTIVE)
    buf = io.BytesIO()
    src.save(buf, format="PNG", transparency=0)
    out, _, _ = summarizer._img_to_png_bytes(buf.getvalue())
    assert Image.open(io.BytesIO(out)).mode == "RGB"


def test_opaque_rgb_unchanged():
    src = Image.new("RGB", (250, 250), (10, 200, 30))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    out, w, h = summarizer._img_to_png_bytes(buf.getvalue())
    img = Image.open(io.BytesIO(out))
    assert (w, h) == (250, 250)
    assert img.getpixel((5, 5)) == (10, 200, 30)


def test_pdf_smask_extraction_survives_with_white_background(tmp_path):
    # Inserting an alpha PNG into a PDF makes pymupdf split the alpha into a
    # soft mask — exactly the structure that used to extract black-filled.
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.insert_image(fitz.Rect(50, 50, 350, 350), stream=_rgba_png_bytes())
    pdf_path = tmp_path / "smask.pdf"
    doc.save(str(pdf_path))
    doc.close()

    images = summarizer._extract_images_pdf_unlocked(pdf_path, min_px=50)
    assert images, "embedded image not extracted"
    out, _, _ = summarizer._img_to_png_bytes(images[0]["image_bytes"])
    img = Image.open(io.BytesIO(out)).convert("RGB")
    corner = img.getpixel((5, 5))
    center = img.getpixel((img.width // 2, img.height // 2))
    assert corner == (255, 255, 255), f"transparent area came out {corner}, not white"
    assert center == (20, 20, 20), f"content area came out {center}"
