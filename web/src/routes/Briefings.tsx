import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { Pill } from "../components/Pill";
import { VenueTag, TypeTag, Tag } from "../components/Tag";
import { api } from "../lib/api";
import { fmtDateRange, monthLabel, dayNumber } from "../lib/format";
import type { MeetingListItem } from "../types";

type Venue = "All" | "ISO-NE" | "NYISO";

export function Briefings() {
  const navigate = useNavigate();
  const [venueFilter, setVenueFilter] = useState<Venue>("All");
  const [search, setSearch] = useState("");

  const { data: meetings = [] } = useQuery({
    queryKey: ["meetings", { all: true }],
    queryFn: () => api.meetings({ past_days: 3650, future_days: 365 }),
  });

  // A "briefing" exists when status is summarized or updated.
  const briefings = useMemo(
    () => meetings.filter((m) => m.status === "summarized" || m.status === "updated"),
    [meetings]
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return briefings.filter((m) => {
      if (venueFilter !== "All" && m.venue !== venueFilter) return false;
      if (q) {
        const hay = `${m.title} ${m.type_name} ${m.venue} ${m.type_short} ${m.location} ${m.tags.join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [briefings, venueFilter, search]);

  const sorted = useMemo(
    () =>
      [...filtered].sort((a, b) =>
        b.meeting_date.localeCompare(a.meeting_date)
      ),
    [filtered]
  );

  const openBriefing = (m: MeetingListItem) => navigate(`/briefing/${m.id}`);

  return (
    <>
      <Topbar crumbs={[{ label: "Briefings" }]} />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Published briefings · ISO-NE, NYISO</div>
          <h1 className="page-title">Briefings</h1>
          <p className="page-subtitle">
            Every meeting with an AI-generated briefing. {sorted.length} of {briefings.length} shown.
          </p>
        </div>

        <div className="filter-bar" style={{ marginBottom: 16 }}>
          <Segmented
            value={venueFilter}
            onChange={setVenueFilter}
            options={[
              { value: "All", label: "All" },
              { value: "ISO-NE", label: "ISO-NE" },
              { value: "NYISO", label: "NYISO" },
            ]}
          />
          <div className="spacer" />
          <div
            className="row"
            style={{
              gap: 6,
              background: "var(--bg-elev)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: "4px 10px",
              minWidth: 280,
            }}
          >
            <Icon name="search" size={13} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search briefings…"
              style={{
                border: 0,
                outline: 0,
                background: "transparent",
                color: "inherit",
                fontSize: 13,
                width: "100%",
                fontFamily: "inherit",
              }}
            />
            {search && (
              <button
                className="btn btn-ghost btn-sm"
                style={{ padding: "0 4px" }}
                onClick={() => setSearch("")}
              >
                <Icon name="x" size={11} />
              </button>
            )}
          </div>
        </div>

        {sorted.length === 0 ? (
          <div className="empty">No briefings match.</div>
        ) : (
          <div className="mtg-list">
            {sorted.map((m) => (
              <button
                key={m.id}
                className="mtg-row"
                onClick={() => openBriefing(m)}
              >
                <div className="mtg-row-date">
                  <div className="mtg-row-month">{monthLabel(m.meeting_date)}</div>
                  <div className="mtg-row-day">{dayNumber(m.meeting_date)}</div>
                </div>
                <div className="mtg-row-venue">
                  <VenueTag>{m.venue}</VenueTag>
                  <TypeTag>{m.type_short}</TypeTag>
                </div>
                <div className="mtg-row-title">
                  <div className="title-line">{m.type_name}</div>
                  <div className="meta-line">
                    {m.location} · {fmtDateRange(m.meeting_date, m.end_date)}
                  </div>
                </div>
                <div className="mtg-row-stats">
                  {m.doc_count > 0 && (
                    <span>
                      <span className="mono">{m.doc_count}</span> docs
                    </span>
                  )}
                  {m.item_count > 0 && (
                    <span>
                      <span className="mono">{m.item_count}</span> items
                    </span>
                  )}
                </div>
                <div className="mtg-row-tags">
                  {m.tags.slice(0, 2).map((t) => (
                    <Tag key={t}>{t}</Tag>
                  ))}
                  {m.tags.length > 2 && (
                    <span className="muted text-xs">+{m.tags.length - 2}</span>
                  )}
                </div>
                <div className="mtg-row-status">
                  <Pill status={m.status} />
                </div>
                <div className="mtg-row-chev">
                  <Icon name="chev-r" size={14} />
                </div>
              </button>
            ))}
          </div>
        )}

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
