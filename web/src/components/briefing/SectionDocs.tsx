import { useState } from "react";
import { Icon } from "../Icon";
import { extFromFilename } from "../../lib/format";
import type { BriefingDoc } from "../../types";

/** Sections with more materials than this collapse behind a "+N more"
 *  toggle — a 14-file item otherwise buries its own summary. */
const VISIBLE = 4;

/** Shared link behavior: docs with no scraped source_url render inert. */
function docLinkProps(doc: BriefingDoc) {
  const href = doc.source_url || undefined;
  return {
    href,
    target: href ? "_blank" : undefined,
    rel: href ? "noopener noreferrer" : undefined,
    title: href || "No source URL recorded for this document",
    onClick: (e: React.MouseEvent) => {
      if (!href) e.preventDefault();
    },
    style: !href
      ? ({ cursor: "default", opacity: 0.75 } as const)
      : undefined,
  };
}

/**
 * A section's materials, rendered as compact chips directly under its
 * heading — the documents that back the discussion, next to the discussion.
 */
export function SectionDocs({ docs }: { docs?: BriefingDoc[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!docs?.length) return null;

  const hidden = expanded ? 0 : Math.max(0, docs.length - VISIBLE);
  const shown = hidden ? docs.slice(0, VISIBLE) : docs;

  return (
    <div className="b-section-docs">
      <div className="b-section-docs-label">
        <Icon name="paperclip" size={11} /> Materials
      </div>
      <ul>
        {shown.map((d) => (
          <li key={d.id}>
            <a className="b-doc-row" {...docLinkProps(d)}>
              <span className="b-doc-ext">{extFromFilename(d.filename)}</span>
              <span className="b-doc-name">{d.filename}</span>
              {d.source_url && (
                <Icon name="external" size={11} className="b-doc-link-icon" />
              )}
            </a>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <button className="b-doc-more" onClick={() => setExpanded(true)}>
          +{hidden} more
        </button>
      )}
    </div>
  );
}

/** Card grid used for the leftover documents listed at the end. */
export function DocCards({ docs }: { docs: BriefingDoc[] }) {
  return (
    <div className="b-sources">
      {docs.map((d) => (
        <a key={d.id} className="b-source" {...docLinkProps(d)}>
          <div className="b-source-ext">{extFromFilename(d.filename)}</div>
          <div>
            <div className="b-source-name">{d.filename}</div>
            <div className="b-source-item">
              {d.item_id ? `Item ${d.item_id} · ${d.item}` : "Meeting-level"}
            </div>
          </div>
          <Icon name="external" size={12} />
        </a>
      ))}
    </div>
  );
}
