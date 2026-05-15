import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { MeetingRow } from "../components/MeetingRow";
import { api } from "../lib/api";
import { TODAY } from "../lib/fixtures";
import type { MeetingListItem, MeetingStatus } from "../types";

type Venue = "All" | "ISO-NE" | "NYISO";
type StatusFilter = "All" | MeetingStatus;
type View = "list" | "card";

export function Overview() {
  const navigate = useNavigate();
  const [view, setView] = useState<View>("list");
  const [venueFilter, setVenueFilter] = useState<Venue>("All");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("All");

  const { data: meetings = [] } = useQuery({
    queryKey: ["meetings", { past_days: 730, future_days: 365 }],
    queryFn: () => api.meetings({ past_days: 730, future_days: 365 }),
  });

  const filtered = useMemo(() => {
    return meetings.filter((m) => {
      if (venueFilter !== "All" && m.venue !== venueFilter) return false;
      if (statusFilter !== "All" && m.status !== statusFilter) return false;
      return true;
    });
  }, [meetings, venueFilter, statusFilter]);

  const upcoming = useMemo(
    () =>
      filtered
        .filter((m) => m.meeting_date >= TODAY)
        .sort((a, b) => a.meeting_date.localeCompare(b.meeting_date)),
    [filtered]
  );
  const past = useMemo(
    () =>
      filtered
        .filter((m) => m.meeting_date < TODAY)
        .sort((a, b) => b.meeting_date.localeCompare(a.meeting_date)),
    [filtered]
  );

  const summarizedThisMonth = meetings.filter(
    (m) => m.status === "summarized" && m.meeting_date.startsWith(TODAY.slice(0, 7))
  ).length;
  const pendingReview = meetings.filter((m) => m.status === "materials").length;

  const openMeeting = (m: MeetingListItem) => navigate(`/meeting/${m.id}`);

  return (
    <>
      <Topbar
        crumbs={[{ label: "Overview" }]}
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
          <div className="page-eyebrow">Meeting calendar · ISO-NE, NYISO</div>
          <h1 className="page-title">Good morning, Ben.</h1>
          <p className="page-subtitle">
            {pendingReview > 0 ? (
              <>
                <span style={{ color: "var(--ink)" }}>
                  {pendingReview} meetings
                </span>{" "}
                with materials ready to summarize.{" "}
              </>
            ) : null}
            {summarizedThisMonth} briefings published this month.
          </p>
        </div>

        <div className="kpi-grid">
          <div className="kpi">
            <div className="kpi-label">Upcoming</div>
            <div className="kpi-num">{upcoming.length}</div>
            <div className="kpi-sub">next 30 days</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">Materials ready</div>
            <div className="kpi-num">{pendingReview}</div>
            <div className="kpi-sub">awaiting briefing</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">Summarized</div>
            <div className="kpi-num">{summarizedThisMonth}</div>
            <div className="kpi-sub">this month</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">Hours saved</div>
            <div className="kpi-num">~64</div>
            <div className="kpi-sub">vs. manual briefing time</div>
          </div>
        </div>

        <div className="filter-bar">
          <div className="row" style={{ gap: 6 }}>
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
                { value: "All", label: "All" },
                { value: "scheduled", label: "Scheduled" },
                { value: "materials", label: "Materials" },
                { value: "summarized", label: "Summarized" },
                { value: "updated", label: "Updated" },
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

        <div className="section-h">
          <h2>Upcoming</h2>
          <span className="meta">{upcoming.length} meetings</span>
        </div>
        {view === "list" ? (
          <div className="mtg-list">
            {upcoming.length === 0 ? (
              <div className="empty">Nothing on the calendar.</div>
            ) : (
              upcoming.map((m) => (
                <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="list" />
              ))
            )}
          </div>
        ) : (
          <div className="mtg-cards">
            {upcoming.map((m) => (
              <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="card" />
            ))}
          </div>
        )}

        <div className="section-h">
          <h2>Recent</h2>
          <span className="meta">{past.length} meetings</span>
        </div>
        {view === "list" ? (
          <div className="mtg-list">
            {past.map((m) => (
              <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="list" />
            ))}
          </div>
        ) : (
          <div className="mtg-cards">
            {past.map((m) => (
              <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="card" />
            ))}
          </div>
        )}

        <div style={{ height: 48 }} />
      </div>
    </>
  );
}
