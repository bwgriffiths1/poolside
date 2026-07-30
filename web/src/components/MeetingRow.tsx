import { Pill } from "./Pill";
import { Tag, VenueTag, TypeTag } from "./Tag";
import { Icon } from "./Icon";
import { displayTitle, fmtDateRange, monthLabel, dayNumber } from "../lib/format";
import type { MeetingListItem } from "../types";

interface MeetingRowProps {
  m: MeetingListItem;
  onOpen: (m: MeetingListItem) => void;
  view: "list" | "card";
}

export function MeetingRow({ m, onOpen, view }: MeetingRowProps) {
  // Location is empty on some venues (PJM) — join so there's no dangling
  // " · " separator in front of the date.
  const metaLine = [m.location, fmtDateRange(m.meeting_date, m.end_date)]
    .filter(Boolean)
    .join(" · ");

  if (view === "list") {
    return (
      <button className="mtg-row" onClick={() => onOpen(m)}>
        <div className="mtg-row-date">
          <div className="mtg-row-month">{monthLabel(m.meeting_date)}</div>
          <div className="mtg-row-day">{dayNumber(m.meeting_date)}</div>
        </div>
        <div className="mtg-row-venue">
          <VenueTag>{m.venue}</VenueTag>
          <TypeTag>{m.type_short}</TypeTag>
        </div>
        <div className="mtg-row-title">
          <div className="title-line">{displayTitle(m)}</div>
          <div className="meta-line">{metaLine}</div>
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
    );
  }

  return (
    <button className="mtg-card" onClick={() => onOpen(m)}>
      <div
        className="row"
        style={{ justifyContent: "space-between", marginBottom: 12 }}
      >
        <div className="row" style={{ gap: 6 }}>
          <VenueTag>{m.venue}</VenueTag>
          <TypeTag>{m.type_short}</TypeTag>
        </div>
        <Pill status={m.status} />
      </div>
      <div className="mtg-card-title">{displayTitle(m)}</div>
      <div className="mtg-card-date">{fmtDateRange(m.meeting_date, m.end_date)}</div>
      {m.location && <div className="mtg-card-loc text-xs muted">{m.location}</div>}
      <div className="mtg-card-meta">
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
      {m.tags.length > 0 && (
        <div className="row" style={{ gap: 4, marginTop: 10, flexWrap: "wrap" }}>
          {m.tags.slice(0, 3).map((t) => (
            <Tag key={t}>{t}</Tag>
          ))}
        </div>
      )}
    </button>
  );
}
