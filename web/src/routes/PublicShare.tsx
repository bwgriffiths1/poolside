import { useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Icon } from "../components/Icon";
import { VenueTag, TypeTag } from "../components/Tag";
import { BlockRenderer } from "../components/briefing/BlockRenderer";
import { DocCards, SectionDocs } from "../components/briefing/SectionDocs";
import { MeetingLinks } from "../components/meeting/MeetingLinks";
import {
  FilingRow,
  authorLine,
  classLabel,
} from "../components/docket/FilingRow";
import { useScrollSpy } from "../hooks/useScrollSpy";
import {
  api,
  type PublicShareDocket,
  type PublicShareMeeting,
} from "../lib/api";
import { qk } from "../lib/queries";
import { Markdown, inlineMd } from "../lib/markdown";
import { extractTakeaways, splitByH2 } from "../lib/stateOfPlay";
import type { Briefing as BriefingType } from "../types";

/**
 * Public, read-only share views. No auth required — backed by the
 * /api/public/share/:token endpoint, whose payload discriminates on
 * "kind": meeting tokens render the briefing reader, docket tokens the
 * docket page. Same content as the authenticated views but with no
 * edit / approve / share controls and no sidebar.
 */
export function PublicShare() {
  const { token } = useParams<{ token: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: qk.publicShare(token!),
    queryFn: () => api.publicShare(token as string),
    enabled: !!token,
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="public-share-shell">
        <div className="muted">Loading…</div>
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="public-share-shell">
        <div className="public-share-empty">
          <h1>Link unavailable</h1>
          <p className="muted">
            This share link is missing, has been revoked, or has expired.
          </p>
        </div>
      </div>
    );
  }

  if (data.kind === "docket") {
    return <PublicDocket d={data} token={token!} />;
  }
  return <PublicBriefing data={data} />;
}

// ─── Meeting briefing view ──────────────────────────────────────────────

function PublicBriefing({ data }: { data: PublicShareMeeting }) {
  const refs = useRef<Record<string, HTMLElement | null>>({});
  const sectionIds = data.briefing
    ? [
        "top",
        ...(data.briefing.executive_summary?.length ? ["exec"] : []),
        ...data.briefing.sections.map((s) => s.id),
        ...(data.briefing.other_docs?.length ? ["sources"] : []),
      ]
    : ["top"];
  const active = useScrollSpy(sectionIds, refs, "top");

  const jump = (target: string) => {
    const el = refs.current[target];
    if (!el) return;
    window.scrollTo({ top: el.offsetTop - 32, behavior: "smooth" });
  };

  const b = data.briefing;

  return (
    <div className="public-share-shell">
      <header className="public-share-bar">
        <div className="mark">
          Poolside<span className="mark-accent">.</span>
        </div>
        <span style={{ flex: 1 }} />
        <span className="muted text-xs">Read-only briefing</span>
      </header>

      <div className="briefing-page" style={{ maxWidth: 980, margin: "0 auto" }}>
        <aside className="briefing-side">
          <TOC briefing={b} active={active} onJump={jump} />
        </aside>

        <article className="briefing-article">
          <header
            ref={(el) => {
              refs.current.top = el;
            }}
            className="briefing-header"
          >
            <div className="page-eyebrow">
              <VenueTag style={{ marginRight: 6 }}>{data.venue}</VenueTag>
              <TypeTag style={{ marginRight: 6 }}>{data.type_short}</TypeTag>
              <span>{b.subtitle}</span>
            </div>
            <h1 className="briefing-title">{b.title}</h1>
            <p className="briefing-headline">{b.headline}</p>

            <div className="briefing-meta-row">
              <span>
                <Icon name="dot" size={11} /> Generated {b.generated_at}
              </span>
              <span>
                <Icon name="dot" size={11} />{" "}
                {b.word_count.toLocaleString()} words · {b.reading_time} min read
              </span>
            </div>

            <MeetingLinks venue={data.venue} externalId={data.external_id} />
          </header>

          {b.tldr.length > 0 && (
          <section className="briefing-tldr">
            <div className="b-eyebrow">Key takeaways</div>
            <ol>
              {b.tldr.map((t, i) => (
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

          {b.executive_summary && b.executive_summary.length > 0 && (
            <section
              ref={(el) => {
                refs.current.exec = el;
              }}
              className="briefing-section briefing-exec"
            >
              <div className="b-eyebrow">Executive summary</div>
              <div className="b-section-body b-exec-body">
                {b.executive_summary.map((blk, i) => (
                  <BlockRenderer key={i} block={blk} />
                ))}
              </div>
            </section>
          )}

          {b.sections.map((s) => {
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
                  {s.vote && <div className="b-section-vote">{s.vote}</div>}
                </div>
              </div>
              <SectionDocs docs={s.docs} />
              {s.body.length > 0 && (
                <div className="b-section-body">
                  {s.body.map((blk, i) => (
                    <BlockRenderer key={i} block={blk} />
                  ))}
                </div>
              )}
              {s.next_steps && s.next_steps.length > 0 && (
                <div className="b-next">
                  <div className="b-next-label">Next steps</div>
                  <ul>
                    {s.next_steps.map((n, i) => (
                      <li key={i}>{inlineMd(n)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
            );
          })}

          {(b.other_docs?.length ?? 0) > 0 && (
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
                </div>
              </div>
              <DocCards docs={b.other_docs!} />
            </section>
          )}

          <footer className="briefing-footer">
            <div className="muted text-sm">
              Shared from Poolside · {b.model} · {b.generated_at}
            </div>
          </footer>
        </article>
      </div>
    </div>
  );
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
          <button onClick={() => onJump("top")}>Headline & TL;DR</button>
        </li>
        {briefing.sections.map((s) => (
          <li key={s.id} className={active === s.id ? "on" : ""}>
            <button onClick={() => onJump(s.id)}>
              <span className="toc-num">{s.item_id}</span>
              <span>{s.title}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

// ─── Docket view ────────────────────────────────────────────────────────

function PublicDocket({ d, token }: { d: PublicShareDocket; token: string }) {
  const [showInterventions, setShowInterventions] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);

  // File downloads go through the token-scoped public passthrough — the
  // session-gated route 401s for anonymous share viewers.
  const fileHref = (fileId: number) =>
    `/api/public/share/${encodeURIComponent(token)}/files/${fileId}/download`;

  const { substantive, administrative, interventions } = useMemo(() => {
    const filings = d.filings ?? [];
    return {
      substantive: filings.filter((f) => f.treatment !== "skip"),
      administrative: filings.filter(
        (f) => f.treatment === "skip" && f.document_class !== "Intervention",
      ),
      interventions: filings.filter(
        (f) => f.treatment === "skip" && f.document_class === "Intervention",
      ),
    };
  }, [d.filings]);

  const roster = useMemo(
    () =>
      [...(d.intervenors ?? [])].sort((a, b) =>
        a.org.localeCompare(b.org, "en", { sensitivity: "base" }),
      ),
    [d.intervenors],
  );

  const sop = useMemo(() => splitByH2(d.brief?.detailed), [d.brief?.detailed]);
  const { takeaways, bodySections } = useMemo(
    () => extractTakeaways(sop.sections),
    [sop.sections],
  );

  const refs = useRef<Record<string, HTMLElement | null>>({});
  const sectionIds = useMemo(
    () => [
      "top",
      ...(takeaways ? ["sop"] : []),
      ...bodySections.map((s) => s.id),
      ...(d.intervenors.length ? ["intervenors"] : []),
      "filings",
      ...substantive.map((f) => `f${f.id}`),
    ],
    [takeaways, bodySections, d.intervenors.length, substantive],
  );
  const active = useScrollSpy(sectionIds, refs, "top");
  const jump = (target: string) => {
    const el = refs.current[target];
    if (!el) return;
    window.scrollTo({ top: el.offsetTop - 32, behavior: "smooth" });
  };

  const brief = d.brief;

  return (
    <div className="public-share-shell">
      <header className="public-share-bar" style={{ maxWidth: 1180 }}>
        <div className="mark">
          Poolside<span className="mark-accent">.</span>
        </div>
        <span style={{ flex: 1 }} />
        <span className="muted text-xs">Read-only docket</span>
      </header>

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
            {(d.title || d.party_label) && (
              <p className="page-subtitle el-tagline">
                {d.party_label && `${d.party_label}: `}
                {d.title}
              </p>
            )}
            <p className="el-meta">
              {d.filings.length} filing{d.filings.length === 1 ? "" : "s"} ·{" "}
              {d.intervenors.length} intervenor
              {d.intervenors.length === 1 ? "" : "s"}
            </p>
          </div>

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
            </div>

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
              <div className="empty">No state of play yet.</div>
            )}
            {brief && (
              <div className="el-brief-meta">
                v{brief.version} ·{" "}
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
                  <li key={iv.org}>{iv.org}</li>
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
              <div className="empty">No filings yet.</div>
            ) : (
              <div className="el-filings">
                {substantive.map((f) => (
                  <div
                    key={f.id}
                    ref={(el) => {
                      refs.current[`f${f.id}`] = el;
                    }}
                  >
                    <FilingRow f={f} canEdit={false} fileHref={fileHref} />
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
                      <FilingRow
                        key={f.id}
                        f={f}
                        canEdit={false}
                        fileHref={fileHref}
                      />
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
                      <FilingRow
                        key={f.id}
                        f={f}
                        canEdit={false}
                        fileHref={fileHref}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>

          <footer className="briefing-footer">
            <div className="muted text-sm">
              Shared from Poolside · FERC Docket {d.docket_number}
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
}
