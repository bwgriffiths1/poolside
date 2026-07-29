import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Pill } from "../components/Pill";
import { VenueTag, TypeTag } from "../components/Tag";
import { Icon } from "../components/Icon";
import { MaterialAssignment } from "../components/MaterialAssignment";
import { AddAgendaItem } from "../components/agenda/AddAgendaItem";
import { SummarizeRunner } from "../components/meeting/SummarizeRunner";
import { SummarizeJobBanner } from "../components/meeting/SummarizeJobBanner";
import { FilesSection } from "../components/meeting/FilesSection";
import { DangerZone } from "../components/meeting/DangerZone";
import { useSummarizeJob } from "../hooks/useSummarizeJob";
import { api } from "../lib/api";
import { qk, useBriefing, useCan, useMeeting } from "../lib/queries";
import { toast } from "../lib/toast";
import { fmtDateRange } from "../lib/format";

// The workbench half of the meeting split (route is RequireRole-gated to
// editors): run summarization, triage files, extend the agenda, manage
// uploads, delete things. Reading lives on /meeting/:id.

export function MeetingManage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { isAdmin } = useCan();

  const meetingId = Number(id);
  const { data: detail, isLoading: detailLoading } = useMeeting(meetingId);
  const { data: briefing } = useBriefing(meetingId);

  const m = detail;
  const hasBriefing =
    !!briefing && (briefing.sections.length > 0 || briefing.tldr.length > 0);

  const [showSummaryRunner, setShowSummaryRunner] = useState(false);

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

  if (!m || !detail) {
    return (
      <>
        <Topbar
          crumbs={[
            { label: "Meetings", to: "/meetings" },
            { label: detailLoading ? "Loading…" : "Not found" },
            { label: "Manage" },
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
          { label: `${m.venue} · ${m.type_short}`, to: `/meeting/${m.id}` },
          { label: "Manage" },
        ]}
        actions={
          <>
            {isAdmin && (
              <button
                className="btn btn-sm"
                onClick={() => cleanupZips.mutate()}
                disabled={cleanupZips.isPending}
                title="Undo a prior Expand zips run — zips are now handled inline at summarize time."
              >
                <Icon name="refresh" />{" "}
                {cleanupZips.isPending ? "Cleaning…" : "Reset zip rows"}
              </button>
            )}
            <button
              className="btn btn-sm"
              onClick={() => navigate(`/meeting/${m.id}`)}
            >
              <Icon name="book" /> Open reader
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

      <div className="page-wide">
        <div className="manage-head">
          <div>
            <div className="page-eyebrow">
              <VenueTag style={{ marginRight: 6 }}>{m.venue}</VenueTag>
              <TypeTag style={{ marginRight: 6 }}>{m.type_short}</TypeTag>
              {m.external_id}
            </div>
            <div className="manage-head-title">{m.type_name}</div>
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
          </div>
          <Link className="btn btn-sm" to={`/meeting/${m.id}`}>
            <Icon name="arrow-l" size={12} /> Back to meeting
          </Link>
        </div>

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
            {detail.agenda.length} items ·{" "}
            {detail.agenda.flatMap((i) => i.docs).length} documents
          </span>
        </div>
        <p className="muted text-xs" style={{ marginTop: -8, marginBottom: 12 }}>
          Per-item edits, re-runs, and item materials live on the{" "}
          <Link to={`/meeting/${m.id}`}>meeting page</Link>.
        </p>
        <AddAgendaItem meetingId={meetingId} />

        <FilesSection meetingId={meetingId} />

        <DangerZone meetingId={meetingId} title={m.title} />

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
