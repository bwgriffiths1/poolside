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
