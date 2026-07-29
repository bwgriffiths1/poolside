import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { MeetingRow } from "../components/MeetingRow";
import { useMeetingsAll } from "../lib/queries";
import { fmtDateRange } from "../lib/format";
import {
  addDays,
  addMonths,
  atNoon,
  currentMonth,
  localIso,
  mondayOf,
  monthStart,
  monthTitle,
  parseMonthParam,
} from "../lib/dates";
import type { MeetingListItem } from "../types";

// Longest span a meeting can paint on the grid — a bad end_date from a
// scraper shouldn't flood a month with chips.
const MAX_SPAN_DAYS = 14;
const MAX_VISIBLE_CHIPS = 3;

interface SpanSeg {
  m: MeetingListItem;
  startCol: number; // 0-based weekday column, inclusive
  endCol: number;
  contLeft: boolean; // meeting started before this segment (prior week)
  contRight: boolean; // meeting continues past this segment
}

interface Week {
  days: string[]; // 5 ISO dates, Mon–Fri
  lanes: SpanSeg[][]; // one row per stack of non-overlapping spans
}

export function Calendar() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const month = parseMonthParam(params.get("m")) ?? currentMonth();
  const [expandedDays, setExpandedDays] = useState<Set<string>>(new Set());

  const setMonth = (ym: string) => {
    const next = new URLSearchParams(params);
    if (ym === currentMonth()) next.delete("m");
    else next.set("m", ym);
    setParams(next);
    setExpandedDays(new Set());
  };

  const { data: meetings = [] } = useMeetingsAll();

  const todayIso = localIso(new Date());
  const firstIso = monthStart(month);
  const lastIso = localIso(addDays(atNoon(monthStart(addMonths(month, 1))), -1));

  // Monday-start weekday grid (Mon–Fri; NEPOOL never meets weekends) padded
  // to full weeks with adjacent-month days.
  // Plain derivations, no manual useMemo: the React Compiler memoizes these
  // itself, and it refuses to preserve hand-written deps once Date objects
  // flow through the helper chain.
  const days: string[] = [];
  {
    const end = addDays(mondayOf(atNoon(lastIso)), 6);
    for (let d = mondayOf(atNoon(firstIso)); d <= end; d = addDays(d, 1)) {
      const dow = d.getDay();
      if (dow !== 0 && dow !== 6) days.push(localIso(d));
    }
  }

  // Multi-day meetings render as one bar spanning their days (split at week
  // boundaries, stacked into lanes when they overlap); single-day meetings
  // stay chips inside their day cell.
  const gridStart = days[0];
  const gridEnd = days[days.length - 1];

  // weeks + lanes + singles all populate inside ONE derivation block: lanes
  // mutate the week objects, and a cached `weeks` from a separate memo scope
  // would accumulate duplicate lanes on every meetings refetch.
  const weeks: Week[] = [];
  const singlesByDay = new Map<string, MeetingListItem[]>();
  {
    for (let i = 0; i < days.length; i += 5) {
      weeks.push({ days: days.slice(i, i + 5), lanes: [] });
    }

    const weekSegs = new Map<Week, SpanSeg[]>();
    for (const m of meetings) {
      const rawEnd =
        m.end_date && m.end_date > m.meeting_date ? m.end_date : m.meeting_date;
      // Cap runaway spans — a bad end_date from a scraper shouldn't flood
      // the month with a wall-to-wall bar.
      const cap = localIso(addDays(atNoon(m.meeting_date), MAX_SPAN_DAYS - 1));
      const end = rawEnd > cap ? cap : rawEnd;

      if (end === m.meeting_date) {
        if (m.meeting_date >= gridStart && m.meeting_date <= gridEnd) {
          const list = singlesByDay.get(m.meeting_date) ?? [];
          list.push(m);
          singlesByDay.set(m.meeting_date, list);
        }
        continue;
      }

      for (const w of weeks) {
        if (end < w.days[0] || m.meeting_date > w.days[w.days.length - 1]) {
          continue;
        }
        let startCol = 0;
        while (startCol < w.days.length && w.days[startCol] < m.meeting_date) {
          startCol++;
        }
        let endCol = w.days.length - 1;
        while (endCol >= 0 && w.days[endCol] > end) endCol--;
        if (startCol > endCol) continue; // only hidden weekend days overlap
        const segs = weekSegs.get(w) ?? [];
        segs.push({
          m,
          startCol,
          endCol,
          contLeft: m.meeting_date < w.days[startCol],
          contRight: end > w.days[endCol],
        });
        weekSegs.set(w, segs);
      }
    }

    // Greedy lane allocation: overlapping bars stack, the rest share a lane.
    for (const [w, segs] of weekSegs) {
      segs.sort(
        (a, b) =>
          a.startCol - b.startCol ||
          a.m.meeting_date.localeCompare(b.m.meeting_date) ||
          a.m.type_short.localeCompare(b.m.type_short),
      );
      for (const seg of segs) {
        const lane = w.lanes.find((l) => l[l.length - 1].endCol < seg.startCol);
        if (lane) lane.push(seg);
        else w.lanes.push([seg]);
      }
    }
  }
  for (const list of singlesByDay.values()) {
    list.sort((a, b) => a.type_short.localeCompare(b.type_short));
  }

  const monthCount = meetings.filter(
    (m) => m.meeting_date >= firstIso && m.meeting_date <= lastIso,
  ).length;

  // Venue prefix on chips only once a second venue is actually visible.
  const multiVenue = new Set(meetings.map((m) => m.venue)).size > 1;

  const dayCls = (base: string, iso: string) =>
    `${base}${iso >= firstIso && iso <= lastIso ? "" : " is-out"}${
      iso === todayIso ? " is-today" : ""
    }`;

  const openMeeting = (m: MeetingListItem) => navigate(`/meeting/${m.id}`);
  const toggleDay = (iso: string) =>
    setExpandedDays((prev) => {
      const next = new Set(prev);
      if (next.has(iso)) next.delete(iso);
      else next.add(iso);
      return next;
    });

  // Mobile agenda: month days only, meetings on their start day (the row
  // already prints the full date range).
  const agendaMap = new Map<string, MeetingListItem[]>();
  for (const m of meetings) {
    if (m.meeting_date < firstIso || m.meeting_date > lastIso) continue;
    const list = agendaMap.get(m.meeting_date) ?? [];
    list.push(m);
    agendaMap.set(m.meeting_date, list);
  }
  const agendaDays = Array.from(agendaMap.entries()).sort((a, b) =>
    a[0].localeCompare(b[0]),
  );

  const isCurrentMonth = month === currentMonth();

  return (
    <>
      <Topbar crumbs={[{ label: "Calendar" }]} />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Meeting calendar</div>
          <div className="cal-title-row">
            <h1 className="page-title">{monthTitle(month)}</h1>
            <div className="cal-toolbar">
              <button
                className="btn btn-sm"
                onClick={() => setMonth(addMonths(month, -1))}
                aria-label="Previous month"
              >
                <Icon name="arrow-l" />
              </button>
              <button
                className="btn btn-sm"
                onClick={() => setMonth(currentMonth())}
                disabled={isCurrentMonth}
              >
                Today
              </button>
              <button
                className="btn btn-sm"
                onClick={() => setMonth(addMonths(month, 1))}
                aria-label="Next month"
              >
                <Icon name="arrow-r" />
              </button>
              <span className="muted text-xs">
                {monthCount} meeting{monthCount === 1 ? "" : "s"}
              </span>
            </div>
          </div>
        </div>

        <div className="cal-grid">
          <div className="cal-dow-row">
            {["Mon", "Tue", "Wed", "Thu", "Fri"].map((d) => (
              <div key={d} className="cal-dow">
                {d}
              </div>
            ))}
          </div>
          {weeks.map((w) => (
            <div
              key={w.days[0]}
              className="cal-week"
              style={{
                gridTemplateRows: [
                  "auto",
                  ...w.lanes.map(() => "auto"),
                  "1fr",
                ].join(" "),
              }}
            >
              {/* Background layer: cell borders, out-month + today washes.
                  Spans all rows so bars and chips paint over one surface. */}
              {w.days.map((iso, c) => (
                <div
                  key={`bg-${iso}`}
                  className={dayCls("cal-bg", iso)}
                  style={{ gridColumn: c + 1, gridRow: "1 / -1" }}
                />
              ))}
              {w.days.map((iso, c) => (
                <div
                  key={`dt-${iso}`}
                  className={dayCls("cal-datecell", iso)}
                  style={{ gridColumn: c + 1, gridRow: 1 }}
                >
                  <span className="cal-cell-date">
                    {Number(iso.slice(8, 10)) === 1
                      ? atNoon(iso).toLocaleDateString("en-US", {
                          month: "short",
                          day: "numeric",
                        })
                      : Number(iso.slice(8, 10))}
                  </span>
                </div>
              ))}
              {w.lanes.map((lane, li) =>
                lane.map((seg) => (
                  <button
                    key={`${seg.m.id}-${seg.startCol}`}
                    type="button"
                    className={`cal-span${seg.contLeft ? " cont-l" : ""}${seg.contRight ? " cont-r" : ""}`}
                    style={{
                      gridColumn: `${seg.startCol + 1} / ${seg.endCol + 2}`,
                      gridRow: li + 2,
                    }}
                    onClick={() => openMeeting(seg.m)}
                    title={`${seg.m.type_name} · ${fmtDateRange(seg.m.meeting_date, seg.m.end_date)}`}
                  >
                    <span className={`cal-chip-dot ${seg.m.status}`} />
                    <span className="cal-chip-label">
                      {multiVenue
                        ? `${seg.m.venue} ${seg.m.type_short}`
                        : seg.m.type_short}
                    </span>
                    <span className="cal-chip-title">
                      {seg.m.title || seg.m.type_name}
                    </span>
                  </button>
                )),
              )}
              {w.days.map((iso, c) => (
                <DayChips
                  key={`ch-${iso}`}
                  col={c + 1}
                  row={w.lanes.length + 2}
                  meetings={singlesByDay.get(iso) ?? []}
                  expanded={expandedDays.has(iso)}
                  onToggle={() => toggleDay(iso)}
                  onOpen={openMeeting}
                  multiVenue={multiVenue}
                />
              ))}
            </div>
          ))}
        </div>

        <div className="cal-agenda">
          {agendaDays.length === 0 ? (
            <div className="empty">No meetings this month.</div>
          ) : (
            agendaDays.map(([iso, list]) => (
              <div key={iso}>
                <div
                  className={`cal-agenda-day${iso === todayIso ? " is-today" : ""}`}
                >
                  {atNoon(iso).toLocaleDateString("en-US", {
                    weekday: "short",
                    month: "short",
                    day: "numeric",
                  })}
                </div>
                <div className="mtg-list">
                  {list.map((m) => (
                    <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="list" />
                  ))}
                </div>
              </div>
            ))
          )}
        </div>

        <div style={{ height: 48 }} />
      </div>
    </>
  );
}

function DayChips({
  col,
  row,
  meetings,
  expanded,
  onToggle,
  onOpen,
  multiVenue,
}: {
  col: number;
  row: number;
  meetings: MeetingListItem[];
  expanded: boolean;
  onToggle: () => void;
  onOpen: (m: MeetingListItem) => void;
  multiVenue: boolean;
}) {
  const visible = expanded ? meetings : meetings.slice(0, MAX_VISIBLE_CHIPS);
  const hidden = meetings.length - visible.length;

  return (
    <div className="cal-daychips" style={{ gridColumn: col, gridRow: row }}>
      {visible.map((m) => (
        <MeetingChip key={m.id} m={m} multiVenue={multiVenue} onOpen={onOpen} />
      ))}
      {hidden > 0 && (
        <button type="button" className="cal-more" onClick={onToggle}>
          +{hidden} more
        </button>
      )}
      {expanded && meetings.length > MAX_VISIBLE_CHIPS && (
        <button type="button" className="cal-more" onClick={onToggle}>
          show less
        </button>
      )}
    </div>
  );
}

function MeetingChip({
  m,
  multiVenue,
  onOpen,
}: {
  m: MeetingListItem;
  multiVenue: boolean;
  onOpen: (m: MeetingListItem) => void;
}) {
  return (
    <button
      type="button"
      className="cal-chip"
      onClick={() => onOpen(m)}
      title={`${m.type_name} · ${fmtDateRange(m.meeting_date, m.end_date)}`}
    >
      <span className={`cal-chip-dot ${m.status}`} />
      <span className="cal-chip-label">
        {multiVenue ? `${m.venue} ${m.type_short}` : m.type_short}
      </span>
      <span className="cal-chip-title">{m.title || m.type_name}</span>
    </button>
  );
}
