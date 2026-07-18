import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { VenueTag, TypeTag } from "../components/Tag";
import { api, type SummarySearchHit } from "../lib/api";
import { qk, useMeetingsAll } from "../lib/queries";
import type { MeetingListItem } from "../types";

const SUMMARY_LIMIT = 200;

type DateRange = "all" | "upcoming" | "30d" | "90d" | "year";

function cutoffIso(range: DateRange): string | null {
  if (range === "all") return null;
  const d = new Date();
  if (range === "upcoming") return d.toISOString().slice(0, 10); // today
  if (range === "30d") d.setDate(d.getDate() - 30);
  else if (range === "90d") d.setDate(d.getDate() - 90);
  else if (range === "year") d.setDate(d.getDate() - 365);
  return d.toISOString().slice(0, 10);
}

function passDateRange(meeting_date: string, range: DateRange): boolean {
  if (range === "all") return true;
  const today = new Date().toISOString().slice(0, 10);
  if (range === "upcoming") return meeting_date >= today;
  const cutoff = cutoffIso(range);
  return !!cutoff && meeting_date >= cutoff;
}

// Custom `from`/`to` take precedence over the preset segmented when set.
// Otherwise we derive the lower bound from the preset (upper bound stays null
// — "from cutoff to forever").
function effectiveFromDate(from: string, range: DateRange): string | null {
  if (from) return from;
  if (range === "all" || range === "upcoming") {
    if (range === "upcoming") return new Date().toISOString().slice(0, 10);
    return null;
  }
  return cutoffIso(range);
}
function effectiveToDate(to: string, _range: DateRange): string | null {
  return to || null;
}
function passCustomDate(date: string, from: string, to: string): boolean {
  if (from && date < from) return false;
  if (to && date > to) return false;
  return true;
}

export function Search() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const initialQ = params.get("q") ?? "";

  // Live input mirrors the URL ?q= param. We push to history on submit so the
  // back button works as expected; typing alone doesn't spam history entries.
  const [input, setInput] = useState(initialQ);
  const q = (params.get("q") ?? "").trim();
  const typeFilter = params.get("type") ?? "All";
  const dateRange = (params.get("range") ?? "all") as DateRange;
  const fromDate = params.get("from") ?? "";
  const toDate = params.get("to") ?? "";
  const tagFilter = params.get("tag") ?? "";
  const presenterFilter = params.get("presenter") ?? "";
  const statusFilter = (params.get("status") ?? "all") as
    | "all"
    | "approved"
    | "draft";

  // The free-text "presenter" filter is debounced so typing doesn't fire a
  // request per keystroke.
  const [presenterInput, setPresenterInput] = useState(presenterFilter);
  useEffect(() => setPresenterInput(presenterFilter), [presenterFilter]);

  useEffect(() => {
    setInput(params.get("q") ?? "");
  }, [params]);

  const updateParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value && value !== "all" && value !== "All") next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  const summaries = useQuery({
    queryKey: [
      "search-summaries",
      q,
      SUMMARY_LIMIT,
      typeFilter,
      dateRange,
      fromDate,
      toDate,
      tagFilter,
      presenterFilter,
      statusFilter,
    ],
    queryFn: () =>
      api.searchSummaries(q, {
        limit: SUMMARY_LIMIT,
        from_date: effectiveFromDate(fromDate, dateRange),
        to_date: effectiveToDate(toDate, dateRange),
        type_short: typeFilter !== "All" ? typeFilter : null,
        tag: tagFilter || null,
        presenter: presenterFilter || null,
        status: statusFilter === "all" ? null : statusFilter,
      }),
    enabled: q.length >= 2,
    staleTime: 30_000,
  });

  const tagOptions = useQuery({
    queryKey: qk.searchTags,
    queryFn: () => api.listSearchTags(),
    staleTime: 5 * 60_000,
  });

  // Meeting-haystack search is client-side (same as the palette + /meetings).
  const meetings = useMeetingsAll();

  // All distinct committee codes across the meeting list (for the dropdown).
  const types = useMemo(() => {
    const seen = new Set<string>();
    (meetings.data ?? []).forEach((m) => seen.add(m.type_short));
    return ["All", ...Array.from(seen).sort()];
  }, [meetings.data]);

  const meetingHits = useMemo<MeetingListItem[]>(() => {
    if (!q || !meetings.data) return [];
    const lo = q.toLowerCase();
    return meetings.data
      .filter((m) => {
        if (typeFilter !== "All" && m.type_short !== typeFilter) return false;
        if (fromDate || toDate) {
          if (!passCustomDate(m.meeting_date, fromDate, toDate)) return false;
        } else if (!passDateRange(m.meeting_date, dateRange)) {
          return false;
        }
        if (tagFilter && !m.tags.some((t) => t === tagFilter)) return false;
        const hay = `${m.title} ${m.type_name} ${m.venue} ${m.type_short} ${m.location} ${m.tags.join(" ")}`.toLowerCase();
        return hay.includes(lo);
      })
      .sort((a, b) => b.meeting_date.localeCompare(a.meeting_date));
  }, [meetings.data, q, typeFilter, dateRange, fromDate, toDate, tagFilter]);

  // Summary hits are filtered server-side now; we still apply the preset
  // range client-side when no custom from/to is set (the server sees the
  // cutoff derived from the preset, but "upcoming" is checked client-side too).
  const filteredSummaries = useMemo<SummarySearchHit[]>(() => {
    return (summaries.data ?? []).filter((hit) => {
      if (!fromDate && !toDate && !passDateRange(hit.meeting_date, dateRange)) {
        return false;
      }
      return true;
    });
  }, [summaries.data, dateRange, fromDate, toDate]);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams(params);
    if (input.trim()) next.set("q", input.trim());
    else next.delete("q");
    setParams(next);
  };

  const openMeeting = (m: MeetingListItem) => navigate(`/meeting/${m.id}`);

  const openSummaryHit = (hit: SummarySearchHit) => {
    if (hit.entity_type === "meeting") {
      navigate(`/briefing/${hit.meeting_id}`);
    } else if (hit.item_id) {
      navigate(
        `/meeting/${hit.meeting_id}?item=${encodeURIComponent(hit.item_id)}`,
      );
    } else {
      navigate(`/meeting/${hit.meeting_id}`);
    }
  };

  const totalSummary = filteredSummaries.length;
  const totalMeetings = meetingHits.length;
  const totalSummaryRaw = summaries.data?.length ?? 0;
  const truncated = totalSummaryRaw >= SUMMARY_LIMIT;
  const filtersActive =
    typeFilter !== "All" ||
    dateRange !== "all" ||
    !!fromDate ||
    !!toDate ||
    !!tagFilter ||
    !!presenterFilter ||
    statusFilter !== "all";

  // Commit the debounced presenter input back to the URL.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      if (presenterInput !== presenterFilter) {
        updateParam("presenter", presenterInput || null);
      }
    }, 350);
    return () => window.clearTimeout(handle);
    // We deliberately omit `params` / `updateParam` from deps — `updateParam`
    // closes over `params` and we only want to fire when the input changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presenterInput]);

  return (
    <>
      <Topbar crumbs={[{ label: "Search" }]} />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Cross-meeting search</div>
          <h1 className="page-title">Search</h1>
          <p className="page-subtitle">
            Match against meeting titles, tags, and the body of every summary
            and briefing. Type a phrase and hit Enter.
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          className="row"
          style={{
            gap: 6,
            background: "var(--bg-elev)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "6px 12px",
            maxWidth: 640,
            marginBottom: 18,
          }}
        >
          <Icon name="search" size={14} />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="capacity accreditation, FCM tariff…"
            autoFocus
            style={{
              border: 0,
              outline: 0,
              background: "transparent",
              color: "inherit",
              fontSize: 14,
              width: "100%",
              fontFamily: "inherit",
              padding: "4px 0",
            }}
          />
          {input && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setInput("");
                const next = new URLSearchParams(params);
                next.delete("q");
                setParams(next);
              }}
            >
              <Icon name="x" size={11} />
            </button>
          )}
          <button type="submit" className="btn btn-sm btn-primary">
            Search
          </button>
        </form>

        <div className="search-filters">
          <div className="search-filter-row">
            <select
              className="select"
              value={typeFilter}
              onChange={(e) => updateParam("type", e.target.value)}
              style={{ minWidth: 160 }}
            >
              {types.map((t) => (
                <option key={t} value={t}>
                  {t === "All" ? "All committees" : t}
                </option>
              ))}
            </select>
            <select
              className="select"
              value={tagFilter}
              onChange={(e) => updateParam("tag", e.target.value || null)}
              style={{ minWidth: 180 }}
            >
              <option value="">All tags</option>
              {(tagOptions.data ?? []).map((t) => (
                <option key={t.name} value={t.name}>
                  {t.tag_type === "initiative" ? `${t.name} (initiative)` : t.name}
                </option>
              ))}
            </select>
            <input
              className="input"
              placeholder="Presenter (substring)…"
              value={presenterInput}
              onChange={(e) => setPresenterInput(e.target.value)}
              style={{ minWidth: 200, flex: 1 }}
            />
            <Segmented
              value={statusFilter}
              onChange={(v) =>
                updateParam("status", v === "all" ? null : (v as string))
              }
              options={[
                { value: "all", label: "All" },
                { value: "approved", label: "Approved" },
                { value: "draft", label: "Draft" },
              ]}
            />
          </div>
          <div className="search-filter-row">
            <Segmented
              value={fromDate || toDate ? "custom" : dateRange}
              onChange={(v) => {
                const next = new URLSearchParams(params);
                if (v === "custom") {
                  next.delete("range");
                  // Seed empty `from`/`to` so the inputs become visible.
                  if (!next.get("from") && !next.get("to")) {
                    next.set("from", "");
                  }
                } else {
                  next.delete("from");
                  next.delete("to");
                  if (v !== "all") next.set("range", v as string);
                  else next.delete("range");
                }
                setParams(next);
              }}
              options={[
                { value: "all", label: "All time" },
                { value: "upcoming", label: "Upcoming" },
                { value: "30d", label: "30 d" },
                { value: "90d", label: "90 d" },
                { value: "year", label: "1 yr" },
                { value: "custom", label: "Custom" },
              ]}
            />
            {(fromDate || toDate || params.get("from") != null) && (
              <>
                <span className="muted text-xs">From</span>
                <input
                  type="date"
                  className="input"
                  value={fromDate}
                  onChange={(e) => updateParam("from", e.target.value || null)}
                  style={{ width: 150 }}
                />
                <span className="muted text-xs">to</span>
                <input
                  type="date"
                  className="input"
                  value={toDate}
                  onChange={(e) => updateParam("to", e.target.value || null)}
                  style={{ width: 150 }}
                />
              </>
            )}
            <span style={{ flex: 1 }} />
            {filtersActive && (
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => {
                  const next = new URLSearchParams(params);
                  ["type", "range", "from", "to", "tag", "presenter", "status"].forEach(
                    (k) => next.delete(k),
                  );
                  setParams(next);
                  setPresenterInput("");
                }}
                title="Clear filters"
              >
                <Icon name="x" size={11} /> Clear filters
              </button>
            )}
          </div>
        </div>

        {!q && (
          <div className="empty">
            Type a phrase above. Try a tariff section, a presenter's name, or
            an initiative code like <code>CAR-SA</code>.
          </div>
        )}

        {q && q.length < 2 && (
          <div className="muted text-sm">
            Type at least two characters.
          </div>
        )}

        {q && q.length >= 2 && (
          <>
            <section style={{ marginBottom: 28 }}>
              <h2 className="section-head">
                Meetings {totalMeetings > 0 && <span className="muted text-xs">· {totalMeetings}</span>}
              </h2>
              {totalMeetings === 0 ? (
                <div className="muted text-sm">No meeting titles match.</div>
              ) : (
                <div className="search-list">
                  {meetingHits.map((m) => (
                    <button
                      key={m.id}
                      className="search-row"
                      onClick={() => openMeeting(m)}
                    >
                      <div className="mono text-xs muted" style={{ flex: "0 0 96px" }}>
                        {m.meeting_date}
                      </div>
                      <div style={{ flex: "0 0 auto", display: "flex", gap: 4 }}>
                        <VenueTag>{m.venue}</VenueTag>
                        <TypeTag>{m.type_short}</TypeTag>
                      </div>
                      <div className="search-row-main">
                        <div className="search-row-title">{m.title || m.type_name}</div>
                        <div className="muted text-xs">{m.location}</div>
                      </div>
                      <Icon name="chev-r" size={14} />
                    </button>
                  ))}
                </div>
              )}
            </section>

            <section>
              <h2 className="section-head">
                Summary text{" "}
                {summaries.isLoading ? (
                  <span className="muted text-xs">· searching…</span>
                ) : (
                  totalSummary > 0 && (
                    <span className="muted text-xs">
                      · {totalSummary}
                      {truncated && "+"}
                    </span>
                  )
                )}
              </h2>

              {summaries.isLoading ? (
                <div className="muted text-sm">Searching…</div>
              ) : totalSummary === 0 ? (
                <div className="muted text-sm">
                  {totalSummaryRaw > 0 && filtersActive
                    ? `No matches in this committee / date range. (${totalSummaryRaw} match${totalSummaryRaw === 1 ? "" : "es"} without filters.)`
                    : "No summary text matches."}
                </div>
              ) : (
                <div className="search-list">
                  {filteredSummaries.map((hit) => {
                    const label =
                      hit.entity_type === "meeting"
                        ? "Meeting briefing"
                        : `Item ${hit.item_id ?? ""}`;
                    return (
                      <button
                        key={`${hit.entity_type}-${hit.entity_id}`}
                        className="search-row"
                        onClick={() => openSummaryHit(hit)}
                      >
                        <div
                          className="mono text-xs muted"
                          style={{ flex: "0 0 96px" }}
                        >
                          {hit.meeting_date}
                        </div>
                        <div
                          style={{ flex: "0 0 auto", display: "flex", gap: 4 }}
                        >
                          <VenueTag>{hit.venue}</VenueTag>
                          <TypeTag>{hit.type_short}</TypeTag>
                        </div>
                        <div className="search-row-main">
                          <div className="search-row-title">
                            {hit.entity_type === "agenda_item" && hit.item_title
                              ? hit.item_title
                              : label}
                            {hit.presenter && (
                              <span className="muted text-xs" style={{ marginLeft: 8, fontWeight: 400 }}>
                                · {hit.presenter}
                                {hit.organization ? ` (${hit.organization})` : ""}
                              </span>
                            )}
                          </div>
                          <div
                            className="search-row-snippet muted text-sm"
                            dangerouslySetInnerHTML={{ __html: hit.snippet }}
                          />
                        </div>
                        <Icon name="chev-r" size={14} />
                      </button>
                    );
                  })}
                </div>
              )}

              {truncated && (
                <div className="muted text-xs" style={{ marginTop: 10 }}>
                  Showing the top {SUMMARY_LIMIT} matches by relevance.
                  Narrow your query to see the rest.
                </div>
              )}
            </section>
          </>
        )}

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
