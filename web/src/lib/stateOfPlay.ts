/** State-of-play markdown structure helpers, shared by the Docket page
 *  and the public docket share view. */

export interface SopSection {
  id: string;
  title: string;
  md: string;
}

/** Split the state-of-play markdown at its `## ` headings so each section
 *  can carry a scroll-spy ref and a rail entry. Returns the pre-heading
 *  preamble (usually empty) plus one {id, title, md} per section. */
export function splitByH2(md: string | null | undefined): {
  preamble: string;
  sections: SopSection[];
} {
  if (!md) return { preamble: "", sections: [] };
  const lines = md.split("\n");
  const sections: SopSection[] = [];
  const preamble: string[] = [];
  let cur: { id: string; title: string; buf: string[] } | null = null;
  const seen = new Map<string, number>();
  for (const line of lines) {
    const m = /^##\s+(.+?)\s*$/.exec(line);
    if (m) {
      if (cur) {
        sections.push({ id: cur.id, title: cur.title, md: cur.buf.join("\n") });
      }
      const title = m[1].trim();
      let slug =
        "s-" +
        (title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") ||
          "section");
      const n = (seen.get(slug) ?? 0) + 1;
      seen.set(slug, n);
      if (n > 1) slug += `-${n}`;
      cur = { id: slug, title, buf: [line] };
    } else if (cur) {
      cur.buf.push(line);
    } else {
      preamble.push(line);
    }
  }
  if (cur) {
    sections.push({ id: cur.id, title: cur.title, md: cur.buf.join("\n") });
  }
  return { preamble: preamble.join("\n").trim(), sections };
}

/** Pull "Key Takeaways" bullets out of the split sections when that section
 *  exists and is a plain bullet list (analyst rewrites fall back to normal
 *  rendering). The takeaways get the briefing page's numbered-band
 *  treatment above the State of Play. */
export function extractTakeaways(sections: SopSection[]): {
  takeaways: string[] | null;
  bodySections: SopSection[];
} {
  const kt = sections.find(
    (s) =>
      s.title.toLowerCase().replace(/[^a-z ]/g, "").trim() === "key takeaways",
  );
  if (!kt) return { takeaways: null, bodySections: sections };
  const bullets = kt.md
    .split("\n")
    .slice(1) // drop the ## heading line
    .map((ln) => ln.trim())
    .filter((ln) => ln && !/^-{3,}$/.test(ln)) // drop blanks + --- rules
    .map((ln) => /^[-*]\s+(.*)$/.exec(ln)?.[1]);
  if (!bullets.length || bullets.some((b) => b == null)) {
    return { takeaways: null, bodySections: sections };
  }
  return {
    takeaways: bullets as string[],
    bodySections: sections.filter((s) => s !== kt),
  };
}
