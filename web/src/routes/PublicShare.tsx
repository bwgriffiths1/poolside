import { useRef } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Icon } from "../components/Icon";
import { VenueTag, TypeTag } from "../components/Tag";
import { BlockRenderer } from "../components/briefing/BlockRenderer";
import { useScrollSpy } from "../hooks/useScrollSpy";
import { api } from "../lib/api";
import { inlineMd } from "../lib/markdown";
import type { Briefing as BriefingType } from "../types";

/**
 * Public, read-only briefing view. No auth required — backed by the
 * /api/public/share/:token endpoint. Renders the same content as the
 * authenticated Briefing reader (TOC, sections, decisions, sources) but
 * with no edit / approve / share controls and no sidebar.
 */
export function PublicShare() {
  const { token } = useParams<{ token: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["public-share", token],
    queryFn: () => api.publicShare(token as string),
    enabled: !!token,
    retry: false,
  });

  const refs = useRef<Record<string, HTMLElement | null>>({});
  const sectionIds = data?.briefing
    ? ["top", ...data.briefing.sections.map((s) => s.id), "sources"]
    : ["top"];
  const active = useScrollSpy(sectionIds, refs, "top");

  const jump = (target: string) => {
    const el = refs.current[target];
    if (!el) return;
    window.scrollTo({ top: el.offsetTop - 32, behavior: "smooth" });
  };

  if (isLoading) {
    return (
      <div className="public-share-shell">
        <div className="muted">Loading briefing…</div>
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
          </header>

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

          {b.sections.map((s) => (
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
                  {s.vote && <div className="b-section-vote">{s.vote}</div>}
                </div>
              </div>
              <div className="b-section-body">
                {s.body.map((blk, i) => (
                  <BlockRenderer key={i} block={blk} />
                ))}
              </div>
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
          ))}

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
