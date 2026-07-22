import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { api } from "../lib/api";
import { qk } from "../lib/queries";
import { toast } from "../lib/toast";
import { Markdown } from "../lib/markdown";

type EntityType = "meeting" | "agenda_item" | "docket" | "docket_filing";
type ViewMode = "split" | "source" | "preview";

export function Editor() {
  const { type, id } = useParams<{ type: EntityType; id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const entityType = (
    type === "meeting" || type === "docket" || type === "docket_filing"
      ? type
      : "agenda_item"
  ) as EntityType;
  const entityId = Number(id);

  const { data, isLoading } = useQuery({
    queryKey: qk.summary(entityType, entityId),
    queryFn: () => api.getSummary(entityType, entityId),
  });

  const [view, setView] = useState<ViewMode>("split");
  const [body, setBody] = useState("");
  const [oneLine, setOneLine] = useState("");
  const [dirty, setDirty] = useState(false);
  // The server version our editor state was seeded from — when a background
  // refetch brings a newer one while `dirty`, we keep the user's text and
  // show the conflict banner instead of silently clobbering their edits.
  const [loadedVersion, setLoadedVersion] = useState<number | null>(null);
  const loadedKeyRef = useRef<string | null>(null);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!data) return;
    const key = `${data.entity_type}:${data.entity_id}`;
    if (key === loadedKeyRef.current && dirtyRef.current) return;
    loadedKeyRef.current = key;
    setBody(data.detailed || "");
    setOneLine(data.one_line || "");
    setDirty(false);
    setLoadedVersion(data.version);
  }, [data?.entity_type, data?.entity_id, data?.version]);

  const serverMovedOn =
    dirty && data != null && data.version !== loadedVersion;

  const loadServerVersion = () => {
    if (!data) return;
    setBody(data.detailed || "");
    setOneLine(data.one_line || "");
    setDirty(false);
    setLoadedVersion(data.version);
  };

  const save = useMutation({
    mutationFn: () =>
      api.saveSummary(entityType, entityId, {
        one_line: oneLine || undefined,
        detailed: body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.summary(entityType, entityId) });
      qc.invalidateQueries({ queryKey: qk.summaryVersions(entityType, entityId) });
      qc.invalidateQueries({ queryKey: qk.allMeetingDetails });
      qc.invalidateQueries({ queryKey: qk.allBriefings });
      if (data?.docket_id != null) {
        qc.invalidateQueries({ queryKey: qk.docket(data.docket_id) });
        qc.invalidateQueries({ queryKey: qk.dockets });
      }
      setDirty(false);
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  });

  // Cmd/Ctrl-S to save
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        if (dirty && !save.isPending) save.mutate();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, save]);

  // ── Toolbar action: insert / wrap markdown at cursor ──────────────────────
  const transformSelection = (
    transform: (selection: string, before: string, after: string) => {
      replacement: string;
      cursorOffset?: number;
    }
  ) => {
    const t = textareaRef.current;
    if (!t) return;
    const start = t.selectionStart;
    const end = t.selectionEnd;
    const before = t.value.slice(0, start);
    const sel = t.value.slice(start, end);
    const after = t.value.slice(end);
    const { replacement, cursorOffset } = transform(sel, before, after);
    const next = before + replacement + after;
    setBody(next);
    setDirty(true);
    // Restore selection on the next tick
    requestAnimationFrame(() => {
      t.focus();
      const pos = start + (cursorOffset ?? replacement.length);
      t.setSelectionRange(pos, pos);
    });
  };

  const wrap = (open: string, close = open, placeholder = "text") =>
    transformSelection((sel) => ({
      replacement: open + (sel || placeholder) + close,
      cursorOffset: sel
        ? open.length + sel.length + close.length
        : open.length + placeholder.length,
    }));

  const prefixLine = (prefix: string) =>
    transformSelection((sel) => {
      // Multi-line: prefix every line. Single-line: prefix at start.
      if (sel.includes("\n")) {
        const replaced = sel
          .split("\n")
          .map((ln) => prefix + ln)
          .join("\n");
        return { replacement: replaced };
      }
      return { replacement: prefix + sel };
    });

  const insertBlock = (block: string) =>
    transformSelection((sel) => {
      const replacement = (sel ? sel + "\n\n" : "") + block + "\n";
      return { replacement };
    });

  const insertCallout = (label: string) =>
    transformSelection((sel) => {
      const body = sel || `${label} body here…`;
      // GitHub-style admonition lines
      const replacement = `\n> [!${label}] ${body}\n\n`;
      return { replacement };
    });

  const insertTable = () =>
    insertBlock(
      [
        "| Column A | Column B | Column C |",
        "|---|---|---|",
        "| Row 1A | Row 1B | Row 1C |",
        "| Row 2A | Row 2B | Row 2C |",
      ].join("\n")
    );

  const [uploadingImage, setUploadingImage] = useState(false);

  // Paste handler: detect image in clipboard, upload, insert markdown.
  const onPaste = async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    let imageItem: DataTransferItem | null = null;
    for (let k = 0; k < items.length; k++) {
      if (items[k].kind === "file" && items[k].type.startsWith("image/")) {
        imageItem = items[k];
        break;
      }
    }
    if (!imageItem) return; // not an image — let the default paste happen

    e.preventDefault();
    // Image paste is backed by editor_images, which is meeting-bound —
    // docket summaries are text-only for now.
    if (entityType === "docket" || entityType === "docket_filing") {
      toast.error("Image paste isn't supported for docket summaries yet.");
      return;
    }
    const file = imageItem.getAsFile();
    if (!file) return;

    // Drop a placeholder at the cursor so the user sees something immediately.
    const placeholder = `\n\n![uploading…](pending)\n\n`;
    let placeholderInserted = false;
    transformSelection(() => {
      placeholderInserted = true;
      return { replacement: placeholder };
    });

    setUploadingImage(true);
    try {
      const buf = await file.arrayBuffer();
      // base64 encode in chunks to avoid stack overflow on big screenshots
      const bytes = new Uint8Array(buf);
      let bin = "";
      const chunk = 0x8000;
      for (let p = 0; p < bytes.length; p += chunk) {
        bin += String.fromCharCode.apply(
          null,
          Array.from(bytes.subarray(p, p + chunk)) as number[]
        );
      }
      const b64 = btoa(bin);

      const res = await api.uploadEditorImage({
        entity_type: entityType,
        entity_id: entityId,
        image_b64: b64,
        mime_type: file.type || "image/png",
        filename: `pasted-${new Date().toISOString().replace(/[:.]/g, "-")}.png`,
      });

      // Swap the placeholder for the real markdown
      const realMd = `![pasted](${res.url})`;
      setBody((prev) => prev.replace("![uploading…](pending)", realMd));
      setDirty(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`Image upload failed: ${msg}`);
      // Remove the placeholder
      if (placeholderInserted) {
        setBody((prev) => prev.replace("![uploading…](pending)", ""));
      }
    } finally {
      setUploadingImage(false);
    }
  };

  const wc = useMemo(() => {
    const words = (body.match(/\b\w+\b/g) || []).length;
    return { words, chars: body.length };
  }, [body]);

  const isDocketEntity =
    entityType === "docket" || entityType === "docket_filing";
  // Where "back" leads: the owning meeting, or the owning docket page.
  const backTo = data
    ? isDocketEntity
      ? `/docket/${data.docket_id}`
      : `/meeting/${data.meeting_id}`
    : null;
  const entityLabel =
    entityType === "meeting"
      ? "Briefing"
      : entityType === "docket"
        ? "State of Play"
        : entityType === "docket_filing"
          ? "Filing"
          : "Item";

  return (
    <>
      <Topbar
        crumbs={[
          entityType === "meeting"
            ? { label: "Briefings", to: "/briefings" }
            : isDocketEntity
              ? { label: "FERC eLibrary", to: "/elibrary" }
              : { label: "Meetings", to: "/meetings" },
          data && backTo
            ? {
                label: `${entityLabel}: ${data.parent_label}`,
                to: backTo,
              }
            : { label: "Loading…" },
          { label: "Edit" },
        ]}
        actions={
          <>
            {data?.is_manual && (
              <span className="text-xs muted">manual edit · v{data.version}</span>
            )}
            <button
              className="btn btn-sm"
              onClick={() => backTo && navigate(backTo)}
            >
              <Icon name="x" size={12} /> Cancel
            </button>
            <button
              className="btn btn-sm btn-accent"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
            >
              <Icon name="check" size={12} />{" "}
              {save.isPending ? "Saving…" : dirty ? "Save" : "Saved"}
            </button>
          </>
        }
      />

      <div className="editor-page">
        {isLoading || !data ? (
          <div className="muted" style={{ padding: "var(--pad-6)" }}>
            Loading…
          </div>
        ) : (
          <>
            <header className="editor-header">
              <div className="page-eyebrow">
                {entityType === "meeting"
                  ? "Meeting briefing"
                  : entityType === "docket"
                    ? "Docket state of play"
                    : entityType === "docket_filing"
                      ? "Filing summary"
                      : "Agenda item summary"}
              </div>
              <h1 className="editor-title">{data.parent_label}</h1>
              <div className="row" style={{ gap: 16, marginTop: 12, flexWrap: "wrap" }}>
                <div style={{ flex: 1, minWidth: 280 }}>
                  <label className="field-label">One-line summary (italic preview)</label>
                  <input
                    className="input"
                    value={oneLine}
                    onChange={(e) => {
                      setOneLine(e.target.value);
                      setDirty(true);
                    }}
                    placeholder="One sentence preview shown under the title…"
                  />
                </div>
                <div style={{ alignSelf: "flex-end" }}>
                  <Segmented
                    value={view}
                    onChange={setView}
                    options={[
                      { value: "source", label: "Source" },
                      { value: "split", label: "Split" },
                      { value: "preview", label: "Preview" },
                    ]}
                  />
                </div>
              </div>
            </header>

            {serverMovedOn && (
              <div
                className="row text-sm"
                style={{
                  margin: "0 0 12px",
                  padding: "10px 12px",
                  border: "1px solid var(--warn)",
                  borderRadius: "var(--radius)",
                  background: "var(--bg-elev)",
                  gap: 12,
                }}
              >
                <span style={{ flex: 1 }}>
                  This summary changed on the server (now v{data.version})
                  while you were editing. Saving will overwrite that version —
                  it stays in history.
                </span>
                <button className="btn btn-sm" onClick={loadServerVersion}>
                  Discard my edits, load v{data.version}
                </button>
              </div>
            )}

            <Toolbar
              onHeading={(n) => prefixLine("#".repeat(n) + " ")}
              onBold={() => wrap("**", "**", "bold")}
              onItalic={() => wrap("*", "*", "italic")}
              onCode={() => wrap("`", "`", "code")}
              onList={() => prefixLine("- ")}
              onOrderedList={() => prefixLine("1. ")}
              onQuote={() => prefixLine("> ")}
              onCallout={insertCallout}
              onTable={insertTable}
              onImage={() => {
                const url = window.prompt(
                  "Image URL or filename (will render as ![alt](url))"
                );
                if (url) wrap(`![image](${url})`, "", "");
              }}
              onLink={() => {
                const url = window.prompt("Link URL");
                if (url) {
                  transformSelection((sel) => ({
                    replacement: `[${sel || "link text"}](${url})`,
                  }));
                }
              }}
              onHR={() => insertBlock("---")}
            />

            <div className={`editor-pane editor-${view}`}>
              {(view === "source" || view === "split") && (
                <textarea
                  ref={textareaRef}
                  className="editor-textarea"
                  value={body}
                  onChange={(e) => {
                    setBody(e.target.value);
                    setDirty(true);
                  }}
                  onPaste={onPaste}
                  spellCheck={true}
                  placeholder={
                    "# Start writing your summary in markdown…\n\n" +
                    "Use the toolbar above for headings, callouts, tables.\n" +
                    "Paste a screenshot from your clipboard to insert an image."
                  }
                />
              )}
              {(view === "preview" || view === "split") && (
                <div className="editor-preview">
                  {oneLine && (
                    <p
                      className="serif"
                      style={{
                        fontSize: 18,
                        fontStyle: "italic",
                        color: "var(--ink-soft)",
                        margin: "0 0 18px",
                        lineHeight: 1.5,
                        maxWidth: "60ch",
                      }}
                    >
                      {oneLine}
                    </p>
                  )}
                  <Markdown source={body} className="editor-preview-body" />
                </div>
              )}
            </div>

            <footer className="editor-statusbar">
              <span className="text-xs muted">
                {wc.words.toLocaleString()} words · {wc.chars.toLocaleString()} chars
              </span>
              <span className="spacer" />
              {uploadingImage && (
                <span className="text-xs" style={{ color: "var(--accent)" }}>
                  uploading image…
                </span>
              )}
              {save.isSuccess && !dirty && !uploadingImage && (
                <span className="text-xs" style={{ color: "var(--success)" }}>
                  ✓ saved
                </span>
              )}
              {dirty && !uploadingImage && (
                <span className="text-xs muted">unsaved changes — ⌘S to save</span>
              )}
            </footer>
          </>
        )}
      </div>
    </>
  );
}

// ─── Toolbar ──────────────────────────────────────────────────────────────

function Toolbar(props: {
  onHeading: (n: 1 | 2 | 3) => void;
  onBold: () => void;
  onItalic: () => void;
  onCode: () => void;
  onList: () => void;
  onOrderedList: () => void;
  onQuote: () => void;
  onCallout: (label: string) => void;
  onTable: () => void;
  onImage: () => void;
  onLink: () => void;
  onHR: () => void;
}) {
  return (
    <div className="editor-toolbar">
      <Group>
        <ToolbarBtn label="H1" onClick={() => props.onHeading(1)} hint="Big heading" />
        <ToolbarBtn label="H2" onClick={() => props.onHeading(2)} hint="Section heading" />
        <ToolbarBtn label="H3" onClick={() => props.onHeading(3)} hint="Sub-heading" />
      </Group>
      <Group>
        <ToolbarBtn label="B" onClick={props.onBold} hint="Bold (Cmd+B)" bold />
        <ToolbarBtn label="I" onClick={props.onItalic} hint="Italic (Cmd+I)" italic />
        <ToolbarBtn label="‹›" onClick={props.onCode} hint="Inline code" mono />
      </Group>
      <Group>
        <ToolbarBtn label="•  list" onClick={props.onList} hint="Bullet list" />
        <ToolbarBtn label="1. list" onClick={props.onOrderedList} hint="Numbered list" />
        <ToolbarBtn label="❝ quote" onClick={props.onQuote} hint="Blockquote" />
      </Group>
      <Group>
        <CalloutMenu onCallout={props.onCallout} />
      </Group>
      <Group>
        <ToolbarBtn label="link" onClick={props.onLink} hint="Insert link" />
        <ToolbarBtn label="image" onClick={props.onImage} hint="Insert image" />
        <ToolbarBtn label="table" onClick={props.onTable} hint="Insert table" />
        <ToolbarBtn label="—" onClick={props.onHR} hint="Horizontal rule" />
      </Group>
    </div>
  );
}

function Group({ children }: { children: React.ReactNode }) {
  return <div className="editor-toolbar-group">{children}</div>;
}

function ToolbarBtn({
  label,
  hint,
  onClick,
  bold,
  italic,
  mono,
}: {
  label: string;
  hint?: string;
  onClick: () => void;
  bold?: boolean;
  italic?: boolean;
  mono?: boolean;
}) {
  return (
    <button
      type="button"
      className="editor-toolbar-btn"
      title={hint}
      onClick={onClick}
      style={{
        fontWeight: bold ? 700 : undefined,
        fontStyle: italic ? "italic" : undefined,
        fontFamily: mono ? "var(--font-mono)" : undefined,
      }}
    >
      {label}
    </button>
  );
}

function CalloutMenu({ onCallout }: { onCallout: (label: string) => void }) {
  const [open, setOpen] = useState(false);
  const labels = ["Position", "Next Steps", "Note", "Warning", "Decision", "Risk"];
  return (
    <div className="editor-callout-menu">
      <button
        type="button"
        className="editor-toolbar-btn"
        onClick={() => setOpen(!open)}
        title="Insert callout"
      >
        callout ▾
      </button>
      {open && (
        <div className="editor-callout-dropdown" onMouseLeave={() => setOpen(false)}>
          {labels.map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => {
                onCallout(l);
                setOpen(false);
              }}
            >
              {l}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
