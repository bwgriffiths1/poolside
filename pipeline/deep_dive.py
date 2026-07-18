"""
pipeline/deep_dive.py — Deep dive / special report generation.

Cross-meeting, document-centric analysis that produces a rich report with
generous tables and figures. Uses the same NEPOOL brand design as meeting
briefings but with a "Special Report" framing.

Pipeline:
  1. Collect selected documents (may span multiple meetings)
  2. Extract text and images for each document
  3. Build prompt from deep_dive_prompt.md
  4. Single multimodal LLM call with all document texts + images
  5. Post-process KEEP_IMAGE directives
  6. Store result in deep_dive_reports table
"""
from __future__ import annotations

import logging
from typing import Callable

import pipeline.db_new as db
from pipeline.summarizer import (
    get_text_for_doc,
    extract_and_store_images,
    call_llm_multimodal,
    replace_keep_images_inline,
    clean_output,
    load_prompt,
    load_model_config,
    make_client,
    HAIKU,
)

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = {
    "max_images": 20,
    "comparison_mode": True,
}


def run_deep_dive(
    report_id: int,
    client=None,
    progress_fn: Callable[[str], None] | None = None,
) -> bool:
    """
    Generate a deep dive report for the given report_id.

    Expects the deep_dive_reports row and deep_dive_documents rows to
    already exist in the DB. Returns True on success.
    """
    def _progress(msg: str) -> None:
        logger.info("Deep dive %d: %s", report_id, msg)
        if progress_fn:
            progress_fn(msg)

    report = db.get_deep_dive_report(report_id)
    if not report:
        logger.error("Deep dive report %d not found", report_id)
        return False

    config = {**_DEFAULT_CONFIG, **(report.get("config") or {})}
    max_images = config.get("max_images", 20)
    comparison_mode = config.get("comparison_mode", True)

    # Resolve model
    model_cfg = load_model_config()
    model = report.get("model_id") or model_cfg.get("meeting_model", HAIKU)

    if client is None:
        client = make_client()

    db.update_deep_dive_report(report_id, status="generating")
    _progress("Loading source documents...")

    # ── Collect documents and their content ──────────────────────────────
    docs = db.get_deep_dive_documents(report_id)
    if not docs:
        db.update_deep_dive_report(report_id, status="error",
                                   error_message="No documents linked to report")
        return False

    doc_blocks = []
    all_images: list[dict] = []
    label_idx = 1

    for i, doc in enumerate(docs):
        _progress(f"Extracting text from {doc['filename']} ({i+1}/{len(docs)})...")

        # Get text
        text = get_text_for_doc(doc)
        if not text:
            _progress(f"  ⚠ No text extracted from {doc['filename']}")
            continue

        # Build context header
        meeting_date = doc.get("meeting_date", "Unknown date")
        committee = doc.get("type_name") or doc.get("type_short", "")
        venue = doc.get("venue_short", "")
        header = (
            f"### Document {i+1}: {doc['filename']}\n"
            f"**Meeting:** {venue} {committee} — {meeting_date}\n"
        )
        doc_blocks.append(f"{header}\n{text}")

        # Extract images
        _progress(f"Extracting images from {doc['filename']}...")
        images = extract_and_store_images(doc)
        if not images:
            # Try fetching existing images from DB
            images = db.get_images_for_document(doc["id"])
        for img in images:
            img["_label_idx"] = label_idx
            img["doc_filename"] = doc["filename"]
            label_idx += 1
        all_images.extend(images)

    if not doc_blocks:
        db.update_deep_dive_report(report_id, status="error",
                                   error_message="No text could be extracted from any document")
        return False

    _progress(f"Collected {len(doc_blocks)} document(s), {len(all_images)} image(s)")

    # ── Build prompt ─────────────────────────────────────────────────────
    prompt_slug = report.get("prompt_slug") or "deep_dive_prompt"
    prompt_template = load_prompt(prompt_slug)
    if not prompt_template:
        db.update_deep_dive_report(report_id, status="error",
                                   error_message=f"Prompt template '{prompt_slug}' not found")
        return False

    # Build context block (document listing)
    doc_names = [d["filename"] for d in docs]
    meetings_involved = sorted(set(
        f"{d.get('venue_short', '')} {d.get('type_short', '')} {d.get('meeting_date', '')}"
        for d in docs
    ))
    context_block = (
        f"**Documents under analysis:** {', '.join(doc_names)}\n"
        f"**Meetings spanned:** {'; '.join(meetings_involved)}\n"
        f"**Comparison mode:** {'Cross-meeting comparison' if comparison_mode else 'Individual analysis'}\n"
    )

    documents_block = "\n\n---\n\n".join(doc_blocks)

    prompt = prompt_template.replace("{context_block}", context_block)
    prompt = prompt.replace("{documents_block}", documents_block)
    prompt = prompt.replace("{max_images}", str(max_images))

    # Prepend general context
    general_context = load_prompt("general_context_prompt")
    if general_context:
        prompt = general_context + "\n\n" + prompt

    _progress(f"Calling LLM ({model}) with {len(all_images)} images...")

    # ── LLM call ─────────────────────────────────────────────────────────
    try:
        result = call_llm_multimodal(
            client, model, prompt, all_images,
            max_tokens=32768,
            max_images=max_images,
            label=f"deep_dive_{report_id}",
        )
    except Exception as exc:
        db.update_deep_dive_report(report_id, status="error",
                                   error_message=str(exc))
        logger.error("Deep dive %d LLM call failed: %s", report_id, exc)
        return False

    _progress("Post-processing output...")

    # ── Post-process ─────────────────────────────────────────────────────
    result = replace_keep_images_inline(result, all_images, max_keep=max_images)
    result = clean_output(result)

    db.update_deep_dive_report(report_id, status="complete", report_md=result)
    _progress("Report complete!")
    return True
