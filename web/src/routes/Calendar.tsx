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

interface DayEntry {
  m: MeetingListItem;
  cont: boolean; // continuation day of a multi-day meeting (day 2+)
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

  // Monday-start grid padded to full weeks with adjacent-month days.
  // Plain derivations, no manual useMemo: the React Compiler memoizes these
  // itself, and it refuses to preserve hand-written deps once Date objects
  // flow through the helper chain.
  const days: string[] = [];
  {
    const end = addDays(mondayOf(atNoon(lastIso)), 6);
    for (let d = mondayOf(atNoon(firstIso)); d <= end; d = addDays(d, 1)) {
      days.push(localIso(d));
    }
  }

  // Multi-day meetings repeat a chip on every covered day (spans would need
  // lane allocation + re-splitting at each Monday for no real gain here).
  const gridStart = days[0];
  const gridEnd = days[days.length - 1];
  const byDay = new Map<string, DayEntry[]>();
  for (const m of meetings) {
    const end =
      m.end_date && m.end_date > m.meeting_date ? m.end_date : m.meeting_date;
    let d = atNoon(m.meeting_date);
    for (let i = 0; i < MAX_SPAN_DAYS; i++) {
      const iso = localIso(d);
      if (iso > end || iso > gridEnd) break;
      if (iso >= gridStart) {
        const list = byDay.get(iso) ?? [];
        list.push({ m, cont: iso !== m.meeting_date });
        byDay.set(iso, list);
      }
      d = addDays(d, 1);
    }
  }
  for (const list of byDay.values()) {
    list.sort(
      (a, b) =>
        a.m.meeting_date.localeCompare(b.m.meeting_date) ||
        a.m.type_short.localeCompare(b.m.type_short),
    );
  }

  const monthCount = meetings.filter(
    (m) => m.meeting_date >= firstIso && m.meeting_date <= lastIso,
  ).length;

  // Venue prefix on chips only once a second venue is actually visible.
  const multiVenue = new Set(meetings.map((m) => m.venue)).size > 1;

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
          <h1 className="page-title">{monthTitle(month)}</h1>
        </div>

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

        <div className="cal-grid">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
            <div key={d} className="cal-dow">
              {d}
            </div>
          ))}
          {days.map((iso) => (
            <DayCell
              key={iso}
              iso={iso}
              inMonth={iso >= firstIso && iso <= lastIso}
              isToday={iso === todayIso}
              entries={byDay.get(iso) ?? []}
              expanded={expandedDays.has(iso)}
              onToggle={() => toggleDay(iso)}
              onOpen={openMeeting}
              multiVenue={multiVenue}
            />
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

function DayCell({
  iso,
  inMonth,
  isToday,
  entries,
  expanded,
  onToggle,
  onOpen,
  multiVenue,
}: {
  iso: string;
  inMonth: boolean;
  isToday: boolean;
  entries: DayEntry[];
  expanded: boolean;
  onToggle: () => void;
  onOpen: (m: MeetingListItem) => void;
  multiVenue: boolean;
}) {
  const dayNum = Number(iso.slice(8, 10));
  const visible = expanded ? entries : entries.slice(0, MAX_VISIBLE_CHIPS);
  const hidden = entries.length - visible.length;

  return (
    <div
      className={`cal-cell${inMonth ? "" : " is-out"}${isToday ? " is-today" : ""}`}
    >
      <div className="cal-cell-date">
        {dayNum === 1
          ? atNoon(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" })
          : dayNum}
      </div>
      {visible.map(({ m, cont }) => (
        <MeetingChip
          key={`${m.id}-${iso}`}
          m={m}
          cont={cont}
          multiVenue={multiVenue}
          onOpen={onOpen}
        />
      ))}
      {hidden > 0 && (
        <button type="button" className="cal-more" onClick={onToggle}>
          +{hidden} more
        </button>
      )}
      {expanded && entries.length > MAX_VISIBLE_CHIPS && (
        <button type="button" className="cal-more" onClick={onToggle}>
          show less
        </button>
      )}
    </div>
  );
}

function MeetingChip({
  m,
  cont,
  multiVenue,
  onOpen,
}: {
  m: MeetingListItem;
  cont: boolean;
  multiVenue: boolean;
  onOpen: (m: MeetingListItem) => void;
}) {
  return (
    <button
      type="button"
      className={`cal-chip${cont ? " is-cont" : ""}`}
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
