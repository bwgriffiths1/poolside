import type { MeetingListItem } from "../types";

// Date / display helpers shared across screens.

export function fmtDateRange(iso: string, end?: string): string {
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  const d = new Date(`${iso}T12:00:00`);
  const y = d.getFullYear();
  if (!end || end === iso) {
    return d.toLocaleDateString("en-US", { ...opts, year: "numeric" });
  }
  const e = new Date(`${end}T12:00:00`);
  if (d.getMonth() === e.getMonth()) {
    return `${d.toLocaleDateString("en-US", opts)}–${e.getDate()}, ${y}`;
  }
  return `${d.toLocaleDateString("en-US", opts)} – ${e.toLocaleDateString("en-US", opts)}, ${y}`;
}

export function monthLabel(iso: string): string {
  return new Date(`${iso}T12:00:00`)
    .toLocaleDateString("en-US", { month: "short" })
    .toUpperCase();
}

export function dayNumber(iso: string): number {
  return new Date(`${iso}T12:00:00`).getDate();
}

export function weekdayShort(iso: string): string {
  return new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("en-US", {
    weekday: "short",
  });
}

export function fmtShortDate(iso: string): string {
  return new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

export function extFromFilename(filename: string): string {
  return (filename.split(".").pop() || "").toUpperCase();
}

export function formatRel(
  iso: string | null | undefined,
  nullLabel = "never",
): string {
  if (!iso) return nullLabel;
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "in the future";
  if (ms < 60_000) return "just now";
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// Which string identifies this meeting in a list?
//
// For ISO-NE the committee name IS the identity — every MC row is "Markets
// Committee", and `title` ("NEPOOL Markets Committee Meeting") only adds
// boilerplate. For PJM the whole venue is one workstream, so `type_name` is
// identical on every row and the session name in `title` ("CIFP–RBP /
// Connect and Manage - Stage 3") is the only thing telling them apart.
//
// So: prefer `title` when it carries information beyond the committee name,
// else fall back to `type_name`. Normalizing away punctuation and the filler
// words that differ between the fields keeps near-duplicates from counting
// as new information.
function normalizeForCompare(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\b(nepool|iso|ne|meeting|committee|subcommittee)\b/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function displayTitle(m: MeetingListItem): string {
  const title = (m.title || "").trim();
  if (!title) return m.type_name;
  const t = normalizeForCompare(title);
  const tn = normalizeForCompare(m.type_name);
  if (!t || t === tn || tn.includes(t) || t.includes(tn)) return m.type_name;
  return title;
}
