import type { AskSource } from "./api";

// ── Docket mentions ─────────────────────────────────────────────────────
// Mirrors api/routes/ask.py: "@ER26-925" (any FERC prefix, optional
// sub-docket suffix) anywhere in the question scopes it to that docket.

export const MENTION_RE = /(?<![\w@])@([A-Za-z]{1,3}\d{2}-\d+(?:-\d{1,3})?)\b/g;
/** An "@" token immediately before the caret (with whatever's typed after it). */
export const AT_CARET_RE = /(^|[\s(,;])@([\w-]*)$/;

export function normalizeMention(raw: string): string {
  const num = raw.replace(/^@/, "").toUpperCase();
  const m = /^([A-Z]{1,3}\d{2}-\d+)(?:-\d{1,3})?$/.exec(num);
  return m ? m[1] : num;
}

export function mentionedDockets(question: string): string[] {
  const out: string[] = [];
  for (const m of question.matchAll(MENTION_RE)) {
    const num = normalizeMention(m[1]);
    if (!out.includes(num)) out.push(num);
  }
  return out;
}

/** Where a citation or source row should land. */
export function sourceHref(s: AskSource): string {
  switch (s.entity_type) {
    case "docket":
      return `#/docket/${s.docket_id}`;
    case "docket_filing":
    case "docket_filing_file":
      return s.filing_id
        ? `#/docket/${s.docket_id}?filing=${s.filing_id}`
        : `#/docket/${s.docket_id}`;
    default:
      return s.item_id
        ? `#/meeting/${s.meeting_id}?item=${encodeURIComponent(s.item_id)}`
        : `#/${s.entity_type === "meeting" ? "briefing" : "meeting"}/${s.meeting_id}`;
  }
}

/** Turn bare [n] citation markers into internal links so the markdown
 *  renderer emits clickable citations. */
export function linkCitations(md: string, sources: AskSource[]): string {
  if (!md) return md;
  const byN = new Map(sources.map((s) => [s.n, s]));
  return md.replace(/\[(\d+)\](?!\()/g, (match, num) => {
    const s = byN.get(Number(num));
    if (!s) return match;
    return `[${num}](${sourceHref(s)})`;
  });
}

