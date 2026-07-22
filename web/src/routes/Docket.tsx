import { useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Tag } from "../components/Tag";
import { VersionHistory } from "../components/VersionHistory";
import { api, type DocketFiling } from "../lib/api";
import { qk } from "../lib/queries";
import { Markdown, inlineMd } from "../lib/markdown";
import { useDocketJob } from "../hooks/useDocketJob";
import { useScrollSpy } from "../hooks/useScrollSpy";

function fmtDate(iso: string | null | undefined): string {
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

function authorLine(f: DocketFiling): string {
  const authors = f.filing_parties
    .filter((p) => p.type === "AUTHOR")
    .map((p) => p.org);
  return authors.join("; ");
}

/** Split the state-of-play markdown at its `## ` headings so each section
 *  can carry a scroll-spy ref and a rail entry. Returns the pre-heading
 *  preamble (usually empty) plus one {id, title, md} per section. */
function splitByH2(md: string | null | undefined): {
  preamble: string;
  sections: { id: string; title: string; md: string }[];
} {
  if (!md) return { preamble: "", sections: [] };
  const lines = md.split("\n");
  const sections: { id: string; title: string; md: string }[] = [];
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

/** Compact class chip label — the full taxonomy strings are long. */
function classLabel(f: DocketFiling): string {
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

/** Sections with more files than this collapse behind "+N more" —
 *  SectionDocs' behavior, kept in sync by taste rather than import. */
const FILES_VISIBLE = 4;

/** A filing's files as briefing-style material rows (b-section-docs look),
 *  each a live download through the FERC passthrough. */
function FilingFiles({ f }: { f: DocketFiling }) {
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
              href={`/api/dockets/files/${x.id}/download`}
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

function FilingRow({ f }: { f: DocketFiling }) {
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
            <Tag>{classLabel(f)}</Tag>
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
          <FilingFiles f={f} />
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
            {f.summary_detailed && (
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

export function Docket() {
  const { id } = useParams();
  const did = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const jobs = useDocketJob(did);
  const [showHistory, setShowHistory] = useState(false);
  const [showInterventions, setShowInterventions] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);

  const jobActive =
    jobs.job &&
    (jobs.job.status === "queued" ||
      jobs.job.status === "running" ||
      jobs.job.status === "cancelling");

  const { data: d, isLoading } = useQuery({
    queryKey: qk.docket(did),
    queryFn: () => api.docket(did),
    enabled: Number.isFinite(did),
    // While a job runs, keep pulling the detail so newly summarized
    // filings stream into the timeline.
    refetchInterval: jobActive ? 5000 : false,
    refetchIntervalInBackground: true,
  });

  const del = useMutation({
    mutationFn: () => api.deleteDocket(did),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.dockets });
      navigate("/elibrary");
    },
  });

  const { substantive, administrative, interventions } = useMemo(() => {
    const filings = d?.filings ?? [];
    const interv = filings.filter((f) => f.document_class === "Intervention");
    const rest = filings.filter((f) => f.document_class !== "Intervention");
    return {
      // Skip-tier housekeeping (notices, counsel/service-list changes,
      // transcripts…) collapses behind a toggle — signal stays up top.
      substantive: rest.filter((f) => f.treatment !== "skip"),
      administrative: rest.filter((f) => f.treatment === "skip"),
      interventions: interv,
    };
  }, [d?.filings]);

  // Alphabetized roster for the two-column list.
  const roster = useMemo(
    () =>
      [...(d?.intervenors ?? [])].sort((a, b) =>
        a.org.localeCompare(b.org, "en", { sensitivity: "base" }),
      ),
    [d?.intervenors],
  );

  // State-of-play sections, split at `## ` so each is a jump target.
  const sop = useMemo(() => splitByH2(d?.brief?.detailed), [d?.brief?.detailed]);

  // "Key Takeaways" gets the briefing page's numbered-band treatment above
  // the State of Play — pull its bullets out of the markdown when the
  // section exists and is a plain bullet list (analyst rewrites fall back
  // to normal rendering).
  const { takeaways, bodySections } = useMemo(() => {
    const kt = sop.sections.find(
      (s) => s.title.toLowerCase().replace(/[^a-z ]/g, "").trim() ===
        "key takeaways",
    );
    if (!kt) return { takeaways: null, bodySections: sop.sections };
    const bullets = kt.md
      .split("\n")
      .slice(1) // drop the ## heading line
      .map((ln) => ln.trim())
      .filter((ln) => ln && !/^-{3,}$/.test(ln)) // drop blanks + --- rules
      .map((ln) => /^[-*]\s+(.*)$/.exec(ln)?.[1]);
    if (!bullets.length || bullets.some((b) => b == null)) {
      return { takeaways: null, bodySections: sop.sections };
    }
    return {
      takeaways: bullets as string[],
      bodySections: sop.sections.filter((s) => s !== kt),
    };
  }, [sop.sections]);

  // "On this page" rail — briefing-page mechanics (useScrollSpy over .main).
  const refs = useRef<Record<string, HTMLElement | null>>({});
  const sectionIds = useMemo(
    () => [
      "top",
      ...(takeaways ? ["sop"] : []),
      ...bodySections.map((s) => s.id),
      ...(d?.intervenors.length ? ["intervenors"] : []),
      "filings",
      ...substantive.map((f) => `f${f.id}`),
    ],
    [takeaways, bodySections, d?.intervenors.length, substantive],
  );
  const active = useScrollSpy(sectionIds, refs, "top");
  const jump = (target: string) => {
    const el = refs.current[target];
    const main = document.querySelector(".main") as HTMLElement | null;
    if (!el || !main) return;
    main.scrollTo({ top: el.offsetTop - 80, behavior: "smooth" });
  };

  if (isLoading || !d) {
    return (
      <>
        <Topbar
          crumbs={[{ label: "FERC eLibrary", to: "/elibrary" }, { label: "…" }]}
        />
        <div className="page">
          <div className="empty">
            {isLoading ? "Loading…" : "This docket doesn't exist."}
          </div>
        </div>
      </>
    );
  }

  const brief = d.brief;

  return (
    <>
      <Topbar
        crumbs={[
          { label: "FERC eLibrary", to: "/elibrary" },
          { label: d.docket_number },
        ]}
        actions={
          <>
            <button
              className="btn btn-ghost btn-sm"
              disabled={!!jobActive || jobs.isStartingSync}
              onClick={jobs.startSync}
              title="Crawl eLibrary for new filings, summarize them, refresh the state of play"
            >
              <Icon name="refresh" size={12} />
              {jobs.isStartingSync ? "Starting…" : "Sync"}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={del.isPending || !!jobActive}
              onClick={() => {
                if (
                  window.confirm(
                    `Stop tracking ${d.docket_number}? All stored filings and summaries are removed. This cannot be undone.`,
                  )
                ) {
                  del.mutate();
                }
              }}
            >
              <Icon name="trash" size={12} /> Delete
            </button>
          </>
        }
      />

      <div className="el-layout">
        <aside className="briefing-side">
          <nav className="b-toc">
            <div className="b-toc-label">On this page</div>
            <ul>
              <li className={active === "top" ? "on" : ""}>
                <button onClick={() => jump("top")}>
                  {takeaways ? "Key Takeaways" : "State of Play"}
                </button>
              </li>
              {takeaways && (
                <li className={active === "sop" ? "on" : ""}>
                  <button onClick={() => jump("sop")}>State of Play</button>
                </li>
              )}
              {bodySections.map((s) => (
                <li
                  key={s.id}
                  className={`toc-sub${active === s.id ? " on" : ""}`}
                >
                  <button onClick={() => jump(s.id)}>{s.title}</button>
                </li>
              ))}
              {d.intervenors.length > 0 && (
                <li className={active === "intervenors" ? "on" : ""}>
                  <button onClick={() => jump("intervenors")}>
                    Intervenors
                  </button>
                </li>
              )}
              <li className={active === "filings" ? "on" : ""}>
                <button onClick={() => jump("filings")}>Filings</button>
              </li>
              {substantive.map((f, i) => (
                <li
                  key={f.id}
                  className={`toc-sub${active === `f${f.id}` ? " on" : ""}`}
                >
                  <button onClick={() => jump(`f${f.id}`)}>
                    <span className="toc-num">{i + 1}</span>
                    <span>
                      {classLabel(f)}
                      {authorLine(f) ? ` — ${authorLine(f)}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        </aside>

        <div className="el-article">
        <div
          className="page-header"
          ref={(el) => {
            refs.current.top = el;
          }}
        >
          <div className="page-eyebrow">FERC docket</div>
          <h1 className="page-title">{d.docket_number}</h1>
          {d.title && <p className="page-subtitle">{d.title}</p>}
          <p className="el-meta">
            {d.filings.length} filing{d.filings.length === 1 ? "" : "s"} ·{" "}
            {d.intervenors.length} intervenor
            {d.intervenors.length === 1 ? "" : "s"}
            {d.last_crawled_at &&
              ` · last checked ${new Date(d.last_crawled_at).toLocaleString()}`}
          </p>
        </div>

        {jobs.job && (jobActive || jobs.job.status === "failed") && (
          <div
            className={`el-job ${jobs.job.status === "failed" ? "el-job-failed" : ""}`}
          >
            <Icon
              name={jobs.job.status === "failed" ? "x" : "refresh"}
              size={14}
            />
            <div className="el-job-text">
              <div className="el-job-title">
                {jobs.job.status === "failed"
                  ? "Job failed"
                  : jobs.job.mode === "brief"
                    ? "Updating the state of play…"
                    : "Syncing with eLibrary…"}
              </div>
              <div className="el-job-progress">
                {jobs.job.status === "failed"
                  ? jobs.job.error || "Unknown error"
                  : jobs.job.progress_text || "Working…"}
              </div>
            </div>
            {jobActive ? (
              <button
                className="btn btn-ghost btn-sm"
                disabled={jobs.isCancelling || jobs.job.status === "cancelling"}
                onClick={() => jobs.cancel(jobs.job!.id)}
              >
                {jobs.job.status === "cancelling" ? "Cancelling…" : "Cancel"}
              </button>
            ) : (
              <button className="btn btn-ghost btn-sm" onClick={jobs.dismiss}>
                Dismiss
              </button>
            )}
          </div>
        )}

        {/* ── Key takeaways band (briefing treatment) ───────────────── */}
        {takeaways && (
          <section className="briefing-tldr el-section">
            <div className="b-eyebrow">Key takeaways</div>
            <ol>
              {takeaways.map((t, i) => (
                <li key={i}>
                  <span className="tldr-num">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span>{inlineMd(t)}</span>
                </li>
              ))}
            </ol>
          </section>
        )}

        {/* ── State of play ─────────────────────────────────────────── */}
        <section
          className="el-section"
          ref={(el) => {
            refs.current.sop = el;
          }}
        >
          <div className="el-section-head">
            <h2 className="el-section-title">State of Play</h2>
            <div className="el-section-actions">
              {brief?.stale && (
                <span className="el-stale" title="Filing summaries are newer than this report">
                  stale
                </span>
              )}
              {brief && (
                <>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => navigate(`/edit/docket/${did}`)}
                  >
                    <Icon name="edit" size={12} /> Edit
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setShowHistory(!showHistory)}
                  >
                    <Icon name="eye" size={12} /> History
                  </button>
                </>
              )}
              <button
                className="btn btn-ghost btn-sm"
                disabled={!!jobActive || jobs.isStartingBrief}
                onClick={jobs.startBrief}
              >
                <Icon name="spark" size={12} />
                {brief ? "Regenerate" : "Generate"}
              </button>
            </div>
          </div>

          {showHistory && brief && (
            <VersionHistory
              entityType="docket"
              entityId={did}
              currentVersionId={brief.summary_id}
              onRestored={() => setShowHistory(false)}
            />
          )}

          {brief?.detailed ? (
            <article className="ru-body">
              {sop.preamble && <Markdown source={sop.preamble} preserveH2 />}
              {bodySections.map((s) => (
                <div
                  key={s.id}
                  ref={(el) => {
                    refs.current[s.id] = el;
                  }}
                >
                  <Markdown source={s.md} preserveH2 />
                </div>
              ))}
              {sop.sections.length === 0 && (
                <Markdown source={brief.detailed} preserveH2 />
              )}
            </article>
          ) : (
            <div className="empty">
              No state of play yet — it generates automatically after the
              first sync summarizes filings, or click Generate.
            </div>
          )}
          {brief && (
            <div className="el-brief-meta">
              v{brief.version}
              {brief.is_manual ? " · manual edit" : ""} ·{" "}
              {brief.created_at
                ? new Date(brief.created_at).toLocaleString()
                : ""}
            </div>
          )}
        </section>

        {/* ── Intervenors ───────────────────────────────────────────── */}
        {roster.length > 0 && (
          <section
            className="el-section"
            ref={(el) => {
              refs.current.intervenors = el;
            }}
          >
            <div className="el-section-head">
              <h2 className="el-section-title">
                Intervenors <span className="el-count">{roster.length}</span>
              </h2>
            </div>
            <ul className="el-intervenor-cols">
              {roster.map((iv) => (
                <li key={iv.org} title={`Intervened ${fmtDate(iv.date)}`}>
                  {iv.org}
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* ── Filings timeline ──────────────────────────────────────── */}
        <section
          className="el-section"
          ref={(el) => {
            refs.current.filings = el;
          }}
        >
          <div className="el-section-head">
            <h2 className="el-section-title">
              Filings <span className="el-count">{substantive.length}</span>
            </h2>
          </div>
          {substantive.length === 0 ? (
            <div className="empty">No filings crawled yet.</div>
          ) : (
            <div className="el-filings">
              {substantive.map((f) => (
                <div
                  key={f.id}
                  ref={(el) => {
                    refs.current[`f${f.id}`] = el;
                  }}
                >
                  <FilingRow f={f} />
                </div>
              ))}
            </div>
          )}
          {administrative.length > 0 && (
            <div className="el-interventions-note">
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setShowAdmin(!showAdmin)}
              >
                <Icon name="filter" size={12} />
                {showAdmin ? "Hide" : "Show"} {administrative.length}{" "}
                administrative filing{administrative.length === 1 ? "" : "s"}
              </button>
              {showAdmin && (
                <div className="el-filings" style={{ marginTop: 8 }}>
                  {administrative.map((f) => (
                    <FilingRow key={f.id} f={f} />
                  ))}
                </div>
              )}
            </div>
          )}
          {interventions.length > 0 && (
            <div className="el-interventions-note">
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setShowInterventions(!showInterventions)}
              >
                <Icon name="users" size={12} />
                {showInterventions ? "Hide" : "Show"} {interventions.length}{" "}
                intervention filing{interventions.length === 1 ? "" : "s"}
              </button>
              {showInterventions && (
                <div className="el-filings" style={{ marginTop: 8 }}>
                  {interventions.map((f) => (
                    <FilingRow key={f.id} f={f} />
                  ))}
                </div>
              )}
            </div>
          )}
        </section>

        <div style={{ height: 64 }} />
        </div>
      </div>
    </>
  );
}
