import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { MeetingRow } from "../components/MeetingRow";
import { useMeetingsAll } from "../lib/queries";
import { addDays, localIso } from "../lib/dates";
import type { MeetingListItem, MeetingStatus } from "../types";

type StatusFilter = "materials+" | "all" | MeetingStatus;
type View = "list" | "card";
type DateRange = "all" | "upcoming" | "past30" | "past90" | "pastyear";

// The default view. Far-future rows are calendar stubs with no agenda and no
// documents — they're the bulk of the table and there's nothing to read yet,
// so the page opens on meetings that actually have materials. Overview and
// Calendar are where you go to see what's merely scheduled.
const WITH_MATERIALS: MeetingStatus[] = ["materials", "summarized", "updated"];

const LOOKBACK_DAYS: Record<string, number> = {
  past30: 30,
  past90: 90,
  pastyear: 365,
};

export function Meetings() {
  const navigate = useNavigate();
  const [view, setView] = useState<View>("list");
  const [venueFilter, setVenueFilter] = useState<string>("All");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("materials+");
  const [typeFilter, setTypeFilter] = useState<string>("All");
  const [dateRange, setDateRange] = useState<DateRange>("all");
  const [search, setSearch] = useState("");

  const { data: meetings = [] } = useMeetingsAll();

  // Venue and committee options are derived from the data, so a new venue
  // appears on its own instead of needing a hard-coded option (the old
  // All | ISO-NE pair was a no-op once ISO-NE was the only venue shown).
  const venues = ["All", ...Array.from(new Set(meetings.map((m) => m.venue))).sort()];

  // Committee options are scoped to the venue filter — a PJM view shouldn't
  // offer ISO-NE committees. With no venue selected, the dropdown groups by
  // venue so it's clear which ISO each committee belongs to.
  const typesByVenue = new Map<string, string[]>();
  for (const m of meetings) {
    const list = typesByVenue.get(m.venue) ?? [];
    if (!list.includes(m.type_short)) list.push(m.type_short);
    typesByVenue.set(m.venue, list);
  }
  typesByVenue.forEach((list) => list.sort());
  const groupedVenues = Array.from(typesByVenue.keys()).sort();
  const visibleTypes =
    venueFilter === "All"
      ? Array.from(new Set(meetings.map((m) => m.type_short))).sort()
      : (typesByVenue.get(venueFilter) ?? []);
  // A committee picked under one venue filter may not exist under the next —
  // treat a vanished selection as "All" instead of silently matching nothing.
  const effectiveType = visibleTypes.includes(typeFilter) ? typeFilter : "All";

  // Local dates — toISOString() is UTC and would flip "upcoming" an evening
  // early; addDays() builds from date parts so DST can't shift the cutoff.
  const now = new Date();
  const todayIso = localIso(now);
  const lookbackDays = LOOKBACK_DAYS[dateRange];
  const cutoffIso = lookbackDays ? localIso(addDays(now, -lookbackDays)) : "";

  const q = search.trim().toLowerCase();
  const filtered = meetings.filter((m) => {
    if (venueFilter !== "All" && m.venue !== venueFilter) return false;
    if (statusFilter === "materials+") {
      if (!WITH_MATERIALS.includes(m.status)) return false;
    } else if (statusFilter !== "all" && m.status !== statusFilter) {
      return false;
    }
    if (effectiveType !== "All" && m.type_short !== effectiveType) return false;
    if (dateRange === "upcoming" && m.meeting_date < todayIso) return false;
    if (lookbackDays) {
      // Bound BOTH ends — these used to leave the future unbounded, so
      // "30 d" still dragged in every stub a year out.
      if (m.meeting_date > todayIso || m.meeting_date < cutoffIso) return false;
    }
    if (q) {
      const hay = `${m.title} ${m.type_name} ${m.venue} ${m.type_short} ${m.location} ${m.tags.join(" ")}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  // Split at today, each half ordered nearest-to-now first: the two rows
  // either side of the divider are "next up" and "most recent".
  const upcoming = filtered
    .filter((m) => m.meeting_date >= todayIso)
    .sort((a, b) => a.meeting_date.localeCompare(b.meeting_date));
  const past = filtered
    .filter((m) => m.meeting_date < todayIso)
    .sort((a, b) => b.meeting_date.localeCompare(a.meeting_date));

  // A divider only earns its space when both halves have rows.
  const showDividers = upcoming.length > 0 && past.length > 0;

  const openMeeting = (m: MeetingListItem) => navigate(`/meeting/${m.id}`);

  const countBy = (s: MeetingStatus) => meetings.filter((m) => m.status === s).length;
  const withMaterialsCount = meetings.filter((m) =>
    WITH_MATERIALS.includes(m.status),
  ).length;

  const renderGroup = (list: MeetingListItem[]) =>
    view === "list" ? (
      <div className="mtg-list">
        {list.map((m) => (
          <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="list" />
        ))}
      </div>
    ) : (
      <div className="mtg-cards">
        {list.map((m) => (
          <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="card" />
        ))}
      </div>
    );

  return (
    <>
      <Topbar
        crumbs={[{ label: "Meetings" }]}
        actions={
          <button
            className="btn btn-sm btn-primary"
            onClick={() => navigate("/add")}
          >
            <Icon name="plus" /> Add meeting
          </button>
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">All meetings</div>
          <h1 className="page-title">Meetings</h1>
          <p className="page-subtitle">
            Searchable archive of every meeting on file — filter by venue,
            committee, status, and date. Showing {filtered.length} of{" "}
            {meetings.length}
            {statusFilter === "materials+" && (
              <>
                {" "}
                — those with materials posted.{" "}
                <button
                  type="button"
                  className="link-btn"
                  onClick={() => setStatusFilter("all")}
                >
                  Show all
                </button>
              </>
            )}
            {statusFilter !== "materials+" && "."}
          </p>
        </div>

        <div className="filter-bar" style={{ marginBottom: 16 }}>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {venues.length > 2 && (
              <Segmented
                value={venueFilter}
                onChange={setVenueFilter}
                options={venues.map((v) => ({ value: v, label: v }))}
              />
            )}
            <Segmented
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: "materials+", label: `Materials + (${withMaterialsCount})` },
                { value: "all", label: `All (${meetings.length})` },
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
            value={effectiveType}
            onChange={(e) => setTypeFilter(e.target.value)}
            style={{ width: 160 }}
          >
            <option value="All">All committees</option>
            {venueFilter === "All" && groupedVenues.length > 1
              ? groupedVenues.map((v) => (
                  <optgroup key={v} label={v}>
                    {(typesByVenue.get(v) ?? []).map((t) => (
                      <option key={`${v}:${t}`} value={t}>
                        {t}
                      </option>
                    ))}
                  </optgroup>
                ))
              : visibleTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
          </select>
          <Segmented
            value={dateRange}
            onChange={setDateRange}
            options={[
              { value: "all", label: "All time" },
              { value: "upcoming", label: "Upcoming" },
              { value: "past30", label: "Past 30 d" },
              { value: "past90", label: "Past 90 d" },
              { value: "pastyear", label: "Past year" },
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

        {filtered.length === 0 ? (
          <div className="empty">No meetings match these filters.</div>
        ) : (
          <>
            {upcoming.length > 0 && (
              <>
                {showDividers && (
                  <div className="mtg-split">
                    Upcoming <span className="count">{upcoming.length}</span>
                  </div>
                )}
                {renderGroup(upcoming)}
              </>
            )}
            {past.length > 0 && (
              <>
                {showDividers && (
                  <div className="mtg-split">
                    Past <span className="count">{past.length}</span>
                  </div>
                )}
                {renderGroup(past)}
              </>
            )}
          </>
        )}

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
