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
