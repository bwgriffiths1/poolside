import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { Markdown } from "../lib/markdown";
import { api, type SummaryVersionMeta } from "../lib/api";

interface VersionHistoryProps {
  entityType: "meeting" | "agenda_item";
  entityId: number;
  meetingId: number;
  /** Currently displayed version's id, used to indicate "current" in the list */
  currentVersionId?: number | null;
  /** Called after a successful restore so the parent can collapse the panel */
  onRestored?: () => void;
}

export function VersionHistory({
  entityType,
  entityId,
  meetingId,
  currentVersionId,
  onRestored,
}: VersionHistoryProps) {
  const qc = useQueryClient();
  const { data: versions = [], isLoading } = useQuery({
    queryKey: ["summary-versions", entityType, entityId],
    queryFn: () => api.listSummaryVersions(entityType, entityId),
  });

  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [previewContent, setPreviewContent] = useState<string>("");
  const [previewLoading, setPreviewLoading] = useState(false);

  const restore = useMutation({
    mutationFn: (version_id: number) =>
      api.restoreSummaryVersion(entityType, entityId, version_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["summary-versions", entityType, entityId] });
      qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
      qc.invalidateQueries({ queryKey: ["summary", entityType, entityId] });
      qc.invalidateQueries({ queryKey: ["briefing", meetingId] });
      onRestored?.();
    },
    onError: (e: Error) => alert(`Restore failed: ${e.message}`),
  });

  const togglePreview = async (v: SummaryVersionMeta) => {
    if (expandedId === v.id) {
      setExpandedId(null);
      setPreviewContent("");
      return;
    }
    setExpandedId(v.id);
    setPreviewLoading(true);
    try {
      const full = await api.getSummaryVersion(entityType, entityId, v.id);
      setPreviewContent(full.detailed || full.one_line || "(empty)");
    } catch (e) {
      setPreviewContent(`(failed to load: ${e})`);
    } finally {
      setPreviewLoading(false);
    }
  };

  if (isLoading) return <div className="muted text-sm">Loading versions…</div>;
  if (versions.length === 0)
    return <div className="muted text-sm">No versions saved.</div>;

  return (
    <div className="version-history">
      {versions.map((v) => {
        const isCurrent = v.id === currentVersionId;
        const isActive =
          v.status === "approved" || v.status === "draft";
        const expanded = expandedId === v.id;
        return (
          <div
            key={v.id}
            className={`version-row ${isCurrent ? "is-current" : ""} ${v.status}`}
          >
            <div className="version-row-head">
              <span className="version-num mono">v{v.version}</span>
              <span className={`version-status status-${v.status}`}>
                {v.status}
              </span>
              {v.is_manual ? (
                <span className="version-author">manual</span>
              ) : v.model_id ? (
                <span className="version-author mono">{v.model_id}</span>
              ) : (
                <span className="version-author">—</span>
              )}
              <span className="muted text-xs">
                {v.created_by ? `by ${v.created_by}` : ""}{" "}
                {v.created_at ? fmtRel(v.created_at) : ""}
              </span>
              <span className="muted text-xs">
                {(v.size / 1024).toFixed(1)} kB
              </span>
              <span className="spacer" />
              {isCurrent && (
                <span className="version-current-badge">current</span>
              )}
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => togglePreview(v)}
                title="Preview this version"
              >
                <Icon name={expanded ? "chev-d" : "chev-r"} size={11} />{" "}
                {expanded ? "Hide" : "View"}
              </button>
              {!isCurrent && isActive && (
                <button
                  className="btn btn-sm"
                  disabled={restore.isPending}
                  onClick={() => {
                    if (
                      window.confirm(
                        `Restore v${v.version} as the current summary? The current version will be superseded but kept in history.`
                      )
                    ) {
                      restore.mutate(v.id);
                    }
                  }}
                >
                  <Icon name="refresh" size={11} /> Restore
                </button>
              )}
            </div>
            {!v.is_manual && v.preview && !expanded && (
              <div className="version-preview muted text-xs">{v.preview}</div>
            )}
            {expanded && (
              <div className="version-body">
                {previewLoading ? (
                  <div className="muted text-sm">Loading…</div>
                ) : (
                  <Markdown source={previewContent} className="agenda-summary-body" />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function fmtRel(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const ms = Date.now() - t;
  if (ms < 60_000) return "just now";
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}
