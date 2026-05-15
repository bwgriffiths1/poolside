import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { MeetingRow } from "../components/MeetingRow";
import { api } from "../lib/api";
import type { MeetingListItem, MeetingStatus } from "../types";

type Venue = "All" | "ISO-NE" | "NYISO";
type StatusFilter = "All" | MeetingStatus;
type View = "list" | "card";
type DateRange = "all" | "upcoming" | "30d" | "90d" | "year";

export function Meetings() {
  const navigate = useNavigate();
  const [view, setView] = useState<View>("list");
  const [venueFilter, setVenueFilter] = useState<Venue>("All");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("All");
  const [typeFilter, setTypeFilter] = useState<string>("All");
  const [dateRange, setDateRange] = useState<DateRange>("all");
  const [search, setSearch] = useState("");

  const { data: meetings = [] } = useQuery({
    queryKey: ["meetings", { all: true }],
    queryFn: () => api.meetings({ past_days: 3650, future_days: 365 }),
  });

  // All distinct type_short values for the type dropdown.
  const types = useMemo(() => {
    const seen = new Set<string>();
    meetings.forEach((m) => seen.add(m.type_short));
    return ["All", ...Array.from(seen).sort()];
  }, [meetings]);

  const filtered = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const cutoff = (() => {
      const d = new Date();
      if (dateRange === "30d") d.setDate(d.getDate() - 30);
      else if (dateRange === "90d") d.setDate(d.getDate() - 90);
      else if (dateRange === "year") d.setDate(d.getDate() - 365);
      return d.toISOString().slice(0, 10);
    })();
    const q = search.trim().toLowerCase();
    return meetings.filter((m) => {
      if (venueFilter !== "All" && m.venue !== venueFilter) return false;
      if (statusFilter !== "All" && m.status !== statusFilter) return false;
      if (typeFilter !== "All" && m.type_short !== typeFilter) return false;
      if (dateRange === "upcoming" && m.meeting_date < today) return false;
      if (dateRange !== "all" && dateRange !== "upcoming" && m.meeting_date < cutoff) return false;
      if (q) {
        const hay = `${m.title} ${m.type_name} ${m.venue} ${m.type_short} ${m.location} ${m.tags.join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [meetings, venueFilter, statusFilter, typeFilter, dateRange, search]);

  const sorted = useMemo(
    () =>
      [...filtered].sort((a, b) =>
        b.meeting_date.localeCompare(a.meeting_date)
      ),
    [filtered]
  );

  const openMeeting = (m: MeetingListItem) => navigate(`/meeting/${m.id}`);

  // Per-status counts (for the segmented control labels)
  const countBy = (s: MeetingStatus) =>
    meetings.filter((m) => m.status === s).length;

  return (
    <>
      <Topbar
        crumbs={[{ label: "Meetings" }]}
        actions={
          <>
            <button className="btn btn-sm" onClick={() => navigate("/add")}>
              <Icon name="refresh" /> Refresh calendars
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => navigate("/add")}
            >
              <Icon name="plus" /> Add meeting
            </button>
          </>
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">All meetings · ISO-NE, NYISO</div>
          <h1 className="page-title">Meetings</h1>
          <p className="page-subtitle">
            Every meeting in the database — filterable by venue, type, status,
            and date. Showing {sorted.length} of {meetings.length}.
          </p>
        </div>

        <div className="filter-bar" style={{ marginBottom: 16 }}>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            <Segmented
              value={venueFilter}
              onChange={setVenueFilter}
              options={[
                { value: "All", label: "All" },
                { value: "ISO-NE", label: "ISO-NE" },
                { value: "NYISO", label: "NYISO" },
              ]}
            />
            <Segmented
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: "All", label: `All (${meetings.length})` },
                { value: "scheduled", label: `Scheduled (${countBy("scheduled")})` },
                { value: "materials", label: `Materials (${countBy("materials")})` },
                { value: "summarized", label: `Summarized (${countBy("summarized")})` },
                { value: "updated", label: `Updated (${countBy("updated")})` },
              ]}
            />
          </div>
          <div className="spacer" />
          <Segmented
            value={view}
            onChange={setView}
            options={[
              {
                value: "list",
                label: (
                  <>
                    <Icon name="list" /> List
                  </>
                ),
              },
              {
                value: "card",
                label: (
                  <>
                    <Icon name="dot" /> Cards
                  </>
                ),
              },
            ]}
          />
        </div>

        <div className="filter-bar" style={{ marginBottom: 16, gap: 12 }}>
          <select
            className="select"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            style={{ width: 160 }}
          >
            {types.map((t) => (
              <option key={t} value={t}>
                {t === "All" ? "All committees" : t}
              </option>
            ))}
          </select>
          <Segmented
            value={dateRange}
            onChange={setDateRange}
            options={[
              { value: "all", label: "All time" },
              { value: "upcoming", label: "Upcoming" },
              { value: "30d", label: "30 d" },
              { value: "90d", label: "90 d" },
              { value: "year", label: "1 yr" },
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
              minWidth: 240,
            }}
          >
            <Icon name="search" size={13} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by title, tag, location…"
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
          <div className="empty">No meetings match these filters.</div>
        ) : view === "list" ? (
          <div className="mtg-list">
            {sorted.map((m) => (
              <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="list" />
            ))}
          </div>
        ) : (
          <div className="mtg-cards">
            {sorted.map((m) => (
              <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="card" />
            ))}
          </div>
        )}

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
