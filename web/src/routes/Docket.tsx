import { useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { VersionHistory } from "../components/VersionHistory";
import { ShareLinkModal } from "../components/ShareLinkModal";
import {
  FilingRow,
  authorLine,
  classLabel,
  fmtDate,
} from "../components/docket/FilingRow";
import { api } from "../lib/api";
import { qk, useCan } from "../lib/queries";
import { toast } from "../lib/toast";
import { Markdown, inlineMd } from "../lib/markdown";
import { extractTakeaways, splitByH2 } from "../lib/stateOfPlay";
import { useDocketJob } from "../hooks/useDocketJob";
import { useScrollSpy } from "../hooks/useScrollSpy";
import { useTrackView } from "../hooks/useTrackView";

export function Docket() {
  const { id } = useParams();
  const did = Number(id);
  useTrackView("docket", did);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { canEdit } = useCan();
  const jobs = useDocketJob(did);
  const [showHistory, setShowHistory] = useState(false);
  const [showInterventions, setShowInterventions] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [showShare, setShowShare] = useState(false);
  // Header edit — titleDraft is null when not editing, else the tagline draft.
  // partyDraft rides alongside it (the venue/party prefix) so one editor and
  // one Save cover both fields the cover subtitle is built from.
  const [titleDraft, setTitleDraft] = useState<string | null>(null);
  const [partyDraft, setPartyDraft] = useState("");

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

  // The tagline starts life as FERC's root-filing description (truncated),
  // which is rarely how you'd describe the proceeding — let editors rewrite
  // it. The venue/party prefix is editor-set too (blank = just the tagline);
  // together they render as "ISO-NE: <tagline>" on the cover. Saving "" clears
  // either for good; the crawler only auto-fills a NULL title.
  const saveHeader = useMutation({
    mutationFn: (body: { title: string; party_label: string }) =>
      api.updateDocket(did, body),
    onSuccess: () => {
      setTitleDraft(null);
      qc.invalidateQueries({ queryKey: qk.docket(did) });
      qc.invalidateQueries({ queryKey: qk.dockets });
      toast.success("Header updated");
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Couldn't save the header"),
  });

  const { substantive, administrative, interventions } = useMemo(() => {
    const filings = d?.filings ?? [];
    return {
      // Treatment decides the split, not class: an Intervention paired
      // with a protest/comments (doc-ful) is substantive and belongs in
      // the main timeline with a summary.
      substantive: filings.filter((f) => f.treatment !== "skip"),
      // Skip-tier housekeeping (notices, counsel/service-list changes,
      // transcripts…) collapses behind a toggle — signal stays up top.
      administrative: filings.filter(
        (f) => f.treatment === "skip" && f.document_class !== "Intervention",
      ),
      interventions: filings.filter(
        (f) => f.treatment === "skip" && f.document_class === "Intervention",
      ),
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

  // "Key Takeaways" band above the State of Play — see extractTakeaways.
  const { takeaways, bodySections } = useMemo(
    () => extractTakeaways(sop.sections),
    [sop.sections],
  );

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
            {canEdit && (
              <button
                className="btn btn-ghost btn-sm"
                disabled={!!jobActive || jobs.isStartingSync}
                onClick={jobs.startSync}
                title="Crawl eLibrary for new filings, summarize them, refresh the state of play"
              >
                <Icon name="refresh" size={12} />
                {jobs.isStartingSync ? "Starting…" : "Sync"}
              </button>
            )}
            {brief?.detailed && (
              // Plain same-origin link: the browser downloads natively via
              // Content-Disposition. No fetch/blob/revokeObjectURL dance —
              // that pattern crashed Safari on first use.
              <a
                className="btn btn-ghost btn-sm"
                href={`/api/dockets/${did}/docx`}
                title="Word export: state of play + one page per filing with eLibrary links"
              >
                <Icon name="download" size={12} /> Word
              </a>
            )}
            {canEdit && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setShowShare(true)}
                title="Generate a public link to share this docket without login"
              >
                <Icon name="link" size={12} /> Share
              </button>
            )}
            {canEdit && (
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
            )}
          </>
        }
      />

      {canEdit && showShare && (
        <ShareLinkModal
          label="docket"
          queryKey={qk.docketShareTokens(did)}
          list={() => api.listDocketShareLinks(did)}
          create={(days) => api.createDocketShareLink(did, days)}
          onClose={() => setShowShare(false)}
        />
      )}

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

          {titleDraft !== null ? (
            <form
              className="el-header-edit"
              onSubmit={(e) => {
                e.preventDefault();
                saveHeader.mutate({
                  title: titleDraft.trim(),
                  party_label: partyDraft.trim(),
                });
              }}
            >
              <label className="el-header-field">
                <span>Venue prefix</span>
                <input
                  className="input"
                  value={partyDraft}
                  placeholder="e.g. ISO-NE — optional"
                  onChange={(e) => setPartyDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setTitleDraft(null);
                  }}
                />
              </label>
              <label className="el-header-field">
                <span>Tagline</span>
                <input
                  className="input"
                  value={titleDraft}
                  autoFocus
                  placeholder="How you'd describe this proceeding"
                  onChange={(e) => setTitleDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setTitleDraft(null);
                  }}
                />
              </label>
              <div className="el-header-actions">
                <button
                  className="btn btn-sm btn-accent"
                  type="submit"
                  disabled={saveHeader.isPending}
                >
                  {saveHeader.isPending ? "Saving…" : "Save"}
                </button>
                <button
                  className="btn btn-sm btn-ghost"
                  type="button"
                  disabled={saveHeader.isPending}
                  onClick={() => setTitleDraft(null)}
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            (d.title || d.party_label || canEdit) && (
              <p className="page-subtitle el-tagline">
                {d.title || d.party_label ? (
                  <>
                    {d.party_label && `${d.party_label}: `}
                    {d.title}
                  </>
                ) : (
                  <span className="el-tagline-empty">No tagline</span>
                )}
                {canEdit && (
                  <button
                    className="el-tagline-btn"
                    title="Edit the venue prefix and tagline under the docket number"
                    onClick={() => {
                      setPartyDraft(d.party_label || "");
                      setTitleDraft(d.title || "");
                    }}
                  >
                    <Icon name="edit" size={11} /> Edit
                  </button>
                )}
              </p>
            )
          )}

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
              canEdit && (
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={jobs.isCancelling || jobs.job.status === "cancelling"}
                  onClick={() => jobs.cancel(jobs.job!.id)}
                >
                  {jobs.job.status === "cancelling" ? "Cancelling…" : "Cancel"}
                </button>
              )
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
                  {canEdit && (
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => navigate(`/edit/docket/${did}`)}
                    >
                      <Icon name="edit" size={12} /> Edit
                    </button>
                  )}
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setShowHistory(!showHistory)}
                  >
                    <Icon name="eye" size={12} /> History
                  </button>
                </>
              )}
              {canEdit && (
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={!!jobActive || jobs.isStartingBrief}
                  onClick={jobs.startBrief}
                >
                  <Icon name="spark" size={12} />
                  {brief ? "Regenerate" : "Generate"}
                </button>
              )}
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
                  <FilingRow f={f} canEdit={canEdit} />
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
                    <FilingRow key={f.id} f={f} canEdit={canEdit} />
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
                    <FilingRow key={f.id} f={f} canEdit={canEdit} />
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
