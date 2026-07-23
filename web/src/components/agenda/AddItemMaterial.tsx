import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { Segmented } from "../Segmented";
import { api } from "../../lib/api";
import { qk, useCan } from "../../lib/queries";

export function AddItemMaterial({
  itemId,
  meetingId,
  onAdded,
}: {
  itemId: number;
  meetingId: number;
  onAdded: () => void;
}) {
  const qc = useQueryClient();
  const { canEdit } = useCan();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"link" | "upload">("link");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<{
    filename: string;
    summarizable: boolean;
  } | null>(null);

  function refresh() {
    qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
    qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
  }

  async function submitUrl() {
    const u = url.trim();
    if (!u) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.addItemMaterialUrl(itemId, u);
      setUrl("");
      setAdded({
        filename: res.document.filename,
        summarizable: res.summarizable,
      });
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      let last: { filename: string; summarizable: boolean } | null = null;
      for (const file of Array.from(files)) {
        const res = await api.addItemMaterialFile(itemId, file);
        last = {
          filename: res.document.filename,
          summarizable: res.summarizable,
        };
      }
      if (last) setAdded(last);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Render-level gate: this component calls the api directly (no mutations),
  // so hiding it is the whole story for read-only roles.
  if (!canEdit) return null;

  if (!open) {
    return (
      <button
        className="add-material-toggle"
        onClick={() => {
          setOpen(true);
          setAdded(null);
        }}
      >
        <Icon name="plus" size={12} /> Add material to this section
      </button>
    );
  }

  return (
    <div className="add-material">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <Segmented
          value={mode}
          onChange={(v) => {
            setMode(v);
            setError(null);
          }}
          options={[
            { value: "link", label: "Link" },
            { value: "upload", label: "Upload" },
          ]}
        />
        <button className="btn btn-sm btn-ghost" onClick={() => setOpen(false)}>
          <Icon name="x" size={12} />
        </button>
      </div>

      {mode === "link" ? (
        <div className="row" style={{ gap: 8 }}>
          <input
            className="input"
            style={{ flex: 1 }}
            type="url"
            placeholder="https://…/memo.pdf"
            value={url}
            disabled={busy}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitUrl();
            }}
          />
          <button
            className="btn btn-sm btn-accent"
            disabled={busy || !url.trim()}
            onClick={submitUrl}
          >
            {busy ? "Adding…" : "Add link"}
          </button>
        </div>
      ) : (
        <div
          className="file-drop"
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
        >
          <Icon name="upload" size={16} />
          <div className="file-drop-text">
            <strong>Click to upload</strong> a file
          </div>
          {busy && <div className="muted text-xs">Uploading…</div>}
          <input
            ref={inputRef}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              submitFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      )}

      <p className="muted text-xs" style={{ marginTop: 8, marginBottom: 0 }}>
        Attaches to this agenda item and feeds its summary. Readable types
        (PDF, Word, PowerPoint, text) are auto-extracted; 25 MB max.
      </p>

      {error && (
        <div className="muted text-xs" style={{ color: "var(--accent)", marginTop: 8 }}>
          {error}
        </div>
      )}

      {added && (
        <div className="add-material-done">
          <div className="text-xs">
            Added <strong>{added.filename}</strong>.{" "}
            {added.summarizable ? (
              "Re-summarize this section to include it:"
            ) : (
              <span style={{ color: "var(--warn, var(--accent))" }}>
                Text couldn't be extracted, so it won't change the summary —
                but it's attached and downloadable.
              </span>
            )}
          </div>
          {added.summarizable && (
            <button
              className="btn btn-sm btn-accent"
              onClick={() => {
                onAdded();
                setAdded(null);
                setOpen(false);
              }}
            >
              <Icon name="spark" size={12} /> Re-summarize section
            </button>
          )}
        </div>
      )}
    </div>
  );
}
