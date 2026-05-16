"""
v2_pages/meetings.py — Meeting browser with document reassignment.
"""
import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import pipeline.db_new as db
from pipeline.agenda_parser import item_id_to_prefix
from pipeline.summarizer import make_client, run_meeting_summarization

st.set_page_config(page_title="Meetings", layout="wide")
st.title("Meetings")


def _render_summary_with_images(text: str) -> None:
    """
    Render summary markdown, replacing <!-- image_id:N --> comments
    with the actual image from the database.
    """
    import base64 as _b64
    import re as _re

    parts = _re.split(r"(<!-- image_id:\d+ -->)", text)
    for part in parts:
        m = _re.match(r"<!-- image_id:(\d+) -->", part)
        if m:
            img_id = int(m.group(1))
            with db._conn() as conn:
                with db._cursor(conn) as cur:
                    cur.execute(
                        "SELECT image_b64, description, filename FROM document_images WHERE id = %s",
                        (img_id,),
                    )
                    row = cur.fetchone()
            if row and row.get("image_b64"):
                img_bytes = _b64.b64decode(row["image_b64"])
                st.image(img_bytes, use_container_width=True)
        elif part.strip():
            st.markdown(part)

# ---------------------------------------------------------------------------
# Jump-to pre-selection (from Overview page)
# ---------------------------------------------------------------------------
_jump_id = st.session_state.pop("_jump_to_meeting_id", None)
if _jump_id:
    _all = db.list_meetings(limit=2000)
    _jm  = next((m for m in _all if m["id"] == _jump_id), None)
    if _jm:
        st.session_state["_sel_venue"] = _jm["venue_short"]
        st.session_state["_sel_type"]  = _jm["type_short"]
        st.session_state["_sel_mid"]   = _jump_id

# ---------------------------------------------------------------------------
# Cascading selectors: Venue → Meeting Type → Date
# ---------------------------------------------------------------------------
venues = db.get_venues()
venue_shorts = [v["short_name"] for v in venues]

col_v, col_t, col_d = st.columns(3)

with col_v:
    sel_venue = st.selectbox("Venue", options=venue_shorts, key="_sel_venue")

with col_t:
    types    = db.get_meeting_types(venue_short_name=sel_venue)
    type_map = {t["short_name"]: t["name"] for t in types}
    if not type_map:
        st.info("No meeting types for this venue.")
        st.stop()
    sel_type = st.selectbox(
        "Meeting Type",
        options=list(type_map.keys()),
        format_func=lambda k: type_map[k],
        key="_sel_type",
    )

with col_d:
    # All meetings for this venue+type, newest first
    type_meetings = db.list_meetings(venue_short=sel_venue, type_short=sel_type, limit=500)
    type_meetings  = sorted(type_meetings, key=lambda m: m["meeting_date"], reverse=True)
    if not type_meetings:
        st.info("No meetings found.")
        st.stop()

    mid_options   = [m["id"] for m in type_meetings]
    mid_labels    = {m["id"]: str(m["meeting_date"]) for m in type_meetings}

    # If a pre-selected id is valid for the current type, honour it
    _pre = st.session_state.get("_sel_mid")
    _default_idx = mid_options.index(_pre) if _pre in mid_options else 0

    selected_id = st.selectbox(
        "Date",
        options=mid_options,
        index=_default_idx,
        format_func=lambda mid: mid_labels[mid],
        key="_sel_mid",
    )

selected_meeting = next(m for m in type_meetings if m["id"] == selected_id)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item_label(item: dict) -> str:
    prefix = f"{item['item_id']}  " if item.get("item_id") else ""
    return f"{prefix}{item['title']}"


def _doc_row(doc: dict, meeting_id: int, item_options: dict,
             key_prefix: str, edit_mode: bool = False) -> None:
    """Render one document row. Move/Unassign controls only shown in edit mode."""
    icon = "🔒" if doc["ceii_skipped"] else "📄"
    fname = doc["filename"]
    link = f"{icon} [{fname}]({doc['source_url']})" if doc.get("source_url") else f"{icon} {fname}"

    if not edit_mode:
        st.markdown(link)
        return

    col_name, col_move, col_unassign = st.columns([5, 1, 1])
    col_name.markdown(link)

    if col_move.button("↗ Move", key=f"{key_prefix}_move_{doc['id']}", use_container_width=True):
        st.session_state[f"moving_{doc['id']}"] = True

    if col_unassign.button("✕ Unassign", key=f"{key_prefix}_unassign_{doc['id']}", use_container_width=True):
        db.unassign_document(doc["id"], meeting_id)
        st.rerun()

    if st.session_state.get(f"moving_{doc['id']}"):
        move_col, ok_col, cancel_col = st.columns([4, 1, 1])
        with move_col:
            target = st.selectbox(
                "Move to",
                options=list(item_options.keys()),
                format_func=lambda k: item_options[k],
                key=f"{key_prefix}_target_{doc['id']}",
                label_visibility="collapsed",
            )
        if ok_col.button("✓", key=f"{key_prefix}_ok_{doc['id']}", use_container_width=True,
                         disabled=target is None):
            db.reassign_document(doc["id"], target, meeting_id)
            del st.session_state[f"moving_{doc['id']}"]
            st.rerun()
        if cancel_col.button("✕", key=f"{key_prefix}_cancel_{doc['id']}", use_container_width=True):
            del st.session_state[f"moving_{doc['id']}"]
            st.rerun()


# ---------------------------------------------------------------------------
# Load data for this meeting
# ---------------------------------------------------------------------------
agenda_items = db.get_agenda_items(selected_id)

# Build item options dict used by all reassign dropdowns: {db_id: display_label}
item_options: dict[int, str] = {}
for it in agenda_items:
    indent = "  " * it["depth"]
    item_options[it["id"]] = f"{indent}{_item_label(it)}"

# Ensure a "General / Meeting-level" catch-all item always exists so docs like
# the agenda itself can be assigned without being ignored or forced onto a
# specific agenda item.
_general_item = next((it for it in agenda_items if it["item_id"] == "0"), None)
if not _general_item:
    _general_item = db.insert_agenda_item(
        meeting_id=selected_id,
        title="General / Meeting-level Documents",
        seq=-1,
        depth=0,
        item_id="0",
    )
    agenda_items = db.get_agenda_items(selected_id)
_general_item_db_id = _general_item["id"]
item_options[_general_item_db_id] = "General / Meeting-level Documents"

# ---------------------------------------------------------------------------
# Meeting header
# ---------------------------------------------------------------------------
st.divider()
col1, col2, col3 = st.columns([1, 2, 1])
col1.metric("Venue", selected_meeting["venue_short"])
col2.metric(
    "Meeting",
    selected_meeting.get("title")
    or f"{selected_meeting['type_name']}  —  {selected_meeting['meeting_date']}"
)
col3.metric("Status", selected_meeting["status"].capitalize())

if selected_meeting.get("location"):
    st.caption(f"Location: {selected_meeting['location']}")

# ---------------------------------------------------------------------------
# Summarize button
# ---------------------------------------------------------------------------
if agenda_items:
    api_key_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    briefing_row = db.get_current_summary("meeting", selected_id)
    briefing_text = (briefing_row or {}).get("detailed") or ""

    if not api_key_ok:
        st.warning("ANTHROPIC_API_KEY not set — cannot summarise.", icon="⚠️")
    else:
        dl_col, btn_col, chk_col, img_col = st.columns([2, 2, 2, 2])
        with dl_col:
            if briefing_text:
                from pipeline.briefing import generate_docx_bytes
                docx_bytes = generate_docx_bytes(
                    briefing_text=briefing_text,
                    committee=selected_meeting.get("type_name", "Committee"),
                    meeting_dates=[str(selected_meeting["meeting_date"])],
                )
                st.download_button(
                    label="📄 Download Briefing",
                    data=docx_bytes,
                    file_name=f"Briefing_{selected_meeting.get('external_id', selected_id)}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )
            else:
                st.button("📄 Download Briefing", disabled=True, use_container_width=True)
        with btn_col:
            run_btn = st.button("🤖 Summarize Meeting", type="primary", use_container_width=True)
        with chk_col:
            force_rerun = st.checkbox("Force re-run all levels", value=False,
                                      help="Re-summarize even if summaries already exist (creates new versions)")
        with img_col:
            do_images = st.checkbox("Extract images", value=True,
                                    help="Extract and analyse charts/diagrams from slides and PDFs (slower, higher API cost)")
        if run_btn:
            committee_short = selected_meeting.get("type_short", "MC")
            with st.status("Running summarization…", expanded=True) as status_box:
                def _progress(msg: str) -> None:
                    st.write(msg)
                try:
                    client  = make_client()
                    results = run_meeting_summarization(
                        meeting_id=selected_id,
                        client=client,
                        committee_short=committee_short,
                        venue_short=selected_meeting.get("venue_short", "ISO-NE"),
                        progress_fn=_progress,
                        force_rerun=force_rerun,
                        extract_images=do_images if do_images else None,
                    )
                    n1, n2, n3 = results["level1"], results["level2"], results["level3"]
                    errs = results.get("errors", [])
                    summary_msg = (
                        f"Done — {n1} doc-group summaries, {n2} item rollups, "
                        f"meeting briefing {'✓' if n3 else '—'}."
                    )
                    if errs:
                        status_box.update(
                            label=f"Completed with {len(errs)} error(s).",
                            state="error", expanded=True,
                        )
                        for e in errs:
                            st.error(e)
                    else:
                        status_box.update(label=summary_msg, state="complete", expanded=False)
                        st.rerun()
                except Exception as exc:
                    status_box.update(label=f"Summarization failed: {exc}", state="error")
                    st.error(str(exc))

# ---------------------------------------------------------------------------
# Check for new materials
# ---------------------------------------------------------------------------
if agenda_items and selected_meeting.get("external_id"):
    refresh_col, resum_col = st.columns([3, 7])
    with refresh_col:
        check_btn = st.button("🔍 Check for New Materials", use_container_width=True,
                              help="Re-scrape the source to find newly posted documents")
    if check_btn:
        from pipeline.refresh import refresh_meeting_documents
        with st.status("Checking for new materials…", expanded=True) as refresh_box:
            try:
                import yaml as _yaml
                with open("config.yaml") as _fh:
                    _cfg = _yaml.safe_load(_fh)
                refresh_result = refresh_meeting_documents(selected_id, _cfg)

                if not refresh_result.has_new:
                    refresh_box.update(label="No new materials found.", state="complete", expanded=False)
                else:
                    n_new = len(refresh_result.new_docs)
                    n_assigned = sum(1 for d in refresh_result.new_docs if d.get("assigned_to_item"))
                    n_unassigned = len(refresh_result.unassigned_docs)
                    n_affected = len(refresh_result.affected_item_ids)

                    st.write(f"**{n_new} new document(s) found** — {n_assigned} assigned, {n_unassigned} unassigned")

                    for d in refresh_result.new_docs:
                        icon = "✅" if d.get("assigned_to_item") else "❓"
                        st.write(f"  {icon} `{d['filename']}`")

                    if refresh_result.errors:
                        for err in refresh_result.errors:
                            st.warning(err)

                    refresh_box.update(
                        label=f"Found {n_new} new document(s), {n_affected} agenda item(s) affected.",
                        state="complete", expanded=True,
                    )

                    # Offer re-summarization for affected items
                    if refresh_result.affected_item_ids and api_key_ok:
                        # Check if any affected items have manual approvals
                        manual_items = []
                        for aid in refresh_result.affected_item_ids:
                            summ = db.get_current_summary("agenda_item", aid)
                            if summ and summ.get("is_manual") and summ.get("status") == "approved":
                                manual_items.append(aid)
                        if manual_items:
                            st.warning(
                                f"{len(manual_items)} affected item(s) have manually approved summaries "
                                f"— these will be skipped unless you force re-run.",
                                icon="⚠️",
                            )

                        if st.button("🤖 Re-summarize affected items", type="primary"):
                            committee_short = selected_meeting.get("type_short", "MC")
                            with st.status("Re-summarizing affected items…", expanded=True) as resum_box:
                                def _refresh_progress(msg: str) -> None:
                                    st.write(msg)
                                try:
                                    client = make_client()
                                    results = run_meeting_summarization(
                                        meeting_id=selected_id,
                                        client=client,
                                        committee_short=committee_short,
                                        venue_short=selected_meeting.get("venue_short", "ISO-NE"),
                                        progress_fn=_refresh_progress,
                                        force_rerun=True,
                                        item_ids=refresh_result.affected_item_ids,
                                    )
                                    n1 = results["level1"]
                                    n2 = results["level2"]
                                    n3 = results["level3"]
                                    resum_box.update(
                                        label=f"Done — {n1} doc summaries, {n2} rollups, briefing {'✓' if n3 else '—'}.",
                                        state="complete", expanded=False,
                                    )
                                    st.rerun()
                                except Exception as exc:
                                    resum_box.update(label=f"Failed: {exc}", state="error")
                                    st.error(str(exc))
            except Exception as exc:
                refresh_box.update(label=f"Refresh failed: {exc}", state="error")
                st.error(str(exc))

# Meeting summary
mtg_summary = db.get_current_summary("meeting", selected_id)
if mtg_summary and (mtg_summary.get("one_line") or mtg_summary.get("detailed")):
    with st.expander("Meeting summary", expanded=True):
        if mtg_summary.get("one_line"):
            st.markdown(f"**{mtg_summary['one_line']}**")
        if mtg_summary.get("detailed"):
            st.markdown(mtg_summary["detailed"])
        st.caption(
            f"v{mtg_summary['version']}  ·  {mtg_summary['status']}  ·  "
            f"{mtg_summary['created_at'].strftime('%Y-%m-%d %H:%M') if mtg_summary.get('created_at') else '—'}"
        )
else:
    st.info("No meeting summary yet.")

# ---------------------------------------------------------------------------
# Unassigned documents bin  (prominent, before agenda)
# ---------------------------------------------------------------------------
unassigned = db.get_unassigned_documents(selected_id)

if unassigned:
    st.divider()
    st.warning(f"⚠️ {len(unassigned)} unassigned document(s) — assign or ignore each one below.")

    for doc in unassigned:
        icon = "🔒" if doc["ceii_skipped"] else "📄"
        fname = doc["filename"]

        col_name, col_sel, col_assign, col_ignore = st.columns([3, 3, 1, 1])
        with col_name:
            if doc.get("source_url"):
                st.markdown(f"{icon} [{fname}]({doc['source_url']})")
            else:
                st.markdown(f"{icon} {fname}")
        with col_sel:
            target = st.selectbox(
                "Assign to",
                options=list(item_options.keys()),
                format_func=lambda k: item_options[k],
                key=f"unassigned_target_{doc['id']}",
                label_visibility="collapsed",
            )
        with col_assign:
            if st.button("Assign", key=f"unassigned_assign_{doc['id']}", use_container_width=True,
                         disabled=target is None):
                db.assign_document_to_item(target, doc["id"])
                st.rerun()
        with col_ignore:
            if st.button("Ignore", key=f"unassigned_ignore_{doc['id']}", use_container_width=True):
                db.set_document_ignored(doc["id"], True)
                st.rerun()

# ---------------------------------------------------------------------------
# Ignored documents  (collapsed, at top so user sees it before agenda)
# ---------------------------------------------------------------------------
ignored_docs = db.get_ignored_documents(selected_id)
if ignored_docs:
    st.divider()
    with st.expander(f"Ignored documents ({len(ignored_docs)})", expanded=False):
        st.caption("These files are intentionally unassigned and won't trigger a warning.")
        for doc in ignored_docs:
            icon = "🔒" if doc["ceii_skipped"] else "📄"
            fname = doc["filename"]
            col_name, col_btn = st.columns([6, 1])
            with col_name:
                if doc.get("source_url"):
                    st.markdown(f"{icon} [{fname}]({doc['source_url']})")
                else:
                    st.markdown(f"{icon} {fname}")
            with col_btn:
                if st.button("Restore", key=f"restore_ignored_{doc['id']}", use_container_width=True):
                    db.set_document_ignored(doc["id"], False)
                    st.rerun()

# ---------------------------------------------------------------------------
# Agenda
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Agenda")

if not agenda_items:
    st.info("No agenda items loaded for this meeting.")
else:
    for item in agenda_items:
        depth = item["depth"]
        indent = "\u00a0" * (depth * 6)
        item_label = f"{indent}**{item.get('item_id') or ''}**  {item['title']}"

        with st.expander(item_label, expanded=(depth == 0)):
            editing = st.session_state.get(f"editing_{item['id']}", False)

            if not editing:
                # ── Read mode ────────────────────────────────────────────────
                meta_parts = []
                if item.get("presenter"):
                    meta_parts.append(f"👤 {item['presenter']}")
                    if item.get("org"):
                        meta_parts[-1] += f" ({item['org']})"
                if item.get("vote_status"):
                    meta_parts.append(f"🗳 {item['vote_status']}")
                if item.get("time_slot"):
                    meta_parts.append(f"🕐 {item['time_slot']}")
                if item.get("wmpp_id"):
                    meta_parts.append(f"WMPP {item['wmpp_id']}")
                if meta_parts:
                    st.caption("  ·  ".join(meta_parts))

                if item.get("notes"):
                    st.markdown(f"*{item['notes']}*")

                tags = db.get_tags_for_entity("agenda_item", item["id"])
                if tags:
                    st.markdown("  ".join(f"`{t['name']}`" for t in tags))

                summary = db.get_current_summary("agenda_item", item["id"])
                if summary and (summary.get("one_line") or summary.get("detailed")):
                    st.markdown("---")
                    if summary.get("one_line"):
                        st.markdown(f"**Summary:** {summary['one_line']}")
                    if summary.get("detailed"):
                        with st.expander("Full summary"):
                            _render_summary_with_images(summary["detailed"])
                    st.caption(
                        f"v{summary['version']}  ·  {summary['status']}  ·  "
                        f"{summary['created_at'].strftime('%Y-%m-%d %H:%M') if summary.get('created_at') else '—'}"
                    )
                else:
                    st.caption("No summary yet.")

            else:
                # ── Edit mode ─────────────────────────────────────────────────
                summary = db.get_current_summary("agenda_item", item["id"])

                with st.form(key=f"edit_form_{item['id']}"):
                    st.markdown("**Metadata**")
                    title_col, itemid_col = st.columns([3, 1])
                    new_title = title_col.text_input("Title", value=item.get("title") or "")
                    new_item_id = itemid_col.text_input("Item ID", value=item.get("item_id") or "")
                    col_a, col_b = st.columns(2)
                    new_presenter = col_a.text_input("Presenter", value=item.get("presenter") or "")
                    new_org       = col_b.text_input("Organisation", value=item.get("org") or "")
                    col_c, col_d, col_e = st.columns(3)
                    new_vote   = col_c.text_input("Vote status",    value=item.get("vote_status") or "")
                    new_wmpp   = col_d.text_input("WMPP ID",        value=item.get("wmpp_id") or "")
                    new_time   = col_e.text_input("Time slot",      value=item.get("time_slot") or "")
                    new_notes  = st.text_area("Notes", value=item.get("notes") or "", height=80)

                    st.markdown("**Summary**")
                    new_one_line = st.text_input(
                        "One-line summary",
                        value=(summary.get("one_line") or "") if summary else "",
                    )
                    new_detailed = st.text_area(
                        "Detailed summary",
                        value=(summary.get("detailed") or "") if summary else "",
                        height=150,
                    )

                    save_col, cancel_col, del_col, _ = st.columns([1, 1, 1, 3])
                    saved    = save_col.form_submit_button("💾 Save", type="primary", use_container_width=True)
                    cancelled = cancel_col.form_submit_button("Cancel", use_container_width=True)
                    deleted  = del_col.form_submit_button("🗑 Delete", use_container_width=True)

                if deleted:
                    db.delete_agenda_item(item["id"])
                    st.session_state.pop(f"editing_{item['id']}", None)
                    st.rerun()

                if saved:
                    new_prefix = item_id_to_prefix(new_item_id) if new_item_id.strip() else None
                    db.update_agenda_item(
                        item["id"],
                        title=new_title,
                        item_id=new_item_id.strip() or None,
                        prefix=new_prefix,
                        presenter=new_presenter or None,
                        org=new_org or None,
                        vote_status=new_vote or None,
                        wmpp_id=new_wmpp or None,
                        time_slot=new_time or None,
                        notes=new_notes or None,
                    )
                    if new_one_line or new_detailed:
                        db.save_manual_summary(
                            "agenda_item", item["id"],
                            one_line=new_one_line or None,
                            detailed=new_detailed or None,
                        )
                    st.session_state[f"editing_{item['id']}"] = False
                    st.rerun()

                if cancelled:
                    st.session_state[f"editing_{item['id']}"] = False
                    st.rerun()

            # Documents — always visible; controls only shown in edit mode
            docs = db.get_documents_for_item(item["id"])
            if docs:
                st.markdown("**Documents:**")
                for doc in docs:
                    _doc_row(doc, selected_id, item_options,
                             key_prefix=f"item_{item['id']}", edit_mode=editing)

            # Edit button at the very bottom of the card
            if not editing:
                if st.button("✏️ Edit", key=f"edit_btn_{item['id']}"):
                    st.session_state[f"editing_{item['id']}"] = True
                    st.rerun()

    # ── Add new agenda item ──────────────────────────────────────────────
    if st.session_state.get("adding_agenda_item", False):
        with st.form(key="add_agenda_item_form"):
            st.markdown("#### New Agenda Item")
            new_title = st.text_input("Title *")
            new_item_id = st.text_input("Item ID (e.g. 7.1.b)")
            col_a, col_b = st.columns(2)
            new_presenter = col_a.text_input("Presenter")
            new_org = col_b.text_input("Organisation")
            col_c, col_d, col_e = st.columns(3)
            new_vote = col_c.text_input("Vote status")
            new_wmpp = col_d.text_input("WMPP ID")
            new_time = col_e.text_input("Time slot")
            new_notes = st.text_area("Notes", height=80)

            save_col, cancel_col, _ = st.columns([1, 1, 4])
            add_saved = save_col.form_submit_button("💾 Add", type="primary", use_container_width=True)
            add_cancelled = cancel_col.form_submit_button("Cancel", use_container_width=True)

        if add_saved:
            if new_title.strip():
                next_seq = db.get_max_seq(selected_id) + 1
                db.insert_agenda_item(
                    meeting_id=selected_id,
                    title=new_title.strip(),
                    seq=next_seq,
                    depth=0,
                    item_id=new_item_id.strip() or None,
                    presenter=new_presenter.strip() or None,
                    org=new_org.strip() or None,
                    vote_status=new_vote.strip() or None,
                    wmpp_id=new_wmpp.strip() or None,
                    time_slot=new_time.strip() or None,
                    notes=new_notes.strip() or None,
                )
                st.session_state["adding_agenda_item"] = False
                st.rerun()
            else:
                st.error("Title is required.")

        if add_cancelled:
            st.session_state["adding_agenda_item"] = False
            st.rerun()
    else:
        if st.button("➕ Add Agenda Item"):
            st.session_state["adding_agenda_item"] = True
            st.rerun()

