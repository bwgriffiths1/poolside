import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Topbar } from "../components/Topbar";
import { Pill } from "../components/Pill";
import { Tag, VenueTag, TypeTag } from "../components/Tag";
import { Icon } from "../components/Icon";
import { AgendaRow } from "../components/agenda/AgendaRow";
import { AgendaEmpty } from "../components/agenda/AgendaEmpty";
import { idForAnchor } from "../components/agenda/anchors";
import { MeetingLinks } from "../components/meeting/MeetingLinks";
import { WatchToggle } from "../components/meeting/WatchToggle";
import { SummarizeJobBanner } from "../components/meeting/SummarizeJobBanner";
import { FilesSection } from "../components/meeting/FilesSection";
import { useSummarizeJob } from "../hooks/useSummarizeJob";
import { api } from "../lib/api";
import { useBriefing, useCan, useMeeting, useMeetingsAll } from "../lib/queries";
import { toast } from "../lib/toast";
import { fmtDateRange } from "../lib/format";
import { useTrackView } from "../hooks/useTrackView";
import type { MeetingListItem } from "../types";

// The reader half of the meeting split: everything you need to KNOW about a
// meeting. Page-level operations (summarize runner, file triage, agenda
// additions, danger zone) live on /meeting/:id/manage. Per-row agenda tools
// stay here — they're contextual to reading an item.

export function Meeting() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { canEdit } = useCan();

  const meetingId = Number(id);
  useTrackView("meeting", meetingId);

  const { data: detail, isLoading: detailLoading } = useMeeting(meetingId);
  const { data: briefing } = useBriefing(meetingId);

  const m = detail; // detail is a MeetingDetail (extends MeetingListItem)
  const hasBriefing =
    !!briefing && (briefing.sections.length > 0 || briefing.tldr.length > 0);

  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [editingId, setEditingId] = useState<number | null>(null);
  const [searchParams] = useSearchParams();
  const targetItemParam = searchParams.get("item");

  // Status visibility only — starting/cancelling a run lives on Manage.
  const job = useSummarizeJob(meetingId);

  // Anchor links: ?item=7.a → auto-expand + scroll to that agenda item.
  // We run this once the agenda has loaded; subsequent param changes also
  // re-trigger so navigating in-app preserves the behavior.
  useEffect(() => {
    if (!targetItemParam || !detail?.agenda) return;
    const target = detail.agenda.find(
      (it) => (it.item_id ?? "") === targetItemParam,
    );
    if (!target) return;
    setExpandedIds((prev) => {
      if (prev.has(target.id)) return prev;
      const next = new Set(prev);
      next.add(target.id);
      return next;
    });
    // Scroll after the row paints with its expanded body.
    const slug = idForAnchor(target.item_id);
    requestAnimationFrame(() => {
      const el = document.getElementById(slug);
      const main = document.querySelector(".main") as HTMLElement | null;
      if (el && main) {
        main.scrollTo({ top: el.offsetTop - 24, behavior: "smooth" });
      }
    });
  }, [targetItemParam, detail?.agenda]);

  const toggle = (itemId: number) =>
    setExpandedIds((prev) => {
      const n = new Set(prev);
      if (n.has(itemId)) n.delete(itemId);
      else n.add(itemId);
      return n;
    });

  const totals = useMemo(() => {
    const agenda = detail?.agenda ?? [];
    const total = agenda.length;
    const withSummary = agenda.filter((i) => i.has_summary).length;
    const docs = agenda.flatMap((i) => i.docs).length;
    return { total, withSummary, docs };
  }, [detail]);

  if (!m || !detail) {
    return (
      <>
        <Topbar
          crumbs={[
            { label: "Meetings", to: "/meetings" },
            { label: detailLoading ? "Loading…" : "Not found" },
          ]}
        />
        <div className="page">
          <div className="muted">
            {detailLoading ? "Loading meeting…" : "Meeting not found."}
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar
        crumbs={[
          { label: "Meetings", to: "/meetings" },
          { label: `${m.venue} · ${m.type_short}` },
          { label: m.title },
        ]}
        actions={
          <>
            <WatchToggle meetingId={meetingId} />
            <button
              className="btn btn-sm"
              onClick={() => navigate(`/briefing/${m.id}`)}
            >
              <Icon name="book" /> Open briefing
            </button>
            {canEdit && (
              <button
                className="btn btn-sm btn-primary"
                onClick={() => navigate(`/meeting/${m.id}/manage`)}
              >
                <Icon name="settings" /> Manage
              </button>
            )}
          </>
        }
      />

      <div className="page-wide">
        <div className="meeting-head">
          <div>
            <div className="page-eyebrow">
              <VenueTag style={{ marginRight: 6 }}>{m.venue}</VenueTag>
              <TypeTag style={{ marginRight: 6 }}>{m.type_short}</TypeTag>
              {m.external_id}
            </div>
            <h1 className="page-title">{m.type_name}</h1>
            <div className="meeting-head-meta">
              <span>
                <Icon name="calendar" size={13} />{" "}
                {fmtDateRange(m.meeting_date, m.end_date)}
              </span>
              <span>
                <Icon name="globe" size={13} /> {m.location}
              </span>
              <Pill status={m.status} />
            </div>
            <MeetingLinks venue={m.venue} externalId={m.external_id} />
            {detail.one_line && (
              <p className="meeting-headline serif">{detail.one_line}</p>
            )}
          </div>
          <div className="meeting-head-right">
            <div className="stat-block">
              <div className="stat-block-num">{totals.total}</div>
              <div className="stat-block-label">agenda items</div>
            </div>
            <div className="stat-block">
              <div className="stat-block-num">{totals.docs}</div>
              <div className="stat-block-label">documents</div>
            </div>
            <div className="stat-block">
              <div className="stat-block-num">
                <span>{totals.withSummary}</span>
                <span className="muted">/{totals.total}</span>
              </div>
              <div className="stat-block-label">summarized</div>
            </div>
          </div>
        </div>

        {m.tags.length > 0 && (
          <div
            className="row"
            style={{ gap: 6, flexWrap: "wrap", marginBottom: 24 }}
          >
            <span
              className="field-label"
              style={{ marginBottom: 0, marginRight: 4 }}
            >
              Topics
            </span>
            {m.tags.map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </div>
        )}

        {hasBriefing ? (
          <div
            className="briefing-card"
            onClick={() => navigate(`/briefing/${m.id}`)}
          >
            <div>
              <div className="page-eyebrow" style={{ marginBottom: 6 }}>
                Meeting briefing
              </div>
              <h2 className="briefing-card-title serif">
                {briefing!.headline || detail.one_line || briefing!.title}
              </h2>
              <div className="row" style={{ marginTop: 12, gap: 14 }}>
                <span className="text-xs muted">
                  <Icon name="dot" size={11} /> {briefing!.word_count} words ·{" "}
                  {briefing!.reading_time} min read
                </span>
                <span className="text-xs muted">{briefing!.model}</span>
                <span className="text-xs muted">
                  Generated {briefing!.generated_at}
                </span>
              </div>
            </div>
            <div className="briefing-card-right">
              <button
                className="btn btn-sm"
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    await api.downloadBriefingDocx(meetingId);
                  } catch (err) {
                    console.error("Download failed", err);
                    toast.error("Could not download briefing — see console for details.");
                  }
                }}
              >
                <Icon name="download" /> Download .docx
              </button>
              <button
                className="btn btn-sm btn-accent"
                onClick={() => navigate(`/briefing/${m.id}`)}
              >
                Read briefing <Icon name="arrow-r" size={12} />
              </button>
            </div>
          </div>
        ) : (
          <div
            className="briefing-card"
            style={{
              background: "var(--bg-elev)",
              borderColor: "var(--border)",
              cursor: "default",
            }}
          >
            <div>
              <div className="page-eyebrow" style={{ marginBottom: 6 }}>
                Meeting briefing
              </div>
              <h2 className="briefing-card-title serif" style={{ color: "var(--muted)" }}>
                No briefing yet — run summarization to generate one.
              </h2>
            </div>
            {canEdit && (
              <div className="briefing-card-right">
                <button
                  className="btn btn-sm btn-accent"
                  onClick={() => navigate(`/meeting/${m.id}/manage`)}
                >
                  <Icon name="spark" size={12} /> Manage &amp; summarize
                </button>
              </div>
            )}
          </div>
        )}

        {job.job && (
          <SummarizeJobBanner
            job={job.job}
            cancelling={false}
            onDismiss={job.dismiss}
          />
        )}

        <div className="section-h" style={{ marginTop: 32 }}>
          <h2>Agenda</h2>
          <span className="meta">
            {totals.total} items · {totals.docs} documents
          </span>
        </div>
        {detail.agenda.length === 0 ? (
          <AgendaEmpty meetingId={meetingId} lastScraped={m.last_scraped_at} />
        ) : (
          <div className="agenda-list">
            {detail.agenda.map((item) => (
              <AgendaRow
                key={item.id}
                item={item}
                meetingId={meetingId}
                agenda={detail.agenda}
                expanded={expandedIds.has(item.id)}
                onToggle={() => toggle(item.id)}
                isEditing={editingId === item.id}
                onEdit={() => setEditingId(item.id)}
                onCloseEdit={() => setEditingId(null)}
              />
            ))}
          </div>
        )}

        <FilesSection meetingId={meetingId} readOnly />

        <PrevNextNav meetingId={meetingId} />

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}

// Chronological neighbors from the already-warm meetings cache. The briefing
// prev/next API skips meetings without briefings, which is wrong here — the
// reader pages over the calendar record itself.
function PrevNextNav({ meetingId }: { meetingId: number }) {
  const navigate = useNavigate();
  const { data: all } = useMeetingsAll();

  const { prev, next } = useMemo(() => {
    if (!all) return { prev: undefined, next: undefined };
    const sorted = [...all].sort(
      (a, b) => a.meeting_date.localeCompare(b.meeting_date) || a.id - b.id,
    );
    const i = sorted.findIndex((x) => x.id === meetingId);
    if (i < 0) return { prev: undefined, next: undefined };
    return { prev: sorted[i - 1], next: sorted[i + 1] };
  }, [all, meetingId]);

  if (!prev && !next) return null;

  const label = (x: MeetingListItem) =>
    `${fmtDateRange(x.meeting_date, x.end_date)} · ${x.type_short}`;

  return (
    <div className="meeting-prevnext">
      {prev ? (
        <button className="btn btn-sm" onClick={() => navigate(`/meeting/${prev.id}`)}>
          <Icon name="arrow-l" size={12} /> {label(prev)}
        </button>
      ) : (
        <span />
      )}
      {next ? (
        <button className="btn btn-sm" onClick={() => navigate(`/meeting/${next.id}`)}>
          {label(next)} <Icon name="arrow-r" size={12} />
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}
