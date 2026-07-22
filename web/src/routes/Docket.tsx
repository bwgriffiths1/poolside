import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Tag } from "../components/Tag";
import { VersionHistory } from "../components/VersionHistory";
import { api, type DocketFiling } from "../lib/api";
import { qk } from "../lib/queries";
import { Markdown } from "../lib/markdown";
import { useDocketJob } from "../hooks/useDocketJob";

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
          {f.summary_one_line && f.description && (
            <div className="el-filing-origdesc">{f.description}</div>
          )}
          {f.summary_detailed && (
            <article className="el-filing-summary">
              <Markdown source={f.summary_detailed} />
            </article>
          )}
          {f.files.length > 0 && (
            <div className="el-files">
              {f.files.map((x) => (
                <div
                  key={x.id}
                  className={`el-file${x.included ? "" : " excluded"}`}
                  title={x.included ? undefined : "Excluded from summarization"}
                >
                  <Icon name="doc" size={12} />
                  <span className="el-file-desc">
                    {x.file_desc || x.orig_file_name}
                  </span>
                  <span className="el-file-meta mono">
                    {x.page_count ? `${x.page_count}pp · ` : ""}
                    {fmtBytes(x.file_size)}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="el-filing-actions">
            <a
              className="btn btn-ghost btn-sm"
              href={f.elibrary_url}
              target="_blank"
              rel="noreferrer"
            >
              <Icon name="external" size={12} /> eLibrary
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

  const { substantive, interventions } = useMemo(() => {
    const filings = d?.filings ?? [];
    return {
      substantive: filings.filter((f) => f.document_class !== "Intervention"),
      interventions: filings.filter(
        (f) => f.document_class === "Intervention",
      ),
    };
  }, [d?.filings]);

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

      <div className="page">
        <div className="page-header">
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

        {/* ── State of play ─────────────────────────────────────────── */}
        <section className="el-section">
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
              <Markdown source={brief.detailed} preserveH2 />
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
        {d.intervenors.length > 0 && (
          <section className="el-section">
            <div className="el-section-head">
              <h2 className="el-section-title">
                Intervenors{" "}
                <span className="el-count">{d.intervenors.length}</span>
              </h2>
            </div>
            <div className="el-intervenors">
              {d.intervenors.map((iv) => (
                <span className="el-intervenor" key={iv.org} title={`Intervened ${fmtDate(iv.date)}`}>
                  {iv.org}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* ── Filings timeline ──────────────────────────────────────── */}
        <section className="el-section">
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
                <FilingRow key={f.id} f={f} />
              ))}
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
    </>
  );
}
