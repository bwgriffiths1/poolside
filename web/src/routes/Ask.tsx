import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { TypeTag } from "../components/Tag";
import { api, type AskResponse, type AskSource } from "../lib/api";
import { Markdown } from "../lib/markdown";
import { toast } from "../lib/toast";

interface AskEntry extends AskResponse {
  ts: number;
}

const HISTORY_KEY = "poolside-ask-history";

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

/** Turn bare [n] citation markers into internal links so the markdown
 *  renderer emits clickable citations. Meeting-level sources land on the
 *  briefing reader; item-level sources deep-link the meeting page's item. */
export function linkCitations(md: string, sources: AskSource[]): string {
  if (!md) return md;
  const byN = new Map(sources.map((s) => [s.n, s]));
  return md.replace(/\[(\d+)\](?!\()/g, (match, num) => {
    const s = byN.get(Number(num));
    if (!s) return match;
    const href = s.item_id
      ? `#/meeting/${s.meeting_id}?item=${encodeURIComponent(s.item_id)}`
      : `#/briefing/${s.meeting_id}`;
    return `[${num}](${href})`;
  });
}

function SourceRow({ s }: { s: AskSource }) {
  const navigate = useNavigate();
  const target = s.item_id
    ? `/meeting/${s.meeting_id}?item=${encodeURIComponent(s.item_id)}`
    : `/briefing/${s.meeting_id}`;
  return (
    <button className="ask-source" onClick={() => navigate(target)}>
      <span className="ask-source-n mono">{s.n}</span>
      <div className="ask-source-main">
        <div className="row" style={{ gap: 6 }}>
          <span className="mono text-xs muted">{s.meeting_date}</span>
          <TypeTag>{s.type_short}</TypeTag>
          <span className="ask-source-title">
            {s.item_id
              ? `${s.item_id} — ${s.item_title || "Untitled item"}`
              : "Meeting briefing"}
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

function AnswerCard({ entry }: { entry: AskEntry }) {
  const [showSources, setShowSources] = useState(true);
  const meta: string[] = [];
  if (entry.model_id) meta.push(entry.model_id);
  if (entry.cost_usd != null) meta.push(`$${entry.cost_usd.toFixed(3)}`);

  return (
    <div className="ask-card">
      <div className="ask-q">
        <Icon name="chat" size={14} />
        <span>{entry.question}</span>
      </div>
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

export function Ask() {
  const [params, setParams] = useSearchParams();
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<AskEntry[]>(loadHistory);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const autoRan = useRef(false);

  const askMut = useMutation({
    mutationFn: (q: string) => api.ask({ question: q }),
    onSuccess: (res) => {
      setHistory((prev) => {
        const next = [{ ...res, ts: Date.now() }, ...prev];
        saveHistory(next);
        return next;
      });
      setQuestion("");
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

  const clearHistory = () => {
    setHistory([]);
    saveHistory([]);
  };

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
          <div className="page-eyebrow">Cited Q&amp;A · summary corpus</div>
          <h1 className="page-title">Ask Poolside</h1>
          <p className="page-subtitle">
            Ask across every briefing and item summary — answers cite their
            sources, and each citation links to the underlying meeting.
          </p>
        </div>

        <div className="ask-input-card">
          <textarea
            ref={inputRef}
            className="ask-input"
            rows={2}
            placeholder='e.g. "Where does CAR-SA stand and what are the open objections?"'
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <div className="ask-input-foot">
            <span className="muted text-xs">
              Enter to ask · answers come from stored summaries only
            </span>
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
            Searching the corpus and composing a cited answer…
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
