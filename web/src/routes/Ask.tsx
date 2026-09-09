import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { TypeTag } from "../components/Tag";
import {
  api,
  type AskCorpus,
  type AskDepth,
  type AskEffort,
  type AskResponse,
  type AskScope,
  type AskSource,
  type DocketListItem,
} from "../lib/api";
import { qk } from "../lib/queries";
import { Markdown } from "../lib/markdown";
import { toast } from "../lib/toast";
import {
  AT_CARET_RE,
  linkCitations,
  mentionedDockets,
  sourceHref,
} from "../lib/ask";

interface AskEntry extends AskResponse {
  ts: number;
}

const HISTORY_KEY = "poolside-ask-history";
const PREFS_KEY = "poolside-ask-prefs";

interface AskPrefs {
  model?: string;
  effort?: AskEffort;
}

function loadPrefs(): AskPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function savePrefs(prefs: AskPrefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage blocked — the pick still applies this session */
  }
}

function loadHistory(): AskEntry[] {
  try {
    const raw = sessionStorage.getItem(HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveHistory(entries: AskEntry[]) {
  try {
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, 20)));
  } catch {
    /* quota — history is a nicety */
  }
}

function SourceRow({ s }: { s: AskSource }) {
  const navigate = useNavigate();
  const target = sourceHref(s).slice(1); // drop the hash-router "#"
  const isDocket = s.docket_id != null;
  const excerpt = s.tier === "document";

  let title: string;
  if (s.entity_type === "docket") {
    title = "State of play";
  } else if (isDocket) {
    const bits = [s.accession_number, s.document_class].filter(Boolean);
    title = bits.join(" · ") || "Filing";
    if (excerpt && s.filename) title += ` — ${s.filename}`;
    else if (s.description) title += ` — ${s.description}`;
  } else if (excerpt) {
    title = s.filename || "Document";
    if (s.item_id) title = `${s.item_id} · ${title}`;
  } else if (s.item_id) {
    title = `${s.item_id} — ${s.item_title || "Untitled item"}`;
  } else {
    title = "Meeting briefing";
  }

  return (
    <button className="ask-source" onClick={() => navigate(target)}>
      <span className="ask-source-n mono">{s.n}</span>
      <div className="ask-source-main">
        <div className="row" style={{ gap: 6 }}>
          <span className="mono text-xs muted">
            {isDocket ? s.filed_date || "" : s.meeting_date}
          </span>
          {isDocket ? (
            <span className="tag ask-docket-tag mono">{s.docket_number}</span>
          ) : (
            <TypeTag>{s.type_short ?? "?"}</TypeTag>
          )}
          {excerpt && <span className="ask-tier-tag">excerpt</span>}
          <span className="ask-source-title" title={title}>
            {title}
          </span>
        </div>
        {s.snippet && (
          <div
            className="ask-source-snippet"
            // Snippet is escaped server-side; <b> tags are the highlights.
            dangerouslySetInnerHTML={{ __html: s.snippet }}
          />
        )}
      </div>
      <Icon name="arrow-r" size={12} />
    </button>
  );
}

function scopeLabel(scope: AskScope | undefined): string | null {
  if (!scope) return null;
  const bits: string[] = [];
  if (scope.dockets.length) {
    bits.push(scope.dockets.map((d) => d.docket_number).join(", "));
  } else if (scope.corpus === "dockets") {
    bits.push("all tracked dockets");
  } else if (scope.corpus === "meetings") {
    bits.push("meetings only");
  }
  if (scope.depth === "documents") bits.push("summaries + documents");
  if (scope.unknown_dockets.length) {
    bits.push(`not tracked: ${scope.unknown_dockets.join(", ")}`);
  }
  return bits.length ? bits.join(" · ") : null;
}

function AnswerCard({ entry }: { entry: AskEntry }) {
  const [showSources, setShowSources] = useState(true);
  const meta: string[] = [];
  if (entry.model_id) meta.push(entry.model_id);
  if (entry.effort) meta.push(`${entry.effort} effort`);
  if (entry.cost_usd != null) meta.push(`$${entry.cost_usd.toFixed(3)}`);
  const scope = scopeLabel(entry.scope);

  return (
    <div className="ask-card">
      <div className="ask-q">
        <Icon name="chat" size={14} />
        <span>{entry.question}</span>
      </div>
      {scope && (
        <div className="ask-scope-line">
          <Icon name="filter" size={11} />
          {scope}
        </div>
      )}
      <article className="ask-answer">
        <Markdown source={linkCitations(entry.answer_md, entry.sources)} />
      </article>
      {entry.sources.length > 0 && (
        <div className="ask-sources">
          <button
            type="button"
            className="ask-sources-toggle"
            onClick={() => setShowSources(!showSources)}
          >
            <Icon name={showSources ? "chev-d" : "chev-r"} size={12} />
            {entry.sources.length} source{entry.sources.length === 1 ? "" : "s"}
            {meta.length > 0 && (
              <span className="muted"> · {meta.join(" · ")}</span>
            )}
          </button>
          {showSources && (
            <div className="ask-source-list">
              {entry.sources.map((s) => (
                <SourceRow key={s.n} s={s} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Mention autocomplete ────────────────────────────────────────────────

interface MentionState {
  /** Text typed after the "@", for filtering. */
  frag: string;
  /** Caret index where the "@" token starts. */
  start: number;
}

function mentionAtCaret(text: string, caret: number): MentionState | null {
  const before = text.slice(0, caret);
  const m = AT_CARET_RE.exec(before);
  if (!m) return null;
  return { frag: m[2], start: before.length - m[2].length - 1 };
}

function filterDockets(dockets: DocketListItem[], frag: string): DocketListItem[] {
  const f = frag.toLowerCase();
  return dockets
    .filter(
      (d) =>
        !f ||
        d.docket_number.toLowerCase().includes(f) ||
        (d.title || "").toLowerCase().includes(f) ||
        (d.party_label || "").toLowerCase().includes(f),
    )
    .slice(0, 8);
}

export function Ask() {
  const [params, setParams] = useSearchParams();
  const [question, setQuestion] = useState("");
  const [corpus, setCorpus] = useState<AskCorpus>("all");
  const [depth, setDepth] = useState<AskDepth>("summaries");
  // "" = the server's default (model_config.json / DEFAULT_EFFORT).
  const [prefs, setPrefs] = useState<AskPrefs>(loadPrefs);
  const [history, setHistory] = useState<AskEntry[]>(loadHistory);
  const [mention, setMention] = useState<MentionState | null>(null);
  const [menuIdx, setMenuIdx] = useState(0);
  // Caret position to restore after a programmatic edit of the textarea
  // value (React resets the caret to the end when it re-renders the value,
  // so this has to run in an effect, after the commit).
  const pendingCaret = useRef<number | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const autoRan = useRef(false);

  useEffect(() => {
    const pos = pendingCaret.current;
    if (pos === null) return;
    pendingCaret.current = null;
    const el = inputRef.current;
    if (el) {
      el.focus();
      el.setSelectionRange(pos, pos);
    }
  }, [question]);

  const { data: dockets = [] } = useQuery({
    queryKey: qk.dockets,
    queryFn: api.dockets,
    staleTime: 60_000,
  });
  const { data: options } = useQuery({
    queryKey: ["ask-options"],
    queryFn: api.askOptions,
    staleTime: Infinity,
  });

  const modelId = prefs.model || options?.default_model || "";
  const modelOpt = options?.models.find((m) => m.id === modelId);
  const effortSupported = modelOpt ? modelOpt.effort : true;
  const effort = prefs.effort || options?.default_effort || "low";
  const updatePrefs = (patch: AskPrefs) => {
    const next = { ...prefs, ...patch };
    setPrefs(next);
    savePrefs(next);
  };

  const mentioned = mentionedDockets(question);
  const byNumber = new Map(dockets.map((d) => [d.docket_number, d]));
  const knownMentions = mentioned.filter((n) => byNumber.has(n));
  const unknownMentions = mentioned.filter((n) => !byNumber.has(n));
  const menuItems = mention ? filterDockets(dockets, mention.frag) : [];
  const menuOpen = mention !== null && menuItems.length > 0;

  const askMut = useMutation({
    mutationFn: (q: string) =>
      api.ask({
        question: q,
        corpus,
        depth,
        model: prefs.model || undefined,
        effort: effortSupported ? prefs.effort || undefined : undefined,
      }),
    onSuccess: (res) => {
      setHistory((prev) => {
        const next = [{ ...res, ts: Date.now() }, ...prev];
        saveHistory(next);
        return next;
      });
      setQuestion("");
      setMention(null);
    },
    onError: (e: Error) => toast.error(`Ask failed: ${e.message}`),
  });

  const submit = (q?: string) => {
    const text = (q ?? question).trim();
    if (text.length < 3 || askMut.isPending) return;
    askMut.mutate(text);
  };

  // Command palette hands off via /ask?q=… — run it once, then clean the URL.
  useEffect(() => {
    const q = params.get("q");
    if (q && !autoRan.current) {
      autoRan.current = true;
      setQuestion(q);
      submit(q);
      setParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const syncMention = (text: string, caret: number) => {
    const m = mentionAtCaret(text, caret);
    setMention(m);
    if (!m || m.frag !== mention?.frag) setMenuIdx(0);
  };

  const pickDocket = (d: DocketListItem) => {
    if (!mention) return;
    const el = inputRef.current;
    const caret = el ? el.selectionStart : question.length;
    const before = question.slice(0, mention.start);
    const after = question.slice(caret);
    const inserted = `@${d.docket_number} `;
    setQuestion(before + inserted + after);
    setMention(null);
    pendingCaret.current = before.length + inserted.length;
  };

  const insertAt = () => {
    const el = inputRef.current;
    const caret = el ? el.selectionStart : question.length;
    const before = question.slice(0, caret);
    const needsSpace = before.length > 0 && !/\s$/.test(before);
    const inserted = (needsSpace ? " " : "") + "@";
    setQuestion(before + inserted + question.slice(caret));
    const pos = before.length + inserted.length;
    setMention({ frag: "", start: pos - 1 });
    setMenuIdx(0);
    pendingCaret.current = pos;
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMenuIdx((i) => (i + 1) % menuItems.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMenuIdx((i) => (i - 1 + menuItems.length) % menuItems.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        pickDocket(menuItems[menuIdx] ?? menuItems[0]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMention(null);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const clearHistory = () => {
    setHistory([]);
    saveHistory([]);
  };

  const scoped = mentioned.length > 0;

  return (
    <>
      <Topbar
        crumbs={[{ label: "Ask" }]}
        actions={
          history.length > 0 && (
            <button className="btn btn-ghost btn-sm" onClick={clearHistory}>
              <Icon name="trash" size={12} /> Clear
            </button>
          )
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Cited Q&amp;A · meetings + FERC dockets</div>
          <h1 className="page-title">Ask Poolside</h1>
          <p className="page-subtitle">
            Ask across every briefing, item summary and tracked docket —
            answers cite their sources, and each citation links back. Type{" "}
            <span className="mono">@</span> to scope a question to one or
            more dockets; switch to <em>Documents</em> to search the
            underlying filings and materials too.
          </p>
        </div>

        <div className="ask-input-card">
          <textarea
            ref={inputRef}
            className="ask-input"
            rows={2}
            placeholder={
              scoped
                ? 'e.g. "What did the protesters object to, and how did the order respond?"'
                : 'e.g. "Where does CAR-SA stand?" or "@ER26-925 what did NEPGA argue?"'
            }
            value={question}
            onChange={(e) => {
              setQuestion(e.target.value);
              syncMention(e.target.value, e.target.selectionStart);
            }}
            onKeyUp={(e) => {
              // Caret moves without a value change (arrows, clicks) still
              // decide whether we're inside an @token.
              if (e.key.startsWith("Arrow") || e.key === "Home" || e.key === "End") {
                syncMention(question, e.currentTarget.selectionStart);
              }
            }}
            onClick={(e) => syncMention(question, e.currentTarget.selectionStart)}
            onKeyDown={onKeyDown}
            onBlur={() => window.setTimeout(() => setMention(null), 120)}
          />

          {menuOpen && (
            <div className="ask-mention-menu" role="listbox">
              {menuItems.map((d, i) => (
                <button
                  key={d.id}
                  type="button"
                  role="option"
                  aria-selected={i === menuIdx}
                  className={`ask-mention-item${i === menuIdx ? " on" : ""}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseEnter={() => setMenuIdx(i)}
                  onClick={() => pickDocket(d)}
                >
                  <span className="mono ask-mention-num">{d.docket_number}</span>
                  <span className="ask-mention-title">
                    {d.title || d.party_label || "Untitled docket"}
                  </span>
                  {d.filing_count != null && (
                    <span className="muted text-xs">
                      {d.filing_count} filing{d.filing_count === 1 ? "" : "s"}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
          {mention && !menuOpen && dockets.length > 0 && mention.frag && (
            <div className="ask-mention-menu ask-mention-empty">
              No tracked docket matches “{mention.frag}” — add it on the
              eLibrary page first.
            </div>
          )}

          <div className="ask-input-foot">
            <div className="ask-controls">
              <button
                type="button"
                className="btn btn-ghost btn-sm ask-at-btn"
                title="Scope to a docket"
                onClick={insertAt}
              >
                <span className="mono">@</span> Docket
              </button>

              {scoped ? (
                <div className="ask-chips">
                  {knownMentions.map((n) => {
                    const d = byNumber.get(n)!;
                    return (
                      <span key={n} className="ask-chip" title={d.title || undefined}>
                        <span className="mono">{n}</span>
                        {d.title && (
                          <span className="ask-chip-title">{d.title}</span>
                        )}
                      </span>
                    );
                  })}
                  {unknownMentions.map((n) => (
                    <span key={n} className="ask-chip ask-chip-warn" title="Not a tracked docket">
                      <span className="mono">{n}</span>
                      <span className="ask-chip-title">not tracked</span>
                    </span>
                  ))}
                </div>
              ) : (
                <div className="seg" role="radiogroup" aria-label="Corpus">
                  {(
                    [
                      ["all", "All"],
                      ["meetings", "Meetings"],
                      ["dockets", "Dockets"],
                    ] as [AskCorpus, string][]
                  ).map(([v, label]) => (
                    <button
                      key={v}
                      type="button"
                      role="radio"
                      aria-checked={corpus === v}
                      className={corpus === v ? "on" : ""}
                      onClick={() => setCorpus(v)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}

              <div className="seg" role="radiogroup" aria-label="Depth">
                <button
                  type="button"
                  role="radio"
                  aria-checked={depth === "summaries"}
                  className={depth === "summaries" ? "on" : ""}
                  title="Search stored summaries only (fast, cheapest)"
                  onClick={() => setDepth("summaries")}
                >
                  Summaries
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={depth === "documents"}
                  className={depth === "documents" ? "on" : ""}
                  title="Also search the underlying filings and meeting materials, quoting verbatim passages"
                  onClick={() => setDepth("documents")}
                >
                  <Icon name="doc" size={11} /> Documents
                </button>
              </div>

              {options && (
                <div className="ask-model-controls">
                  <select
                    className="select select-sm"
                    aria-label="Model"
                    title={modelOpt?.note}
                    value={modelId}
                    onChange={(e) =>
                      updatePrefs({
                        model:
                          e.target.value === options.default_model
                            ? undefined
                            : e.target.value,
                      })
                    }
                  >
                    {options.models.map((m) => (
                      <option key={m.id} value={m.id} title={m.note}>
                        {m.label}
                        {m.id === options.default_model ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                  <select
                    className="select select-sm"
                    aria-label="Effort"
                    title={
                      effortSupported
                        ? "How hard the model thinks before answering — higher is slower and costs more"
                        : `${modelOpt?.label ?? "This model"} has no effort control`
                    }
                    value={effortSupported ? effort : ""}
                    disabled={!effortSupported}
                    onChange={(e) =>
                      updatePrefs({
                        effort:
                          e.target.value === options.default_effort
                            ? undefined
                            : (e.target.value as AskEffort),
                      })
                    }
                  >
                    {!effortSupported && <option value="">n/a</option>}
                    {options.efforts.map((lvl) => (
                      <option key={lvl} value={lvl}>
                        {lvl} effort
                        {lvl === options.default_effort ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
            <button
              className="btn btn-primary btn-sm"
              disabled={question.trim().length < 3 || askMut.isPending}
              onClick={() => submit()}
            >
              <Icon name="spark" size={12} />
              {askMut.isPending ? "Thinking…" : "Ask"}
            </button>
          </div>
        </div>

        {askMut.isPending && (
          <div className="ask-pending">
            <Icon name="refresh" size={14} />
            {depth === "documents"
              ? "Searching summaries and the underlying documents, then composing a cited answer…"
              : "Searching the corpus and composing a cited answer…"}
          </div>
        )}

        {history.length === 0 && !askMut.isPending && (
          <div className="empty" style={{ marginTop: 24 }}>
            Nothing asked yet this session.
          </div>
        )}

        <div className="ask-history">
          {history.map((entry) => (
            <AnswerCard key={entry.ts} entry={entry} />
          ))}
        </div>

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
