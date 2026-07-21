import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Pill } from "../components/Pill";
import { Tag, VenueTag, TypeTag } from "../components/Tag";
import { Icon } from "../components/Icon";
import { MaterialAssignment } from "../components/MaterialAssignment";
import { AgendaRow } from "../components/agenda/AgendaRow";
import { AddAgendaItem } from "../components/agenda/AddAgendaItem";
import { AgendaEmpty } from "../components/agenda/AgendaEmpty";
import { idForAnchor } from "../components/agenda/anchors";
import { MeetingLinks } from "../components/meeting/MeetingLinks";
import { WatchToggle } from "../components/meeting/WatchToggle";
import { SummarizeRunner } from "../components/meeting/SummarizeRunner";
import { SummarizeJobBanner } from "../components/meeting/SummarizeJobBanner";
import { FilesSection } from "../components/meeting/FilesSection";
import { DangerZone } from "../components/meeting/DangerZone";
import { useSummarizeJob } from "../hooks/useSummarizeJob";
import { api } from "../lib/api";
import { qk, useBriefing, useMeeting } from "../lib/queries";
import { toast } from "../lib/toast";
import { fmtDateRange } from "../lib/format";

export function Meeting() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const meetingId = Number(id);

  const { data: detail, isLoading: detailLoading } = useMeeting(meetingId);
  const { data: briefing } = useBriefing(meetingId);

  const m = detail; // detail is a MeetingDetail (extends MeetingListItem)
  const hasBriefing =
    !!briefing && (briefing.sections.length > 0 || briefing.tldr.length > 0);

  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [editingId, setEditingId] = useState<number | null>(null);
  const [searchParams] = useSearchParams();
  const targetItemParam = searchParams.get("item");
  const [showSummaryRunner, setShowSummaryRunner] = useState(false);
  // TODO: meeting-level summarize options (briefing style, extract images,
  // force re-run) are not honored by the backend yet — see the parity plan.
  // Per-item re-runs work via AgendaRow's "Re-run" button.

  const job = useSummarizeJob(meetingId, {
    onStarted: () => setShowSummaryRunner(false),
  });

  const cleanupZips = useMutation({
    mutationFn: () => api.cleanupZipExpansion(meetingId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetings });
      if (res.deleted_children === 0 && res.un_ignored_zips === 0) {
        toast.info("Nothing to clean up — this meeting wasn't pre-expanded.");
      } else {
        toast.success(
          `Removed ${res.deleted_children} expanded child row(s); ` +
            `restored ${res.un_ignored_zips} zip(s). ` +
            `Zips are now handled inline at summarize time.`,
        );
      }
    },
    onError: (e: Error) => toast.error(`Cleanup failed: ${e.message}`),
  });

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
            <button
              className="btn btn-sm"
              onClick={() => cleanupZips.mutate()}
              disabled={cleanupZips.isPending}
              title="Undo a prior Expand zips run — zips are now handled inline at summarize time."
            >
              <Icon name="refresh" />{" "}
              {cleanupZips.isPending ? "Cleaning…" : "Reset zip rows"}
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => setShowSummaryRunner(true)}
            >
              <Icon name="spark" /> Summarize
            </button>
          </>
        }
      />

      <div className="page-wide" style={{ paddingLeft: 48, paddingRight: 48 }}>
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
            <div className="briefing-card-right">
              <button
                className="btn btn-sm btn-accent"
                onClick={() => setShowSummaryRunner(true)}
              >
                <Icon name="spark" size={12} /> Summarize
              </button>
            </div>
          </div>
        )}

        {job.job && (
          <SummarizeJobBanner
            job={job.job}
            onCancel={() => job.cancel(job.job!.id)}
            cancelling={job.isCancelling}
            onDismiss={job.dismiss}
          />
        )}

        {showSummaryRunner && (
          <SummarizeRunner
            meetingId={meetingId}
            agenda={detail.agenda}
            hasBriefing={hasBriefing}
            onClose={() => setShowSummaryRunner(false)}
            onStart={job.start}
            isStarting={job.isStarting}
          />
        )}

        <MaterialAssignment meetingId={meetingId} agenda={detail.agenda} />

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

        <AddAgendaItem meetingId={meetingId} />

        <FilesSection meetingId={meetingId} />

        <DangerZone meetingId={meetingId} title={m.title} />

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
