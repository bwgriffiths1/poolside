import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "../Icon";
import { Tag } from "../Tag";
import { Markdown } from "../../lib/markdown";
import type { DocketFiling } from "../../lib/api";

/** One docket filing as an expandable timeline row, shared by the Docket
 *  page and the public share view. The caller decides edit affordances
 *  (canEdit) and where file downloads go (fileHref — the public view
 *  points at the token-scoped passthrough). */

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function fmtBytes(n: number | null): string {
  if (!n) return "";
  if (n > 1_000_000) return `${(n / 1_000_000).toFixed(1)} MB`;
  return `${Math.round(n / 1000)} KB`;
}

export function authorLine(f: DocketFiling): string {
  const authors = f.filing_parties
    .filter((p) => p.type === "AUTHOR")
    .map((p) => p.org);
  return authors.join("; ");
}

/** Compact class chip label — the full taxonomy strings are long. The two
 *  anchor roles override: the initial filing and FERC's orders are the
 *  documents the docket pivots on. */
export function classLabel(f: DocketFiling): string {
  if (f.role === "initial") return "Initial Filing";
  if (f.role === "order") return "Order";
  const c = f.document_class || "?";
  const map: Record<string, string> = {
    "Application/Petition/Request": "Filing",
    "Comments/Protest": "Comments",
    "Order/Opinion": "Order",
    "ALJ Issuance": "ALJ",
    "Pleading/Motion": "Motion",
    Intervention: "Intervention",
    Notice: "Notice",
  };
  return map[c] || c;
}

export function defaultFileHref(fileId: number): string {
  return `/api/dockets/files/${fileId}/download`;
}

/** Sections with more files than this collapse behind "+N more" —
 *  SectionDocs' behavior, kept in sync by taste rather than import. */
const FILES_VISIBLE = 4;

/** A filing's files as briefing-style material rows (b-section-docs look),
 *  each a live download through the FERC passthrough. */
function FilingFiles({
  f,
  fileHref,
}: {
  f: DocketFiling;
  fileHref: (fileId: number) => string;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!f.files.length) return null;
  const hidden = expanded ? 0 : Math.max(0, f.files.length - FILES_VISIBLE);
  const shown = hidden ? f.files.slice(0, FILES_VISIBLE) : f.files;

  return (
    <div className="b-section-docs el-files-top">
      <div className="b-section-docs-label">
        <Icon name="paperclip" size={11} /> Files
      </div>
      <ul>
        {shown.map((x) => (
          <li key={x.id}>
            <a
              className={`b-doc-row${x.included ? "" : " el-doc-excluded"}`}
              href={fileHref(x.id)}
              title={
                "Download from FERC (takes 15-60s to start)" +
                (x.included ? "" : " — excluded from summarization")
              }
            >
              <span className="b-doc-ext">
                {(x.file_type || "?").toUpperCase()}
              </span>
              <span className="b-doc-name">
                {x.file_desc || x.orig_file_name}
              </span>
              <span className="el-file-meta mono">
                {x.page_count && x.page_count > 1 ? `${x.page_count}pp · ` : ""}
                {fmtBytes(x.file_size)}
              </span>
              <Icon name="download" size={11} className="b-doc-link-icon" />
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

export function FilingRow({
  f,
  canEdit,
  fileHref = defaultFileHref,
}: {
  f: DocketFiling;
  canEdit: boolean;
  fileHref?: (fileId: number) => string;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const expandable = !!(f.summary_detailed || f.files.length);
  const date = f.filed_date || f.issued_date;

  return (
    <div className={`el-filing${open ? " open" : ""}`}>
      <button
        className="el-filing-head"
        onClick={() => expandable && setOpen(!open)}
        style={{ cursor: expandable ? "pointer" : "default" }}
      >
        <div className="el-filing-date mono">{fmtDate(date)}</div>
        <div className="el-filing-main">
          <div className="el-filing-toprow">
            {f.role ? (
              <span className="el-chip-anchor">{classLabel(f)}</span>
            ) : (
              <Tag>{classLabel(f)}</Tag>
            )}
            {f.ferc_cite && <span className="el-cite mono">{f.ferc_cite}</span>}
            {f.comments_due_date && (
              <span className="el-due">
                comments due {fmtDate(f.comments_due_date)}
              </span>
            )}
            {f.summary_status == null && f.treatment !== "skip" && (
              <span className="el-pending">not summarized</span>
            )}
          </div>
          {authorLine(f) && (
            <div className="el-filing-party">{authorLine(f)}</div>
          )}
          <div className="el-filing-desc">
            {f.summary_one_line || f.description}
          </div>
        </div>
        <div className="ru-row-chev">
          {expandable && (
            <Icon name="chev-r" size={14} className={open ? "rot-90" : ""} />
          )}
        </div>
      </button>

      {open && (
        <div className="el-filing-body">
          <FilingFiles f={f} fileHref={fileHref} />
          {f.summary_one_line && f.description && (
            <div className="el-filing-origdesc">{f.description}</div>
          )}
          {f.summary_detailed && (
            <article className="el-filing-summary">
              <Markdown source={f.summary_detailed} />
            </article>
          )}
          <div className="el-filing-actions">
            <a
              className="btn btn-ghost btn-sm"
              href={f.elibrary_url}
              target="_blank"
              rel="noreferrer"
            >
              <Icon name="external" size={12} /> Doc info
            </a>
            <a
              className="btn btn-ghost btn-sm"
              href={f.filelist_url}
              target="_blank"
              rel="noreferrer"
            >
              <Icon name="list" size={12} /> File list
            </a>
            {canEdit && f.summary_detailed && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => navigate(`/edit/docket_filing/${f.id}`)}
              >
                <Icon name="edit" size={12} /> Edit summary
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
