import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { api, type SummarySearchHit } from "../lib/api";
import type { MeetingListItem } from "../types";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

const MAX_MEETING_RESULTS = 8;
const MAX_SUMMARY_RESULTS = 8;

type SelectableRow =
  | { kind: "meeting"; m: MeetingListItem }
  | { kind: "summary"; hit: SummarySearchHit };

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const { data: meetings = [] } = useQuery({
    queryKey: ["meetings", { all: true }],
    queryFn: () => api.meetings({ past_days: 3650, future_days: 365 }),
  });

  // Summary full-text search — only fires once the user types at least 2 chars.
  const debouncedQuery = useDebouncedValue(query.trim(), 180);
  const summaryHits = useQuery({
    queryKey: ["search-summaries", debouncedQuery],
    queryFn: () => api.searchSummaries(debouncedQuery),
    enabled: open && debouncedQuery.length >= 2,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const meetingMatches = useMemo<MeetingListItem[]>(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      return [...meetings]
        .sort((a, b) => b.meeting_date.localeCompare(a.meeting_date))
        .slice(0, MAX_MEETING_RESULTS);
    }
    return meetings
      .filter((m) => {
        const hay = `${m.title} ${m.type_name} ${m.venue} ${m.type_short} ${m.location} ${m.tags.join(" ")}`.toLowerCase();
        return hay.includes(q);
      })
      .sort((a, b) => b.meeting_date.localeCompare(a.meeting_date))
      .slice(0, MAX_MEETING_RESULTS);
  }, [meetings, query]);

  const summaryMatches = useMemo<SummarySearchHit[]>(() => {
    return (summaryHits.data ?? []).slice(0, MAX_SUMMARY_RESULTS);
  }, [summaryHits.data]);

  // Flat list used for keyboard navigation.
  const rows = useMemo<SelectableRow[]>(() => {
    return [
      ...meetingMatches.map<SelectableRow>((m) => ({ kind: "meeting", m })),
      ...summaryMatches.map<SelectableRow>((hit) => ({ kind: "summary", hit })),
    ];
  }, [meetingMatches, summaryMatches]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  if (!open) return null;

  const activate = (row: SelectableRow) => {
    if (row.kind === "meeting") {
      navigate(`/meeting/${row.m.id}`);
    } else {
      // Summaries route to the briefing reader for meeting-level hits, and
      // the meeting page for agenda-item hits (which expands the item).
      if (row.hit.entity_type === "meeting") {
        navigate(`/briefing/${row.hit.meeting_id}`);
      } else {
        navigate(`/meeting/${row.hit.meeting_id}`);
      }
    }
    onClose();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, Math.max(rows.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const r = rows[selectedIndex];
      if (r) activate(r);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  let runningIndex = 0;

  return (
    <div
      className="cmd-palette-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="cmd-palette" role="dialog" aria-label="Search meetings + summaries">
        <div className="cmd-palette-inputrow">
          <Icon name="search" size={14} />
          <input
            ref={inputRef}
            className="cmd-palette-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search meetings + summary text…"
          />
          <span className="kbd">esc</span>
        </div>

        {rows.length === 0 ? (
          <div className="cmd-palette-empty">
            {query.trim().length >= 2
              ? summaryHits.isFetching
                ? "Searching…"
                : "No matches."
              : "Type to search."}
          </div>
        ) : (
          <div className="cmd-palette-results" role="listbox">
            {meetingMatches.length > 0 && (
              <div className="cmd-palette-group">Meetings</div>
            )}
            {meetingMatches.map((m) => {
              const i = runningIndex++;
              return (
                <button
                  key={`m-${m.id}`}
                  type="button"
                  role="option"
                  aria-selected={i === selectedIndex}
                  className={`cmd-palette-row ${i === selectedIndex ? "active" : ""}`}
                  onMouseEnter={() => setSelectedIndex(i)}
                  onClick={() => activate({ kind: "meeting", m })}
                >
                  <span className="cmd-palette-title">{m.title}</span>
                  <span className="cmd-palette-meta">
                    {m.meeting_date} · {m.venue} · {m.type_short}
                  </span>
                </button>
              );
            })}

            {summaryMatches.length > 0 && (
              <div className="cmd-palette-group">Summary text</div>
            )}
            {summaryMatches.map((hit) => {
              const i = runningIndex++;
              const label =
                hit.entity_type === "meeting"
                  ? `Briefing · ${hit.venue} ${hit.type_short} ${hit.meeting_date}`
                  : `Item ${hit.item_id ?? ""} · ${hit.item_title ?? ""}`;
              return (
                <button
                  key={`s-${hit.entity_type}-${hit.entity_id}`}
                  type="button"
                  role="option"
                  aria-selected={i === selectedIndex}
                  className={`cmd-palette-row ${i === selectedIndex ? "active" : ""}`}
                  onMouseEnter={() => setSelectedIndex(i)}
                  onClick={() => activate({ kind: "summary", hit })}
                >
                  <span
                    className="cmd-palette-title"
                    // `ts_headline` returns HTML with <b> wrapping matches.
                    dangerouslySetInnerHTML={{ __html: hit.snippet || label }}
                  />
                  <span className="cmd-palette-meta">{label}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return v;
}
