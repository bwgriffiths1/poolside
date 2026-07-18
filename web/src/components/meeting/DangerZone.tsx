import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { api } from "../../lib/api";
import { qk } from "../../lib/queries";
import { toast } from "../../lib/toast";

export function DangerZone({ meetingId, title }: { meetingId: number; title: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const wipeDocs = useMutation({
    mutationFn: () => api.deleteAllDocuments(meetingId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetings });
      if (res.removed_documents === 0) {
        toast.info("No documents to remove.");
      } else {
        toast.success(
          `Removed ${res.removed_documents} document${res.removed_documents === 1 ? "" : "s"}. Agenda items kept; doc assignments cleared.`,
        );
      }
    },
    onError: (e: Error) => toast.error(`Failed: ${e.message}`),
  });

  const deleteMtg = useMutation({
    mutationFn: () => api.deleteMeeting(meetingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.meetings });
      navigate("/overview", { replace: true });
    },
    onError: (e: Error) => toast.error(`Failed: ${e.message}`),
  });

  const onWipeDocs = () => {
    if (
      window.confirm(
        "Remove every document attached to this meeting? Agenda items + summaries are kept; doc-to-item assignments are cleared. This can't be undone.",
      )
    ) {
      wipeDocs.mutate();
    }
  };

  const onDelete = () => {
    const ans = window.prompt(
      `Type the meeting title to confirm full deletion:\n\n${title}`,
    );
    if (ans == null) return;
    if (ans.trim() !== title.trim()) {
      toast.info("Title didn't match — nothing deleted.");
      return;
    }
    deleteMtg.mutate();
  };

  return (
    <div className="danger-zone">
      <h2 className="danger-zone-h">Danger zone</h2>
      <div className="danger-row">
        <div>
          <div className="danger-row-h">Remove all documents</div>
          <div className="muted text-xs">
            Wipes documents + their item assignments. Keeps agenda + summaries.
            Useful when the scraper pulled garbage and you want to re-discover
            materials from scratch.
          </div>
        </div>
        <button
          className="btn btn-sm"
          onClick={onWipeDocs}
          disabled={wipeDocs.isPending}
        >
          <Icon name="trash" size={12} />{" "}
          {wipeDocs.isPending ? "Removing…" : "Remove all docs"}
        </button>
      </div>
      <div className="danger-row">
        <div>
          <div className="danger-row-h">Delete this meeting</div>
          <div className="muted text-xs">
            Drops the meeting row + every agenda item, document, summary,
            share link, and summarize job that hangs off it. Cascades. Cannot
            be undone.
          </div>
        </div>
        <button
          className="btn btn-sm"
          style={{ borderColor: "var(--accent-soft)", color: "var(--accent)" }}
          onClick={onDelete}
          disabled={deleteMtg.isPending}
        >
          <Icon name="trash" size={12} />{" "}
          {deleteMtg.isPending ? "Deleting…" : "Delete meeting"}
        </button>
      </div>
    </div>
  );
}
