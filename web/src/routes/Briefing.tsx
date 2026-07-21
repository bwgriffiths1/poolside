import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { VenueTag, TypeTag } from "../components/Tag";
import { BlockRenderer } from "../components/briefing/BlockRenderer";
import { DocCards, SectionDocs } from "../components/briefing/SectionDocs";
import { MeetingLinks } from "../components/meeting/MeetingLinks";
import { VersionHistory } from "../components/VersionHistory";
import { useScrollSpy } from "../hooks/useScrollSpy";
import { useReadingProgress } from "../hooks/useReadingProgress";
import { api, type ShareToken } from "../lib/api";
import { qk, useBriefing, useMeeting } from "../lib/queries";
import { toast } from "../lib/toast";
import { inlineMd } from "../lib/markdown";
import type { Briefing as BriefingType } from "../types";

function voteOk(vote?: string): boolean {
  return !!vote && vote.toLowerCase().includes("approved");
}

function hasDecisions(briefing: BriefingType): boolean {
  return briefing.sections.some((s) => s.vote || (s.next_steps && s.next_steps.length > 0));
}

function TOC({
  briefing,
  active,
  onJump,
}: {
  briefing: BriefingType;
  active: string;
  onJump: (id: string) => void;
}) {
  return (
    <nav className="b-toc">
      <div className="b-toc-label">On this page</div>
      <ul>
        <li className={active === "top" ? "on" : ""}>
          <button onClick={() => onJump("top")}>Headline &amp; TL;DR</button>
        </li>
        {briefing.executive_summary && briefing.executive_summary.length > 0 && (
          <li className={active === "exec" ? "on" : ""}>
            <button onClick={() => onJump("exec")}>
              <span className="toc-num" />
              <span>Executive summary</span>
            </button>
          </li>
        )}
        {briefing.sections.map((s) => (
          <li
            key={s.id}
            className={`${active === s.id ? "on" : ""}${
              (s.depth ?? 0) === 1 ? " toc-sub" : ""
            }`}
          >
            <button onClick={() => onJump(s.id)}>
              <span className="toc-num">{s.item_id}</span>
              <span>{s.title}</span>
            </button>
          </li>
        ))}
        <li className={active === "decisions" ? "on" : ""}>
          <button onClick={() => onJump("decisions")}>
            <span className="toc-num" />
            <span>Decisions &amp; next steps</span>
          </button>
        </li>
        {(briefing.other_docs?.length ?? 0) > 0 && (
          <li className={active === "sources" ? "on" : ""}>
            <button onClick={() => onJump("sources")}>
              <span className="toc-num" />
              <span>Other documents</span>
            </button>
          </li>
        )}
      </ul>
      <div className="b-toc-meta">
        <div className="row">
          <Icon name="dot" size={11} />
          <span className="text-xs">{briefing.reading_time} min read</span>
        </div>
        <div className="row">
          <Icon name="dot" size={11} />
          <span className="text-xs mono">{briefing.model}</span>
        </div>
      </div>
    </nav>
  );
}

export function Briefing() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const meetingId = Number(id);

  const { data: m, isLoading: meetingLoading } = useMeeting(meetingId);
  const {
    data: briefing,
    isLoading: briefingLoading,
    error: briefingError,
  } = useBriefing(meetingId);

  const [showVersions, setShowVersions] = useState(false);
  const [showShare, setShowShare] = useState(false);
  const qc = useQueryClient();
  const approval = useQuery({
    queryKey: qk.approval(meetingId),
    queryFn: () => api.getApproval(meetingId),
    enabled: Number.isFinite(meetingId),
    retry: false,
  });
  const approveMut = useMutation({
    mutationFn: () =>
      approval.data?.status === "approved"
        ? api.unapproveBriefing(meetingId)
        : api.approveBriefing(meetingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.approval(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetings });
    },
    onError: (e: Error) => toast.error(`Approval failed: ${e.message}`),
  });
  const isApproved = approval.data?.status === "approved";
  const refs = useRef<Record<string, HTMLElement | null>>({});
  const sectionIds = briefing
    ? [
        "top",
        ...(briefing.executive_summary?.length ? ["exec"] : []),
        ...briefing.sections.map((s) => s.id),
        "decisions",
        ...(briefing.other_docs?.length ? ["sources"] : []),
      ]
    : ["top"];
  const active = useScrollSpy(sectionIds, refs, "top");
  const progress = useReadingProgress();

  const jump = (target: string) => {
    const el = refs.current[target];
    const main = document.querySelector(".main") as HTMLElement | null;
    if (!el || !main) return;
    main.scrollTo({ top: el.offsetTop - 80, behavior: "smooth" });
  };

  // Deep link: ?s=<section-id> scrolls once the briefing has rendered.
  const [params] = useSearchParams();
  const deepLinked = useRef(false);
  useEffect(() => {
    const target = params.get("s");
    if (!target || !briefing || deepLinked.current) return;
    deepLinked.current = true;
    requestAnimationFrame(() => jump(target));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [briefing, params]);

  const copySectionLink = (sectionId: string) => {
    const url =
      `${window.location.origin}${window.location.pathname}` +
      `#/briefing/${meetingId}?s=${encodeURIComponent(sectionId)}`;
    navigator.clipboard
      .writeText(url)
      .then(() => toast.success("Section link copied."))
      .catch(() => toast.error("Couldn't copy the link."));
  };

  // Documents live on their own section now (SectionDocs); only what maps to
  // no section falls through to the list at the end.
  const otherDocs = briefing?.other_docs ?? [];

  if (meetingLoading || briefingLoading) {
    return (
      <>
        <Topbar
          crumbs={[
            { label: "Briefings", to: "/briefings" },
            { label: "Briefing" },
          ]}
        />
        <div className="page">
          <div className="muted">Loading briefing…</div>
        </div>
      </>
    );
  }

  if (!briefing || (!briefing.sections.length && !briefing.tldr.length) || briefingError) {
    return (
      <>
        <Topbar
          crumbs={[
            { label: "Briefings", to: "/briefings" },
            m && { label: `${m.venue} · ${m.type_short}`, to: `/meeting/${m.id}` },
            { label: "Briefing" },
          ].filter(Boolean) as { label: string; to?: string }[]}
        />
        <div className="page">
          <div className="page-header">
            <div className="page-eyebrow">No briefing</div>
            <h1 className="page-title">
              No briefing has been generated for this meeting yet.
            </h1>
            <p className="page-subtitle">
              Briefings are produced by running summarization from the meeting
              detail page. Once a meeting has agenda items and documents
              ingested, click <strong>Summarize</strong> on the meeting page to
              generate one.
            </p>
          </div>
          {m && (
            <button
              className="btn btn-primary"
              onClick={() => navigate(`/meeting/${m.id}`)}
            >
              Go to meeting →
            </button>
          )}
        </div>
      </>
    );
  }

  if (!m) {
    return (
      <>
        <Topbar crumbs={[{ label: "Briefing not found" }]} />
        <div className="page">
          <div className="muted">Meeting not found.</div>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar
        crumbs={[
          { label: "Briefings", to: "/briefings" },
          { label: `${m.venue} · ${m.type_short}`, to: `/meeting/${m.id}` },
          { label: "Briefing" },
        ]}
        actions={
          <>
            <button
              className="btn btn-sm btn-ghost"
              disabled={!briefing?.prev_meeting_id}
              onClick={() =>
                briefing?.prev_meeting_id &&
                navigate(`/briefing/${briefing.prev_meeting_id}`)
              }
              title="Previous briefing (older)"
            >
              <Icon name="arrow-l" />
            </button>
            <button
              className="btn btn-sm btn-ghost"
              disabled={!briefing?.next_meeting_id}
              onClick={() =>
                briefing?.next_meeting_id &&
                navigate(`/briefing/${briefing.next_meeting_id}`)
              }
              title="Next briefing (newer)"
            >
              <Icon name="arrow-r" />
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => window.print()}
              title="Print this briefing (or save as PDF)"
            >
              <Icon name="doc" /> Print
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => setShowVersions(!showVersions)}
              title="Browse and restore previous versions of this briefing"
            >
              <Icon name="refresh" /> Versions
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => navigate(`/edit/meeting/${meetingId}`)}
            >
              <Icon name="edit" /> Edit
            </button>
            <button
              className="btn btn-sm"
              onClick={async () => {
                try {
                  await api.downloadBriefingDocx(meetingId);
                } catch (err) {
                  console.error("Download failed", err);
                  toast.error("Could not download briefing — see console for details.");
                }
              }}
            >
              <Icon name="download" /> Download .docx
            </button>
            <button
              className="btn btn-sm"
              onClick={() => setShowShare(true)}
              title="Generate a public link to share this briefing without login"
            >
              <Icon name="link" /> Share
            </button>
            <button
              className={`btn btn-sm ${isApproved ? "" : "btn-primary"}`}
              onClick={() => approveMut.mutate()}
              disabled={approveMut.isPending}
              title={
                isApproved
                  ? `Approved by ${approval.data?.approved_by ?? ""}`
                  : "Stamp this briefing as approved and notify watchers"
              }
            >
              <Icon name="check" />{" "}
              {approveMut.isPending
                ? "Working…"
                : isApproved
                ? "Unapprove"
                : "Approve & publish"}
            </button>
          </>
        }
      />

      {showShare && (
        <ShareLinkModal
          meetingId={meetingId}
          onClose={() => setShowShare(false)}
        />
      )}

      <div
        className="b-progress"
        style={{ transform: `scaleX(${progress})` }}
        aria-hidden
      />

      <div className="briefing-page">
        <aside className="briefing-side">
          <TOC briefing={briefing} active={active} onJump={jump} />
        </aside>

        <article className="briefing-article">
          <header
            ref={(el) => {
              refs.current.top = el;
            }}
            className="briefing-header"
          >
            <div className="page-eyebrow">
              <VenueTag style={{ marginRight: 6 }}>{m.venue}</VenueTag>
              <TypeTag style={{ marginRight: 6 }}>{m.type_short}</TypeTag>
              <span>{briefing.subtitle}</span>
            </div>
            <h1 className="briefing-title">{briefing.title}</h1>
            <p className="briefing-headline">{briefing.headline}</p>

            <div className="briefing-meta-row">
              <span>
                <Icon name="dot" size={11} /> Generated {briefing.generated_at}
              </span>
              <span>
                <Icon name="dot" size={11} />{" "}
                {briefing.word_count.toLocaleString()} words ·{" "}
                {briefing.reading_time} min read
              </span>
              <span>
                <Icon name="dot" size={11} /> {briefing.model}
              </span>
              {approval.data && (
                <span>
                  <Icon name="dot" size={11} />{" "}
                  {approval.data.status === "approved" ? (
                    <>
                      <strong style={{ color: "var(--success)" }}>Approved</strong>
                      {approval.data.approved_by && (
                        <> by {approval.data.approved_by}</>
                      )}
                    </>
                  ) : (
                    <span className="muted">Draft</span>
                  )}
                </span>
              )}
            </div>

            <MeetingLinks venue={m.venue} externalId={m.external_id} />
          </header>

          {showVersions && (
            <section style={{ marginBottom: 32 }}>
              <div className="b-eyebrow">Version history</div>
              <VersionHistory
                entityType="meeting"
                entityId={meetingId}
                meetingId={meetingId}
                onRestored={() => setShowVersions(false)}
              />
            </section>
          )}

          {briefing.tldr.length > 0 && (
          <section className="briefing-tldr">
            <div className="b-eyebrow">Key takeaways</div>
            <ol>
              {briefing.tldr.map((t, i) => (
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

          {briefing.executive_summary && briefing.executive_summary.length > 0 && (
            <section
              ref={(el) => {
                refs.current.exec = el;
              }}
              className="briefing-section briefing-exec"
            >
              <div className="b-eyebrow">Executive summary</div>
              <div className="b-section-body b-exec-body">
                {briefing.executive_summary.map((b, i) => (
                  <BlockRenderer key={i} block={b} />
                ))}
              </div>
            </section>
          )}

          {briefing.sections.map((s) => {
            const depth = s.depth ?? 0;
            return (
            <section
              key={s.id}
              ref={(el) => {
                refs.current[s.id] = el;
              }}
              className={`briefing-section b-depth-${depth}${
                depth === 0 ? " b-group" : ""
              }`}
            >
              <div className="b-section-head">
                <div className="b-section-num">{s.item_id}</div>
                <div>
                  <h2 className="b-h2">{s.title}</h2>
                  {s.vote && (
                    <div
                      className={`b-section-vote ${voteOk(s.vote) ? "ok" : ""}`}
                    >
                      {s.vote}
                    </div>
                  )}
                </div>
                <button
                  className="btn btn-sm btn-ghost b-section-link"
                  onClick={() => copySectionLink(s.id)}
                  title="Copy a link to this section"
                >
                  <Icon name="link" size={12} />
                </button>
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() => navigate(`/meeting/${m.id}`)}
                  title="Open in Meeting"
                >
                  <Icon name="external" size={12} />
                </button>
              </div>

              <SectionDocs docs={s.docs} />

              {s.body.length > 0 && (
                <div className="b-section-body">
                  {s.body.map((b, i) => (
                    <BlockRenderer key={i} block={b} />
                  ))}
                </div>
              )}

              {s.next_steps && s.next_steps.length > 0 && (
                <div className="b-next">
                  <div className="b-next-label">Next steps</div>
                  <ul>
                    {s.next_steps.map((n, i) => (
                      <li key={i}>
                        <span>{inlineMd(n)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
            );
          })}

          {hasDecisions(briefing) && (
            <section
              ref={(el) => {
                refs.current.decisions = el;
              }}
              className="briefing-section"
            >
              <div className="b-section-head">
                <div className="b-section-num">∗</div>
                <div>
                  <h2 className="b-h2">Decisions &amp; next steps</h2>
                </div>
              </div>
              <table className="b-decisions">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Title</th>
                    <th>Outcome</th>
                    <th>Next</th>
                  </tr>
                </thead>
                <tbody>
                  {briefing.sections.map((s) => {
                    if (!s.vote && !(s.next_steps?.length)) return null;
                    const outcome = s.vote || "Discussion";
                    const ok = /approve/i.test(outcome);
                    const next =
                      s.next_steps && s.next_steps.length > 0
                        ? s.next_steps[0]
                        : "—";
                    return (
                      <tr key={s.id}>
                        <td className="mono">{s.item_id}</td>
                        <td>{inlineMd(s.title)}</td>
                        <td>
                          <span className={ok ? "delta-pos" : ""}>
                            {inlineMd(outcome)}
                          </span>
                        </td>
                        <td>{inlineMd(next)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}

          {otherDocs.length > 0 && (
            <section
              ref={(el) => {
                refs.current.sources = el;
              }}
              className="briefing-section"
            >
              <div className="b-section-head">
                <div className="b-section-num">§</div>
                <div>
                  <h2 className="b-h2">Other documents</h2>
                  <div className="muted text-sm">
                    {otherDocs.length} file{otherDocs.length === 1 ? "" : "s"}{" "}
                    not tied to a section above · all available on the Meeting
                    page
                  </div>
                </div>
              </div>
              <DocCards docs={otherDocs} />
            </section>
          )}

          <footer className="briefing-footer">
            <div className="muted text-sm">
              Generated by Poolside · {briefing.model} · {briefing.generated_at}
            </div>
            {(briefing.prev_meeting_id || briefing.next_meeting_id) && (
              <nav className="b-footer-nav">
                {briefing.prev_meeting_id ? (
                  <button
                    className="b-footer-nav-btn"
                    onClick={() => navigate(`/briefing/${briefing.prev_meeting_id}`)}
                  >
                    <Icon name="arrow-l" size={12} />
                    <span>
                      <span className="b-footer-nav-label">Older</span>
                      Previous briefing
                    </span>
                  </button>
                ) : (
                  <span />
                )}
                {briefing.next_meeting_id && (
                  <button
                    className="b-footer-nav-btn b-footer-nav-next"
                    onClick={() => navigate(`/briefing/${briefing.next_meeting_id}`)}
                  >
                    <span>
                      <span className="b-footer-nav-label">Newer</span>
                      Next briefing
                    </span>
                    <Icon name="arrow-r" size={12} />
                  </button>
                )}
              </nav>
            )}
          </footer>
        </article>
      </div>
    </>
  );
}

// ─── Share modal ────────────────────────────────────────────────────────

function ShareLinkModal({
  meetingId,
  onClose,
}: {
  meetingId: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const tokens = useQuery({
    queryKey: qk.shareTokens(meetingId),
    queryFn: () => api.listShareLinks(meetingId),
  });
  const create = useMutation({
    mutationFn: (expires_days: number | null) =>
      api.createShareLink(meetingId, expires_days),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.shareTokens(meetingId) }),
    onError: (e: Error) => toast.error(`Create failed: ${e.message}`),
  });
  const revoke = useMutation({
    mutationFn: (token_id: number) => api.revokeShareLink(token_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.shareTokens(meetingId) }),
  });

  const [expiry, setExpiry] = useState<"30" | "90" | "never">("30");

  const onCreate = () => {
    const days = expiry === "never" ? null : Number(expiry);
    create.mutate(days);
  };

  const baseUrl = () => {
    // Use the same origin the user is on; hash router → /#/share/<token>.
    return `${window.location.origin}/#/share`;
  };

  const isActive = (t: ShareToken): boolean => {
    if (t.revoked_at) return false;
    if (t.expires_at && new Date(t.expires_at).getTime() < Date.now()) return false;
    return true;
  };

  const copy = async (t: ShareToken) => {
    const url = `${baseUrl()}/${t.token}`;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // Fallback: prompt — better than silent failure.
      window.prompt("Copy this link:", url);
    }
  };

  return (
    <div
      className="cmd-palette-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="share-modal" role="dialog" aria-label="Share briefing">
        <div className="share-modal-head">
          <h3 style={{ margin: 0, fontSize: 14 }}>Share this briefing</h3>
          <span style={{ flex: 1 }} />
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            <Icon name="x" size={12} />
          </button>
        </div>

        <p className="muted text-sm" style={{ margin: "8px 0 14px" }}>
          A share link opens this briefing without requiring login. Anyone
          with the URL can read it until you revoke or it expires.
        </p>

        <div className="row" style={{ gap: 8, marginBottom: 14 }}>
          <label className="field-label" style={{ marginBottom: 0 }}>
            Expires
          </label>
          <select
            className="select"
            value={expiry}
            onChange={(e) => setExpiry(e.target.value as "30" | "90" | "never")}
            style={{ width: 140 }}
          >
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="never">Never</option>
          </select>
          <span style={{ flex: 1 }} />
          <button
            className="btn btn-sm btn-accent"
            onClick={onCreate}
            disabled={create.isPending}
          >
            <Icon name="plus" size={12} />{" "}
            {create.isPending ? "Creating…" : "Create link"}
          </button>
        </div>

        {tokens.isLoading ? (
          <div className="muted text-sm">Loading…</div>
        ) : (tokens.data ?? []).length === 0 ? (
          <div className="muted text-sm">No share links yet.</div>
        ) : (
          <div className="share-list">
            {(tokens.data ?? []).map((t) => (
              <div
                key={t.id}
                className={`share-row ${isActive(t) ? "" : "inactive"}`}
              >
                <div className="share-row-main">
                  <div className="share-row-url mono text-xs">
                    {baseUrl()}/{t.token.slice(0, 10)}…
                  </div>
                  <div className="muted text-xs" style={{ marginTop: 2 }}>
                    Created {new Date(t.created_at).toLocaleDateString()} ·{" "}
                    {t.revoked_at
                      ? "revoked"
                      : t.expires_at
                      ? `expires ${new Date(t.expires_at).toLocaleDateString()}`
                      : "no expiry"}
                  </div>
                </div>
                {isActive(t) ? (
                  <>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => copy(t)}
                      title="Copy URL"
                    >
                      <Icon name="copy" size={12} /> Copy
                    </button>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => {
                        if (confirm("Revoke this share link?")) {
                          revoke.mutate(t.id);
                        }
                      }}
                      title="Revoke"
                    >
                      <Icon name="trash" size={12} />
                    </button>
                  </>
                ) : (
                  <span className="muted text-xs">
                    {t.revoked_at ? "Revoked" : "Expired"}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
