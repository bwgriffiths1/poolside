import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { api } from "../../lib/api";
import { qk } from "../../lib/queries";
import { formatRel } from "../../lib/format";

export function AgendaEmpty({
  meetingId,
  lastScraped,
}: {
  meetingId: number;
  lastScraped?: string;
}) {
  const qc = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => api.refreshMeeting(meetingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
    },
  });

  const rel = lastScraped ? formatRel(lastScraped) : null;

  return (
    <div className="empty" style={{ textAlign: "left", padding: "var(--pad-5)" }}>
      <div className="serif" style={{ fontSize: 17, color: "var(--ink)", marginBottom: 6 }}>
        Agenda not posted yet.
      </div>
      <div className="muted text-sm" style={{ marginBottom: 12 }}>
        {rel
          ? `Last checked ${rel}. ISO-NE typically posts agendas about a week before the meeting.`
          : "This meeting hasn't been scraped for materials yet."}
      </div>
      <button
        className="btn btn-sm btn-accent"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
      >
        <Icon name="refresh" size={12} />{" "}
        {refresh.isPending ? "Checking…" : "Re-check now"}
      </button>
    </div>
  );
}
