import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { PerItemDocControls } from "../MaterialAssignment";
import { api } from "../../lib/api";
import { qk } from "../../lib/queries";
import { toast } from "../../lib/toast";
import { extFromFilename } from "../../lib/format";
import type { AgendaItem, DocumentRef } from "../../types";

export function DocRow({
  doc,
  meetingId,
  itemId,
  agenda,
}: {
  doc: DocumentRef;
  meetingId: number;
  itemId: number;
  agenda: AgendaItem[];
}) {
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteDocument(doc.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
    },
    onError: (e: Error) => toast.error(`Remove failed: ${e.message}`),
  });

  return (
    <div className="doc-row doc-row-assignable">
      <div className="doc-icon">
        {doc.ceii ? <Icon name="lock" /> : <Icon name="doc" />}
      </div>
      <div className="doc-name truncate">
        {doc.filename}
        {doc.manual && (
          <span className="doc-manual-badge" title="Added manually">
            added
          </span>
        )}
      </div>
      <div className="doc-ext mono text-xs">{extFromFilename(doc.filename)}</div>
      <div className="doc-actions">
        <PerItemDocControls
          meetingId={meetingId}
          docId={doc.id}
          itemId={itemId}
          agenda={agenda}
        />
        {doc.source_url ? (
          <>
            <a
              className="btn btn-sm btn-ghost"
              title="Open source"
              href={doc.source_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Icon name="external" size={12} />
            </a>
            <a
              className="btn btn-sm btn-ghost"
              title="Download"
              href={doc.source_url}
              download={doc.filename}
            >
              <Icon name="download" size={12} />
            </a>
          </>
        ) : (
          doc.manual && (
            <button
              className="btn btn-sm btn-ghost"
              title="Download"
              onClick={() =>
                api
                  .downloadDocumentFile(doc)
                  .catch((e) => toast.error(`Download failed: ${e.message || e}`))
              }
            >
              <Icon name="download" size={12} />
            </button>
          )
        )}
        {doc.manual && (
          <button
            className="btn btn-sm btn-ghost"
            title="Remove this material"
            disabled={remove.isPending}
            onClick={() => {
              if (confirm(`Remove "${doc.filename}" from this item?`))
                remove.mutate();
            }}
          >
            <Icon name="trash" size={12} />
          </button>
        )}
      </div>
    </div>
  );
}
