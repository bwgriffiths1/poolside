import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { api } from "../../lib/api";
import { qk } from "../../lib/queries";
import { toast } from "../../lib/toast";
import { fmtBytes } from "../../lib/format";
import type { Attachment } from "../../types";

export function FilesSection({ meetingId }: { meetingId: number }) {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: qk.attachments(meetingId),
    queryFn: () => api.listAttachments(meetingId),
  });
  const files: Attachment[] = data?.attachments ?? [];

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadAttachment(meetingId, file),
    onError: (err: unknown) =>
      setError(err instanceof Error ? err.message : String(err)),
    onSettled: () =>
      qc.invalidateQueries({ queryKey: qk.attachments(meetingId) }),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteAttachment(id),
    onSettled: () =>
      qc.invalidateQueries({ queryKey: qk.attachments(meetingId) }),
  });

  async function handleFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    setError(null);
    // Upload sequentially so a shared error surfaces cleanly.
    for (const file of Array.from(list)) {
      await upload.mutateAsync(file).catch(() => {
        /* onError already captured it; stop the batch */
      });
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  }

  function onPick(e: ChangeEvent<HTMLInputElement>) {
    handleFiles(e.target.files);
    e.target.value = ""; // allow re-selecting the same file
  }

  return (
    <>
      <div className="section-h" style={{ marginTop: 32 }}>
        <h2>Files</h2>
        <span className="meta">
          {files.length} file{files.length === 1 ? "" : "s"}
        </span>
      </div>
      <p className="muted text-xs" style={{ marginTop: -8, marginBottom: 12 }}>
        Upload your own files against this meeting — hand-written briefings,
        notes, reference docs. Download them back any time. 25 MB max per file.
      </p>

      <div
        className={`file-drop${dragOver ? " is-over" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        role="button"
        tabIndex={0}
      >
        <Icon name="upload" size={18} />
        <div className="file-drop-text">
          <strong>Click to upload</strong> or drag &amp; drop
        </div>
        {upload.isPending && (
          <div className="muted text-xs">Uploading…</div>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          onChange={onPick}
        />
      </div>

      {error && (
        <div className="muted text-xs" style={{ color: "var(--accent)", marginTop: 8 }}>
          Upload failed: {error}
        </div>
      )}

      {files.length > 0 && (
        <div className="file-list">
          {files.map((f) => (
            <div className="file-row" key={f.id}>
              <Icon name="paperclip" size={14} />
              <div className="file-row-main">
                <div className="file-row-name">{f.filename}</div>
                <div className="muted text-xs">
                  {fmtBytes(f.size_bytes)}
                  {f.uploaded_by ? ` · ${f.uploaded_by}` : ""}
                  {f.created_at
                    ? ` · ${new Date(f.created_at).toLocaleDateString()}`
                    : ""}
                  {f.note ? ` · ${f.note}` : ""}
                </div>
              </div>
              <button
                className="btn btn-sm"
                onClick={() =>
                  api
                    .downloadAttachment(f)
                    .catch((err) => toast.error(`Download failed: ${err.message || err}`))
                }
              >
                <Icon name="download" size={12} /> Download
              </button>
              <button
                className="btn btn-sm btn-ghost"
                disabled={remove.isPending}
                onClick={() => {
                  if (confirm(`Delete "${f.filename}"?`)) remove.mutate(f.id);
                }}
                title="Delete file"
              >
                <Icon name="trash" size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
