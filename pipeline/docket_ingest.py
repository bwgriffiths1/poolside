"""FERC docket crawl → enrich → classify → summarize (Level 1).

sync_docket() is the whole flow for one docket, run inside a docket_jobs
daemon thread (api/services/docket_jobs.py):

  1. Crawl    — page through AdvancedSearch, diff accessions vs the DB.
  2. Enrich   — GetDocInfoFromP8 + GetFileListFromP8 per NEW filing only
                (filings are immutable once posted, so this happens once).
  3. Classify — treatment tier by documentClass (config-driven map),
                doc-less detection, per-file include/exclude.
  4. Summarize — one LLM call per filing lacking a summary, from the
                extracted text of its included files. Text is cached in
                docket_filing_files.raw_content because a FERC re-fetch
                costs 40-60s per file.

The state-of-play rollup is NOT chained here — pipeline/docket_brief.py
owns it and the job service decides when to run it.
"""
from __future__ import annotations

import logging
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

import pipeline.db as db
from pipeline.ferc_client import FercClient
from pipeline.summarizer import (
    HAIKU,
    SONNET,
    call_llm,
    clean_output,
    extract_text,
    load_model_config,
    load_prompt,
    make_client,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Treatment tiers by FERC documentClass (full taxonomy:
# elibrary.ferc.gov/eLibrary/assets/class-type.pdf, Jan 2025).
#   full  — substantive summary, ferc_filing_model
#   brief — short summary, ferc_brief_model; ALSO the fallback for classes
#           not listed anywhere, so nothing is silently dropped
#   skip  — recorded in the timeline, never downloaded or summarized
# config.yaml `ferc.treatment_map` overrides per-tier lists wholesale.
DEFAULT_TREATMENT_MAP: dict[str, list[str]] = {
    "full": [
        "Application/Petition/Request",
        "Comments/Protest",
        "Order/Opinion",
        "ALJ Issuance",
        "Briefing/Arguments of Law",
        "Testimony",
        "FERC Report/Study",
    ],
    "brief": [
        "Pleading/Motion",
        "Report/Form",
        "Status Report",
        "Other Submittal",
        "Agreement/Understanding/Contract",
    ],
    "skip": [
        "Intervention",
        "Notice",
        "Transcript",
        "Drawing/Maps",
        "Exhibit",
        "Deposition Document",
        "Interrogatory/Data Request",
        "Applicant Correspondence",
        "FERC Correspondence With Applicant",
        "FERC Correspondence With Government Agencies",
        "Informational Correspondence",
        "Litigation Correspondence",
        "Subpoena",
        "Top Sheet",
        "Affirmation",
        "Approved Designation",
        "Court Related Documents",
        "FERC Comment",
        "FERC Memo",
    ],
}

# Which prompt a filing gets, by documentClass (fallback: ferc_motion_prompt,
# the generic brief-summary template).
_PROMPT_BY_CLASS = {
    "Application/Petition/Request": "ferc_filing_prompt",
    "Order/Opinion": "ferc_order_prompt",
    "ALJ Issuance": "ferc_order_prompt",
    "Comments/Protest": "ferc_comment_prompt",
    "Briefing/Arguments of Law": "ferc_comment_prompt",
    "Testimony": "ferc_comment_prompt",
}
_FALLBACK_PROMPT = "ferc_motion_prompt"

# Files never worth LLM tokens: tariff sheets and their redlines, plus the
# auto-generated tariff record. Matched against FileDescription.
_EXCLUDED_FILE_RE = re.compile(
    r"marked\s+tariff|clean\s+tariff|redline|ferc\s+generated\s+tariff",
    re.IGNORECASE,
)

# Extensions we can actually extract text from.
_EXTRACTABLE = {"pdf", "docx", "txt"}

# Context guards for the per-filing LLM call. A big FERC transmittal letter
# runs ~100-300k chars; five included files must still fit the model window.
_MAX_CHARS_PER_FILE = 150_000
_MAX_CHARS_PER_FILING = 400_000


def load_ferc_config() -> dict:
    """config.yaml `ferc:` section (DB-overridable via app_config), with the
    default treatment map merged under missing keys."""
    from pipeline import appconfig
    try:
        cfg = appconfig.get_config_key("ferc") or {}
    except Exception:
        cfg = {}
    tmap = {**DEFAULT_TREATMENT_MAP, **(cfg.get("treatment_map") or {})}
    return {**cfg, "treatment_map": tmap}


def normalize_docket_number(raw: str) -> str:
    """'er26-925 ' → 'ER26-925'. Loose validation only — FERC adds prefixes
    (docket-prefix.pdf lists ~60) so we never gate on a fixed list."""
    num = (raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}\d{2}-\d+(-\d+)?", num):
        raise ValueError(
            f"'{raw}' doesn't look like a FERC docket number (e.g. ER26-925)")
    return num


def classify_treatment(document_class: str | None, cfg: dict | None = None) -> str:
    tmap = (cfg or load_ferc_config())["treatment_map"]
    cls = (document_class or "").strip()
    for tier in ("full", "brief", "skip"):
        if cls in (tmap.get(tier) or []):
            return tier
    return "brief"


def is_docless(hit: dict) -> bool:
    """Doc-less submittals (mostly interventions) carry a description prefix
    and only a TXT stub transmittal."""
    desc = (hit.get("description") or "").strip().lower()
    if desc.startswith("(doc-less)"):
        return True
    transmittals = hit.get("transmittals") or []
    return bool(transmittals) and all(
        (t.get("fileType") or "").upper() == "TXT" for t in transmittals)


def file_included(file_desc: str | None) -> bool:
    return not _EXCLUDED_FILE_RE.search(file_desc or "")


def extract_parties(docinfo: dict | None, hit: dict | None = None) -> list[dict]:
    """AUTHOR + AGENT affiliations, deduped, order preserved. Falls back to
    the search hit's affiliations when docinfo is missing."""
    rows = []
    if docinfo:
        for a in docinfo.get("eLcAffiliation") or []:
            rows.append({"type": a.get("Correspondent_Type"),
                         "org": a.get("Affiliation_Organization")})
    elif hit:
        for a in hit.get("affiliations") or []:
            rows.append({"type": a.get("afType"),
                         "org": a.get("affiliation")})
    seen: set[tuple] = set()
    out = []
    for r in rows:
        if r["type"] not in ("AUTHOR", "AGENT") or not r["org"]:
            continue
        key = (r["type"], r["org"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def author_orgs(filing_parties) -> list[str]:
    """Display helper: the AUTHOR organizations off a filing_parties blob."""
    return [p["org"] for p in (filing_parties or []) if p.get("type") == "AUTHOR"]


def _parse_date(value) -> date | None:
    """FERC dates arrive as 'MM/DD/YYYY' (search) or ISO stamps (docinfo),
    with '0001-01-01' as their null sentinel."""
    if not value:
        return None
    s = str(value).strip()
    if s.startswith("0001"):
        return None
    for fmt in ("%m/%d/%Y",):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _first_class_type(hit: dict) -> tuple[str | None, str | None]:
    cts = hit.get("classTypes") or []
    if not cts:
        return None, None
    return cts[0].get("documentClass"), cts[0].get("documentType")


def _sub_docket(hit: dict) -> str | None:
    nums = hit.get("docketNumbers") or []
    return nums[0] if nums else None


def _ferc_cite(docinfo: dict | None) -> str | None:
    cites = (docinfo or {}).get("Ferc_Cite") or []
    if isinstance(cites, list):
        return ", ".join(str(c) for c in cites if c) or None
    return str(cites) or None


# ---------------------------------------------------------------------------
# Crawl + enrich
# ---------------------------------------------------------------------------

def ingest_new_filings(docket: dict, ferc: FercClient, cfg: dict,
                       progress=logger.info) -> tuple[list[dict], list[str]]:
    """Crawl the docket, store every accession we haven't seen, enriched
    with docinfo + filelist. Returns (new_filing_rows, errors)."""
    number = docket["docket_number"]
    progress(f"Searching eLibrary for {number}…")
    hits = ferc.search_docket(number)
    known = db.get_docket_accessions(docket["id"])
    new_hits = [h for h in hits
                if (h.get("acesssionNumber") or "").strip()
                and h["acesssionNumber"] not in known]
    progress(f"{len(hits)} filings on the docket; {len(new_hits)} new")

    new_rows: list[dict] = []
    errors: list[str] = []
    for i, hit in enumerate(new_hits, start=1):
        acc = hit["acesssionNumber"]
        progress(f"Fetching metadata {i}/{len(new_hits)}: {acc}")
        docless = is_docless(hit)
        docinfo = None
        files: list[dict] = []
        # Doc-less filings have nothing worth two extra 15-60s API calls;
        # the search hit already carries the party via affiliations.
        if not docless:
            try:
                docinfo = ferc.get_doc_info(acc)
            except Exception as exc:
                errors.append(f"{acc}: docinfo failed: {exc}")
            try:
                files = ferc.get_file_list(acc)
            except Exception as exc:
                errors.append(f"{acc}: filelist failed: {exc}")

        doc_class, doc_type = _first_class_type(hit)
        treatment = ("skip" if docless
                     else classify_treatment(doc_class, cfg))
        di = docinfo or {}
        filing = db.upsert_docket_filing(
            docket["id"], acc,
            category=hit.get("category"),
            document_class=doc_class,
            document_type=doc_type,
            description=hit.get("description"),
            sub_docket=_sub_docket(hit),
            filed_date=_parse_date(hit.get("filedDate")),
            issued_date=_parse_date(hit.get("issuedDate")),
            posted_date=_parse_date(hit.get("postedDate")),
            comments_due_date=_parse_date(di.get("Comments_Due_Date")),
            response_due_date=_parse_date(di.get("Response_Due_Date")),
            ferc_cite=_ferc_cite(docinfo),
            fed_reg_num=di.get("Fed_Reg_Num"),
            filing_parties=extract_parties(docinfo, hit),
            treatment=treatment,
            is_docless=docless,
            raw_hit=hit,
            raw_docinfo=docinfo,
        )
        # File rows: prefer the filelist (orig names, page counts, order);
        # fall back to the search hit's transmittals when it failed.
        if files:
            for f in files:
                ext = _ext_from_name(f.get("Orig_File_Name")) or \
                    _mime_ext(f.get("MimeType"))
                db.upsert_docket_filing_file(
                    filing["id"], f.get("ID"),
                    file_desc=f.get("FileDescription"),
                    orig_file_name=f.get("Orig_File_Name"),
                    file_type=ext,
                    file_size=f.get("File_Size_Num"),
                    page_count=f.get("Page_Count"),
                    file_list_order=f.get("File_List_Order"),
                    included=file_included(f.get("FileDescription")),
                )
        else:
            for order, t in enumerate(hit.get("transmittals") or [], start=1):
                db.upsert_docket_filing_file(
                    filing["id"], t.get("fileId"),
                    file_desc=t.get("fileDesc"),
                    orig_file_name=t.get("fileName"),
                    file_type=(t.get("fileType") or "").lower() or None,
                    file_size=t.get("fileSize"),
                    file_list_order=order,
                    included=file_included(t.get("fileDesc")),
                )
        new_rows.append(filing)
    return new_rows, errors


def _ext_from_name(name: str | None) -> str | None:
    if not name or "." not in name:
        return None
    return name.rsplit(".", 1)[1].lower()


def _mime_ext(mime: str | None) -> str | None:
    return {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "text/plain": "txt",
    }.get((mime or "").lower())


# ---------------------------------------------------------------------------
# Level 1 — per-filing summaries
# ---------------------------------------------------------------------------

def _extract_file_text(ferc: FercClient, filing: dict, file_row: dict) -> str:
    """Cached extracted text for one file, downloading on first touch."""
    if file_row.get("raw_content"):
        return file_row["raw_content"]
    ext = (file_row.get("file_type") or "pdf").lower()
    data = ferc.download_file(file_row["file_id"],
                             filing["accession_number"], file_type=ext)
    if ext == "txt":
        text = data.decode("utf-8", errors="replace")
    elif ext in ("pdf", "docx", "pptx"):
        with tempfile.NamedTemporaryFile(suffix=f".{ext}") as tmp:
            tmp.write(data)
            tmp.flush()
            text = extract_text(Path(tmp.name))
    else:
        raise ValueError(f"no extractor for .{ext}")
    text = text.strip()
    if text:
        db.set_filing_file_content(file_row["id"], text)
    return text


def _filing_meta_block(filing: dict) -> str:
    """Metadata header giving the LLM the registry context for the filing."""
    parties = author_orgs(filing.get("filing_parties"))
    agents = [p["org"] for p in (filing.get("filing_parties") or [])
              if p.get("type") == "AGENT"]
    bits = [
        f"Docket: {filing.get('sub_docket') or ''}",
        f"Accession: {filing['accession_number']}",
        f"Class/Type: {filing.get('document_class')} / {filing.get('document_type')}",
        f"Description: {filing.get('description')}",
        f"Filed: {filing.get('filed_date') or filing.get('issued_date') or '?'}",
    ]
    if parties:
        bits.append(f"Filing party(ies): {'; '.join(parties)}")
    if agents:
        bits.append(f"Agent(s)/counsel: {'; '.join(agents)}")
    if filing.get("ferc_cite"):
        bits.append(f"FERC cite: {filing['ferc_cite']}")
    if filing.get("comments_due_date"):
        bits.append(f"Comments due: {filing['comments_due_date']}")
    return "[FILING METADATA]\n" + "\n".join(bits)


def _split_tldr(text: str) -> tuple[str | None, str]:
    """Prompts open with 'TLDR: <one sentence>' — split it into one_line."""
    m = re.match(r"\s*\**TLDR:?\**\s*:?\s*(.+?)\s*\n+(.*)", text or "",
                 flags=re.DOTALL | re.IGNORECASE)
    if not m:
        return None, (text or "").strip()
    return m.group(1).strip(), m.group(2).strip()


def summarize_filing(filing: dict, ferc: FercClient, client, cfg: dict,
                     model_cfg: dict | None = None,
                     progress=logger.info) -> bool:
    """One LLM call for one filing from its included files' text. Writes a
    summary_versions(entity_type='docket_filing') draft. Returns True when
    a summary was created."""
    files = [f for f in db.list_filing_files(filing["id"], with_content=True)
             if f["included"]
             and (f.get("file_type") or "").lower() in _EXTRACTABLE]
    if not files:
        logger.info("Filing %s: no summarizable files",
                    filing["accession_number"])
        return False

    per_file_cap = int(cfg.get("max_chars_per_file") or _MAX_CHARS_PER_FILE)
    total_cap = int(cfg.get("max_chars_per_filing") or _MAX_CHARS_PER_FILING)

    parts: list[str] = []
    total = 0
    for f in files:
        if total >= total_cap:
            parts.append("…(further attachments omitted for length)")
            break
        try:
            text = _extract_file_text(ferc, filing, f)
        except Exception as exc:
            logger.warning("Filing %s file %s: extraction failed: %s",
                           filing["accession_number"],
                           f.get("orig_file_name"), exc)
            continue
        if not text:
            continue
        if len(text) > per_file_cap:
            text = text[:per_file_cap].rsplit("\n", 1)[0] + "\n…(truncated)"
        label = f.get("file_desc") or f.get("orig_file_name") or "file"
        parts.append(f"### [{label}]\n\n{text}")
        total += len(text)
    if not parts:
        logger.warning("Filing %s: all files yielded no text",
                       filing["accession_number"])
        return False

    slug = _PROMPT_BY_CLASS.get(filing.get("document_class") or "",
                                _FALLBACK_PROMPT)
    template = load_prompt(slug) or load_prompt(_FALLBACK_PROMPT)
    if not template:
        raise ValueError(f"Prompt template '{slug}' not found")

    body = _filing_meta_block(filing) + "\n\n---\n\n" + "\n\n---\n\n".join(parts)
    try:
        prompt = template.format(text=body)
    except (KeyError, IndexError):
        prompt = template + "\n\n---\n\n" + body
    ctx = load_prompt("general_context_prompt").strip()
    if ctx:
        prompt = ctx + "\n\n" + prompt

    mc = model_cfg or load_model_config()
    if filing.get("treatment") == "full":
        model = mc.get("ferc_filing_model") or mc.get("document_model", SONNET)
    else:
        model = mc.get("ferc_brief_model") or mc.get("item_model", HAIKU)
    max_tokens = int(mc.get("ferc_filing_max_tokens") or 16384)

    progress(f"Summarizing {filing['accession_number']} "
             f"({filing.get('document_class')}) with {model}…")
    raw = call_llm(client, model, prompt, max_tokens=max_tokens,
                   label=f"ferc {filing['accession_number']}")
    raw = clean_output(raw)
    if not raw.strip():
        raise ValueError("LLM returned an empty summary")
    one_line, detailed = _split_tldr(raw)

    db.create_summary_version(
        entity_type="docket_filing",
        entity_id=filing["id"],
        one_line=one_line,
        detailed=detailed,
        model_id=model,
        is_manual=False,
        status="draft",
        created_by="system",
    )
    return True


# ---------------------------------------------------------------------------
# Orchestration — the sync job body
# ---------------------------------------------------------------------------

def sync_docket(docket_id: int, progress=logger.info,
                client=None, ferc: FercClient | None = None,
                summarize: bool = True) -> dict:
    """Crawl + enrich + summarize one docket. Returns
    {"filings_found", "filings_summarized", "errors"} for the job row.

    `progress(msg)` is the job-service closure — it also raises the
    cancellation exception, so every loop here is cancel-responsive."""
    docket = db.get_docket(docket_id)
    if not docket:
        raise ValueError(f"Docket {docket_id} not found")
    cfg = load_ferc_config()
    ferc = ferc or FercClient()

    new_rows, errors = ingest_new_filings(docket, ferc, cfg, progress=progress)

    summarized = 0
    if summarize:
        pending = [f for f in db.list_docket_filings(docket_id)
                   if f["treatment"] != "skip"
                   and f.get("summary_status") in (None, "stub")]
        if pending:
            progress(f"Summarizing {len(pending)} filing(s)…")
            if client is None:
                client = make_client()
            model_cfg = load_model_config()
            # Oldest first so the timeline fills chronologically.
            for filing in sorted(
                    pending,
                    key=lambda f: str(f.get("filed_date")
                                      or f.get("issued_date") or "")):
                try:
                    if summarize_filing(filing, ferc, client, cfg,
                                        model_cfg=model_cfg,
                                        progress=progress):
                        summarized += 1
                except Exception as exc:
                    logger.exception("Filing %s failed",
                                     filing["accession_number"])
                    errors.append(f"{filing['accession_number']}: {exc}")

    db.touch_docket_crawled(docket_id)
    # Title fallback: the root filing's description, once we have one.
    if not docket.get("title") and new_rows:
        root = next((f for f in db.list_docket_filings(docket_id)
                     if f.get("document_class") == "Application/Petition/Request"),
                    None)
        if root and root.get("description"):
            db.update_docket(docket_id, title=root["description"][:300])

    return {"filings_found": len(new_rows),
            "filings_summarized": summarized,
            "errors": errors}


def check_for_new_filings(docket_id: int) -> int:
    """Scheduler helper: crawl + enrich WITHOUT summarizing (no LLM spend).
    Returns how many new filings landed. The caller raises the notification."""
    result = sync_docket(docket_id, summarize=False)
    return result["filings_found"]
