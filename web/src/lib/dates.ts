// Local-time date helpers shared by Overview and Calendar. Never use
// toISOString() for day math — it's UTC, and on an evening in New England
// it reads as tomorrow, shifting week/month boundaries. String-returning
// helpers emit "YYYY-MM-DD" (or "YYYY-MM"), which compare correctly as
// plain strings; Date-math goes through a noon anchor so DST transitions
// can't move a date across midnight (same idiom as lib/format.ts).

export function localIso(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function atNoon(iso: string): Date {
  return new Date(`${iso.slice(0, 10)}T12:00:00`);
}

export function addDays(d: Date, n: number): Date {
  const out = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  out.setDate(out.getDate() + n);
  return out;
}

export function mondayOf(d: Date): Date {
  return addDays(d, -((d.getDay() + 6) % 7));
}

// ── Month helpers ("YYYY-MM") ─────────────────────────────────────────────

export function currentMonth(): string {
  return localIso(new Date()).slice(0, 7);
}

export function monthStart(ym: string): string {
  return `${ym}-01`;
}

export function addMonths(ym: string, n: number): string {
  const [y, m] = ym.split("-").map(Number);
  const t = y * 12 + (m - 1) + n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
}

/** "March 2026" for a "YYYY-MM". */
export function monthTitle(ym: string): string {
  return atNoon(monthStart(ym)).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });
}

/** Validate a ?m= query value; only a real "YYYY-MM" passes. */
export function parseMonthParam(raw: string | null): string | null {
  return raw && /^\d{4}-(0[1-9]|1[0-2])$/.test(raw) ? raw : null;
}
