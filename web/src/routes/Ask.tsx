import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { TypeTag } from "../components/Tag";
import { ModelEffortPicker } from "../components/ModelEffortPicker";
import {
  api,
  type AskCorpus,
  type AskDepth,
  type AskDetail,
  type AskEffort,
  type AskHistoryItem,
  type AskHistoryPage,
  type AskJob,
  type AskJobStatus,
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

/** A live answer or an ask_log row — same shape, keyed by id when the
 *  server logged it and by a client timestamp when it couldn't. */
interface AskEntry extends AskResponse {
  key: string;
  created_at?: string | null;
  user_email?: string;
}

const PREFS_KEY = "poolside-ask-prefs";

interface AskPrefs {
  model?: string;
  effort?: AskEffort;
  detail?: AskDetail;
}

function entryFromHistory(item: AskHistoryItem): AskEntry {
  return { ...item, key: `log-${item.id}` };
}


/** Ask runs as a server-side job: submit → 202 → poll. A long memo then
 *  survives the browser/edge request timeout, and a reload can recover an
 *  in-flight job from /api/ask/jobs/active. */
const JOB_POLL_MS = 3000;

function isTerminal(status: AskJobStatus | undefined): boolean {
  return status === "complete" || status === "failed" || status === "cancelled";
}

/** The exchange a follow-up builds on: id + the question, for the chip. */
interface FollowUp {
  id: number;
  question: string;
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

  const firstPage = s.pages && s.pages.length ? s.pages[0] : null;
  const pdfHref =
    excerpt && s.file_row_id != null
      ? `/api/dockets/files/${s.file_row_id}/download${firstPage ? `#page=${firstPage}` : ""}`
      : null;

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
          {pdfHref && (
            <a
              className="ask-source-pdf mono text-xs"
              href={pdfHref}
              target="_blank"
              rel="noreferrer"
              title={firstPage ? `Open the PDF at page ${firstPage}` : "Open the PDF"}
              onClick={(e) => e.stopPropagation()}
            >
              <Icon name="external" size={10} />
              {firstPage ? `p. ${s.pages!.join(", ")}` : "PDF"}
            </a>
          )}
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

function OmittedNote({ scope }: { scope: AskScope | undefined }) {
  const omitted = scope?.omitted_sources ?? [];
  if (!omitted.length) return null;
  return (
    <div
      className="ask-omitted"
      title={omitted.join("\n")}
    >
      <Icon name="filter" size={11} />
      {omitted.length} source{omitted.length === 1 ? "" : "s"} didn't fit the
      prompt budget and were named to the model as omitted — narrow the
      question to cover them.
    </div>
  );
}

function AnswerCard({
  entry,
  parentQuestion,
  onFollowUp,
}: {
  entry: AskEntry;
  /** The question this entry followed up on, when known. */
  parentQuestion?: string | null;
  onFollowUp?: (entry: AskEntry) => void;
}) {
  const [showSources, setShowSources] = useState(true);
  const meta: string[] = [];
  if (entry.model_id) meta.push(entry.model_id);
  if (entry.effort) meta.push(`${entry.effort} effort`);
  if (entry.detail) meta.push(`${entry.detail} detail`);
  if (entry.cost_usd != null) meta.push(`$${entry.cost_usd.toFixed(3)}`);
  const scope = scopeLabel(entry.scope);
  const when = entry.created_at ? new Date(entry.created_at) : null;

  return (
    <div className="ask-card">
      {entry.parent_id != null && (
        <div className="ask-followup-of muted text-xs" title={parentQuestion ?? undefined}>
          ↳ follow-up to{" "}
          <em>{parentQuestion ? parentQuestion : `an earlier question`}</em>
        </div>
      )}
      <div className="ask-q">
        <Icon name="chat" size={14} />
        <span>{entry.question}</span>
        {when && !Number.isNaN(when.getTime()) && (
          <span className="ask-q-when mono text-xs muted">
            {when.toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "numeric",
              minute: "2-digit",
            })}
          </span>
        )}
      </div>
      {scope && (
        <div className="ask-scope-line">
          <Icon name="filter" size={11} />
          {scope}
        </div>
      )}
      <OmittedNote scope={entry.scope} />
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
          {entry.id != null && (
            <span className="ask-card-actions">
              <a
                className="btn btn-ghost btn-sm"
                href={`/api/ask/${entry.id}/docx`}
                title="Download this answer as a Word memo with a sources list"
              >
                <Icon name="download" size={12} /> Word
              </a>
              {onFollowUp && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  title="Ask a follow-up over these same sources"
                  onClick={() => onFollowUp(entry)}
                >
                  <Icon name="chat" size={12} /> Follow up
                </button>
              )}
            </span>
          )}
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
  // "Clear" hides everything at or before this log id (or all live-only
  // entries) for the rest of the session; the server keeps the log.
  const [clearedAt, setClearedAt] = useState<number | null>(null);
  const [liveOnly, setLiveOnly] = useState<AskEntry[]>([]);
  const [jobId, setJobId] = useState<number | null>(null);
  const [followUp, setFollowUp] = useState<FollowUp | null>(null);
  // Jobs whose terminal state has been handled (prepend + toast) once:
  // the ref guards the poll callback; the state keeps render ref-free.
  const settledRef = useRef<Set<number>>(new Set());
  const [settledIds, setSettledIds] = useState<number[]>([]);
  const qc = useQueryClient();
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
    queryKey: qk.askOptions,
    queryFn: api.askOptions,
    staleTime: Infinity,
  });
  const { data: historyPage } = useQuery({
    queryKey: qk.askHistory,
    queryFn: () => api.askHistory(20),
    staleTime: 30_000,
  });
  const detail = prefs.detail || options?.default_detail || "standard";

  // Recover an in-flight job after a reload (fetched once per mount).
  const { data: activeJobs } = useQuery({
    queryKey: qk.askJobsActive,
    queryFn: api.activeAskJobs,
    staleTime: Infinity,
  });
  const recoveredId =
    activeJobs?.find((j) => !isTerminal(j.status) && !settledIds.includes(j.id))?.id ?? null;
  const effectiveJobId = jobId ?? recoveredId;

  const settle = (job: AskJob) => {
    if (settledRef.current.has(job.id)) return;
    settledRef.current.add(job.id);
    setSettledIds((prev) => [...prev, job.id]);
    if (job.status === "complete" && job.result) {
      const item = job.result;
      qc.setQueryData<AskHistoryPage>(qk.askHistory, (prev) => ({
        items: [item, ...(prev?.items ?? []).filter((it) => it.id !== item.id)].slice(0, 50),
        next_before_id: prev?.next_before_id ?? null,
      }));
      qc.invalidateQueries({ queryKey: qk.askHistory });
    } else if (job.status === "failed") {
      toast.error(`Ask failed: ${job.error || "unknown error"}`, 12_000);
    } else if (job.status === "cancelled") {
      toast.info("Ask cancelled.");
    }
    setJobId(null);
  };

  const { data: job } = useQuery({
    queryKey: qk.askJob(effectiveJobId),
    queryFn: () => api.getAskJob(effectiveJobId as number),
    enabled: effectiveJobId != null,
    refetchInterval: (q) => {
      const data = q.state.data as AskJob | undefined;
      if (data && isTerminal(data.status)) {
        settle(data); // runs outside render
        return false;
      }
      return JOB_POLL_MS;
    },
    refetchIntervalInBackground: true,
  });
  const jobActive = effectiveJobId != null && !(job && isTerminal(job.status));

  const modelId = prefs.model || options?.default_model || "";
  const modelOpt = options?.models.find((m) => m.id === modelId);
  const effortSupported = modelOpt ? modelOpt.effort : true;
  const effort = prefs.effort || options?.default_effort || "low";
  const updatePrefs = (patch: AskPrefs) => {
    const next = { ...prefs, ...patch };
    setPrefs(next);
    savePrefs(next);
    // A deep memo wants verbatim language; flip Documents on when the
    // analyst picks Deep (they can flip it back — it's a visible control).
    if (patch.detail === "deep") setDepth("documents");
  };

  const mentioned = mentionedDockets(question);
  const byNumber = new Map(dockets.map((d) => [d.docket_number, d]));
  const knownMentions = mentioned.filter((n) => byNumber.has(n));
  const unknownMentions = mentioned.filter((n) => !byNumber.has(n));
  const menuItems = mention ? filterDockets(dockets, mention.frag) : [];
  const menuOpen = mention !== null && menuItems.length > 0;

  const askMut = useMutation({
    mutationFn: (q: string) =>
      api.startAskJob({
        question: q,
        corpus,
        depth,
        model: prefs.model || undefined,
        effort: effortSupported ? prefs.effort || undefined : undefined,
        detail: prefs.detail || undefined,
        parent_id: followUp?.id,
      }),
    onSuccess: (res) => {
      setJobId(res.job_id);
      setQuestion("");
      setMention(null);
      setFollowUp(null);
    },
    onError: (e: Error) => toast.error(`Ask failed: ${e.message}`),
  });

  const cancelMut = useMutation({
    mutationFn: (id: number) => api.cancelAskJob(id),
    onError: (e: Error) => toast.error(`Could not cancel: ${e.message}`),
  });

  const submit = (q?: string) => {
    const text = (q ?? question).trim();
    if (text.length < 3 || askMut.isPending || jobActive) return;
    askMut.mutate(text);
  };

  const startFollowUp = (entry: AskEntry) => {
    if (entry.id == null) return;
    setFollowUp({ id: entry.id, question: entry.question });
    const el = inputRef.current;
    if (el) {
      el.focus();
      const main = document.querySelector(".main") as HTMLElement | null;
      main?.scrollTo({ top: 0, behavior: "smooth" });
    }
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

  const logged = (historyPage?.items ?? [])
    .filter((it) => clearedAt === null || it.id > clearedAt)
    .map(entryFromHistory);
  const history: AskEntry[] = [...liveOnly, ...logged].sort((a, b) =>
    (b.created_at ?? "") < (a.created_at ?? "") ? -1 : 1,
  );

  const clearHistory = () => {
    const newest = historyPage?.items?.[0]?.id ?? null;
    setClearedAt(newest ?? Number.MAX_SAFE_INTEGER);
    setLiveOnly([]);
  };
  const questionById = new Map(
    (historyPage?.items ?? []).map((it) => [it.id, it.question] as const),
  );
  const busy = askMut.isPending || jobActive;

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
            underlying filings and materials too. Your questions are kept
            here across sessions.
          </p>
        </div>

        <div className="ask-input-card">
          {followUp && (
            <div className="ask-followup-chip">
              <Icon name="chat" size={12} />
              <span>
                Following up on: <em>{followUp.question}</em>
              </span>
              <button
                type="button"
                className="ask-followup-x"
                aria-label="Cancel follow-up"
                title="Ask a fresh question instead"
                onClick={() => setFollowUp(null)}
              >
                <Icon name="x" size={11} />
              </button>
            </div>
          )}
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
                <ModelEffortPicker
                  options={options}
                  modelId={modelId}
                  effort={effort}
                  detail={detail}
                  onChange={updatePrefs}
                />
              )}
            </div>
            <button
              className="btn btn-primary btn-sm"
              disabled={question.trim().length < 3 || busy}
              onClick={() => submit()}
            >
              <Icon name="spark" size={12} />
              {busy ? "Thinking…" : followUp ? "Follow up" : "Ask"}
            </button>
          </div>
        </div>

        {busy && (
          <div className="ask-pending">
            <Icon name="refresh" size={14} />
            <span className="ask-pending-text">
              {job?.progress_text
                ? job.progress_text
                : detail === "deep"
                  ? "Pulling every relevant source and composing a full memo — a few minutes at higher effort…"
                  : depth === "documents"
                    ? "Searching summaries and the underlying documents, then composing a cited answer…"
                    : "Searching the corpus and composing a cited answer…"}
              {job?.request?.question && (
                <span className="muted"> · {job.request.question}</span>
              )}
            </span>
            {effectiveJobId != null && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={cancelMut.isPending || job?.status === "cancelling"}
                onClick={() => cancelMut.mutate(effectiveJobId)}
              >
                {job?.status === "cancelling" ? "Cancelling…" : "Cancel"}
              </button>
            )}
          </div>
        )}

        {history.length === 0 && !busy && (
          <div className="empty" style={{ marginTop: 24 }}>
            Nothing asked yet.
          </div>
        )}

        <div className="ask-history">
          {history.map((entry) => (
            <AnswerCard
              key={entry.key}
              entry={entry}
              parentQuestion={
                entry.parent_id != null ? questionById.get(entry.parent_id) ?? null : null
              }
              onFollowUp={startFollowUp}
            />
          ))}
        </div>

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
