import { useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { VenueTag, TypeTag } from "../components/Tag";
import { BlockRenderer } from "../components/briefing/BlockRenderer";
import { useScrollSpy } from "../hooks/useScrollSpy";
import { api } from "../lib/api";
import { extFromFilename } from "../lib/format";
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
        {briefing.sections.map((s) => (
          <li key={s.id} className={active === s.id ? "on" : ""}>
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
        <li className={active === "sources" ? "on" : ""}>
          <button onClick={() => onJump("sources")}>
            <span className="toc-num" />
            <span>Source documents</span>
          </button>
        </li>
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

  const { data: m, isLoading: meetingLoading } = useQuery({
    queryKey: ["meeting", meetingId],
    queryFn: () => api.meeting(meetingId),
  });
  const { data: briefing, isLoading: briefingLoading, error: briefingError } = useQuery({
    queryKey: ["briefing", meetingId],
    queryFn: () => api.briefing(meetingId),
    retry: false,
  });

  const refs = useRef<Record<string, HTMLElement | null>>({});
  const sectionIds = briefing
    ? ["top", ...briefing.sections.map((s) => s.id), "decisions", "sources"]
    : ["top"];
  const active = useScrollSpy(sectionIds, refs, "top");

  const jump = (target: string) => {
    const el = refs.current[target];
    const main = document.querySelector(".main") as HTMLElement | null;
    if (!el || !main) return;
    main.scrollTo({ top: el.offsetTop - 80, behavior: "smooth" });
  };

  const allDocs = (m?.agenda ?? []).flatMap((i) =>
    i.docs.map((d) => ({ ...d, item: i.title, item_id: i.item_id }))
  );

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
              onClick={() => navigate(`/edit/meeting/${meetingId}`)}
            >
              <Icon name="edit" /> Edit
            </button>
            <button className="btn btn-sm">
              <Icon name="download" /> Download .docx
            </button>
            <button className="btn btn-sm btn-primary">
              <Icon name="check" /> Approve & publish
            </button>
          </>
        }
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
              <span>
                <Icon name="dot" size={11} /> v2 · awaiting review
              </span>
            </div>
          </header>

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

          <section className="briefing-intro">
            <p className="b-p has-dropcap">
              The Markets Committee convened May 12–13 in Holyoke for what
              proved to be the most consequential meeting of the spring
              stakeholder cycle. Three voting items reached resolution —
              Capacity Accreditation Phase 2 advanced to NPC, ESI Phase 2
              design was approved, and FCA 19 parameters were finalized —
              while two discussion items (DASI status, IBR performance) set
              up Q3 deliverables. Below: section-by-section analysis with
              positions and downstream implications.
            </p>
          </section>

          {briefing.sections.map((s) => (
            <section
              key={s.id}
              ref={(el) => {
                refs.current[s.id] = el;
              }}
              className="briefing-section"
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
                  className="btn btn-sm btn-ghost"
                  onClick={() => navigate(`/meeting/${m.id}`)}
                  title="Open in Meeting"
                >
                  <Icon name="external" size={12} />
                </button>
              </div>

              <div className="b-section-body">
                {s.body.map((b, i) => (
                  <BlockRenderer key={i} block={b} />
                ))}
              </div>

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
          ))}

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

          <section
            ref={(el) => {
              refs.current.sources = el;
            }}
            className="briefing-section"
          >
            <div className="b-section-head">
              <div className="b-section-num">§</div>
              <div>
                <h2 className="b-h2">Source documents</h2>
                <div className="muted text-sm">
                  {allDocs.length} files ingested · all available on the
                  Meeting page
                </div>
              </div>
            </div>
            <div className="b-sources">
              {allDocs.slice(0, 24).map((d) => {
                const href = d.source_url || undefined;
                return (
                  <a
                    key={d.id}
                    className="b-source"
                    href={href}
                    target={href ? "_blank" : undefined}
                    rel={href ? "noopener noreferrer" : undefined}
                    onClick={(e) => {
                      if (!href) e.preventDefault();
                    }}
                    style={!href ? { cursor: "default", opacity: 0.75 } : undefined}
                    title={href || "No source URL recorded for this document"}
                  >
                    <div className="b-source-ext">{extFromFilename(d.filename)}</div>
                    <div>
                      <div className="b-source-name">{d.filename}</div>
                      <div className="b-source-item">
                        Item {d.item_id} · {d.item}
                      </div>
                    </div>
                    <Icon name="external" size={12} />
                  </a>
                );
              })}
            </div>
          </section>

          <footer className="briefing-footer">
            <div className="muted text-sm">
              Generated by Poolside · {briefing.model} · {briefing.generated_at}
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn btn-sm">
                <Icon name="refresh" /> Regenerate
              </button>
              <button className="btn btn-sm">
                <Icon name="edit" /> Edit markdown
              </button>
              <button className="btn btn-sm btn-primary">
                <Icon name="check" /> Approve & publish
              </button>
            </div>
          </footer>
        </article>
      </div>
    </>
  );
}
