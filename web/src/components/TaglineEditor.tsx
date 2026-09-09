import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { api, type SummaryEntityType } from "../lib/api";
import { qk } from "../lib/queries";
import { toast } from "../lib/toast";

/** The one-sentence tagline under a briefing title (summary_versions
 *  .one_line), editable in place by editors: click the pencil, type,
 *  Enter/Save. Viewers just see the text. Renders nothing for viewers when
 *  there is no tagline yet. */
export function TaglineEditor({
  entityType,
  entityId,
  value,
  canEdit,
  className,
  invalidate = [],
}: {
  entityType: SummaryEntityType;
  entityId: number;
  value: string;
  canEdit: boolean;
  /** Class for the read-only text (e.g. "briefing-headline"). */
  className: string;
  /** Query keys to refresh after a save, besides the summary itself. */
  invalidate?: readonly (readonly unknown[])[];
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (text: string) => api.setSummaryOneLine(entityType, entityId, text),
    onSuccess: () => {
      setDraft(null);
      qc.invalidateQueries({ queryKey: qk.summary(entityType, entityId) });
      for (const key of invalidate) qc.invalidateQueries({ queryKey: key });
      toast.success("Tagline saved.");
    },
    onError: (e: Error) => toast.error(`Could not save tagline: ${e.message}`),
  });

  if (draft !== null) {
    return (
      <form
        className="tagline-edit"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(draft.trim());
        }}
      >
        <input
          className="input"
          autoFocus
          value={draft}
          maxLength={500}
          placeholder="One sentence that captures the meeting…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setDraft(null);
            if (e.key === "Enter") {
              e.preventDefault();
              if (!save.isPending) save.mutate(draft.trim());
            }
          }}
        />
        <div className="row" style={{ gap: 6 }}>
          <button className="btn btn-primary btn-sm" type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </button>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={() => setDraft(null)}
            disabled={save.isPending}
          >
            Cancel
          </button>
        </div>
      </form>
    );
  }

  if (!value && !canEdit) return null;
  return (
    <p className={`${className} tagline-row`}>
      {value ? (
        <span>{value}</span>
      ) : (
        <span className="tagline-empty">No tagline yet.</span>
      )}
      {canEdit && (
        <button
          type="button"
          className="tagline-pencil"
          title="Edit the tagline"
          aria-label="Edit the tagline"
          onClick={() => setDraft(value)}
        >
          <Icon name="edit" size={13} />
        </button>
      )}
    </p>
  );
}
