import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { Tag } from "../components/Tag";
import {
  api,
  type PromptIndex,
  type PromptMeta,
  type VenueCommitteePrompts,
} from "../lib/api";

type Tab = "shared" | "venues" | "pipeline" | "models";
type VenueSubTab = "briefing" | "agenda_item";

export function Prompts() {
  const [tab, setTab] = useState<Tab>("shared");

  const { data: index, isLoading } = useQuery({
    queryKey: ["prompt-index"],
    queryFn: () => api.prompts(),
  });

  return (
    <>
      <Topbar
        crumbs={[{ label: "Prompt Library" }]}
        actions={null}
      />
      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Pipeline · Prompts</div>
          <h1 className="page-title">Prompt library</h1>
          <p className="page-subtitle">
            Edit the markdown prompts the summarization pipeline uses. Changes
            take effect immediately on the next run — no restart needed.
          </p>
        </div>

        <Segmented
          value={tab}
          onChange={setTab}
          options={[
            { value: "shared", label: "Shared" },
            { value: "venues", label: "Venue prompts" },
            { value: "pipeline", label: "Pipeline" },
            { value: "models", label: "Models" },
          ]}
          style={{ marginBottom: 24 }}
        />

        {isLoading || !index ? (
          <div className="muted">Loading prompt index…</div>
        ) : tab === "shared" ? (
          <SharedTab prompts={index.shared} />
        ) : tab === "venues" ? (
          <VenuesTab index={index} />
        ) : tab === "pipeline" ? (
          <PipelineTab prompts={index.pipeline} extras={index.extras} />
        ) : (
          <ModelsTab />
        )}

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}

// ─── Shared tab ────────────────────────────────────────────────────────────

function SharedTab({ prompts }: { prompts: PromptMeta[] }) {
  const [slug, setSlug] = useState<string>(prompts[0]?.slug ?? "");
  const active = prompts.find((p) => p.slug === slug) ?? prompts[0];
  return (
    <div className="prompt-layout">
      <PromptList prompts={prompts} active={slug} onSelect={setSlug} />
      {active && <PromptEditor meta={active} />}
    </div>
  );
}

// ─── Pipeline tab ──────────────────────────────────────────────────────────

function PipelineTab({
  prompts,
  extras,
}: {
  prompts: PromptMeta[];
  extras: PromptMeta[];
}) {
  const all = [...prompts, ...extras];
  const [slug, setSlug] = useState<string>(all[0]?.slug ?? "");
  const active = all.find((p) => p.slug === slug) ?? all[0];
  return (
    <div className="prompt-layout">
      <PromptList prompts={all} active={slug} onSelect={setSlug} />
      {active && <PromptEditor meta={active} />}
    </div>
  );
}

// ─── Venues tab ────────────────────────────────────────────────────────────

function VenuesTab({ index }: { index: PromptIndex }) {
  const [venueSlug, setVenueSlug] = useState<string>(
    index.venues[0]?.venue_short ?? ""
  );
  const [sub, setSub] = useState<VenueSubTab>("briefing");
  const [commSlug, setCommSlug] = useState<string>("");

  const venue = index.venues.find((v) => v.venue_short === venueSlug);
  // Reset committee when venue changes
  useEffect(() => {
    if (venue && (!commSlug || !venue.committees.find((c) => c.short_name === commSlug))) {
      setCommSlug(venue.committees[0]?.short_name ?? "");
    }
  }, [venue, commSlug]);

  if (!venue) return <div className="muted">No venues configured.</div>;

  const committee = venue.committees.find((c) => c.short_name === commSlug) ?? venue.committees[0];
  const meta: PromptMeta | undefined =
    sub === "briefing" ? committee?.briefing : committee?.agenda_item;

  return (
    <>
      <div className="row" style={{ gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <Segmented
          value={venueSlug}
          onChange={setVenueSlug}
          options={index.venues.map((v) => ({
            value: v.venue_short,
            label: v.venue_short,
          }))}
        />
        <Segmented
          value={sub}
          onChange={setSub}
          options={[
            { value: "briefing", label: "Briefing" },
            { value: "agenda_item", label: "Agenda item" },
          ]}
        />
      </div>

      <div className="prompt-layout">
        <CommitteeList
          committees={venue.committees}
          sub={sub}
          active={commSlug}
          onSelect={setCommSlug}
        />
        {meta && committee && (
          <PromptEditor
            meta={meta}
            heading={`${venue.venue_short} · ${committee.short_name} — ${
              sub === "briefing" ? "Briefing" : "Agenda item"
            }`}
          />
        )}
      </div>
    </>
  );
}

// ─── List components ───────────────────────────────────────────────────────

function PromptList({
  prompts,
  active,
  onSelect,
}: {
  prompts: PromptMeta[];
  active: string;
  onSelect: (s: string) => void;
}) {
  return (
    <nav className="prompt-list">
      {prompts.map((p) => (
        <button
          key={p.slug}
          className={`prompt-list-item ${active === p.slug ? "on" : ""}`}
          onClick={() => onSelect(p.slug)}
        >
          <div className="prompt-list-label">{p.label || p.slug}</div>
          <div className="prompt-list-meta">
            <code>{p.slug}</code>
            {!p.exists && <span className="badge-missing">missing</span>}
            {p.exists && (
              <span className="muted text-xs">{(p.size / 1024).toFixed(1)} kB</span>
            )}
          </div>
          {p.hint && <div className="prompt-list-hint">{p.hint}</div>}
        </button>
      ))}
    </nav>
  );
}

function CommitteeList({
  committees,
  sub,
  active,
  onSelect,
}: {
  committees: VenueCommitteePrompts[];
  sub: VenueSubTab;
  active: string;
  onSelect: (s: string) => void;
}) {
  return (
    <nav className="prompt-list">
      {committees.map((c) => {
        const meta = sub === "briefing" ? c.briefing : c.agenda_item;
        return (
          <button
            key={c.short_name}
            className={`prompt-list-item ${active === c.short_name ? "on" : ""}`}
            onClick={() => onSelect(c.short_name)}
          >
            <div className="prompt-list-label">{c.short_name}</div>
            <div className="prompt-list-meta">
              <span className="muted text-xs">{c.name}</span>
            </div>
            <div className="prompt-list-meta" style={{ marginTop: 4 }}>
              {meta.exists ? (
                <span className="muted text-xs">{(meta.size / 1024).toFixed(1)} kB</span>
              ) : (
                <span className="badge-missing">missing</span>
              )}
            </div>
          </button>
        );
      })}
    </nav>
  );
}

// ─── Editor ────────────────────────────────────────────────────────────────

function PromptEditor({
  meta,
  heading,
}: {
  meta: PromptMeta;
  heading?: string;
}) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["prompt", meta.slug],
    queryFn: () => api.prompt(meta.slug),
  });

  const [content, setContent] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    setContent(data?.content ?? "");
    setDirty(false);
  }, [data?.content, meta.slug]);

  const save = useMutation({
    mutationFn: () => api.savePrompt(meta.slug, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prompt", meta.slug] });
      qc.invalidateQueries({ queryKey: ["prompt-index"] });
      setDirty(false);
    },
    onError: (err: Error) => alert(`Save failed: ${err.message}`),
  });

  return (
    <section className="prompt-editor">
      <div className="prompt-editor-head">
        <div>
          <div className="page-eyebrow" style={{ marginBottom: 4 }}>
            {heading || meta.label || "Prompt"}
          </div>
          <div className="row" style={{ gap: 8 }}>
            <code className="prompt-slug">{meta.slug}.md</code>
            {!data?.exists && <Tag>new — will be created on save</Tag>}
            {data?.exists && data.modified && (
              <span className="muted text-xs">
                modified {new Date(data.modified).toLocaleString()}
              </span>
            )}
          </div>
          {meta.hint && (
            <div className="muted text-sm" style={{ marginTop: 6, maxWidth: 65 + "ch" }}>
              {meta.hint}
            </div>
          )}
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button
            className="btn btn-sm"
            disabled={!dirty}
            onClick={() => {
              setContent(data?.content ?? "");
              setDirty(false);
            }}
          >
            Revert
          </button>
          <button
            className="btn btn-sm btn-accent"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            <Icon name="check" size={12} />{" "}
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="muted">Loading prompt…</div>
      ) : (
        <textarea
          className="prompt-textarea"
          value={content}
          onChange={(e) => {
            setContent(e.target.value);
            setDirty(true);
          }}
          spellCheck={false}
        />
      )}
    </section>
  );
}

// ─── Models tab ────────────────────────────────────────────────────────────

const MODEL_OPTIONS = [
  { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5 (fast, cheap)" },
  { value: "claude-sonnet-4-6", label: "Sonnet 4.6 (balanced)" },
  { value: "claude-opus-4-6", label: "Opus 4.6 (most capable)" },
  { value: "claude-opus-4-7", label: "Opus 4.7 (latest)" },
];

function ModelsTab() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["model-config"],
    queryFn: () => api.modelConfig(),
  });
  const [draft, setDraft] = useState({
    document_model: "",
    item_model: "",
    meeting_model: "",
  });
  useEffect(() => {
    if (data) {
      setDraft({
        document_model: data.document_model,
        item_model: data.item_model,
        meeting_model: data.meeting_model,
      });
    }
  }, [data]);

  const save = useMutation({
    mutationFn: () => api.saveModelConfig(draft),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["model-config"] }),
    onError: (err: Error) => alert(`Save failed: ${err.message}`),
  });

  if (!data) return <div className="muted">Loading model config…</div>;

  return (
    <div className="card">
      <div className="page-eyebrow" style={{ marginBottom: 4 }}>
        Summarization models
      </div>
      <p className="muted text-sm" style={{ marginTop: 0, marginBottom: 16, maxWidth: "65ch" }}>
        Choose which model runs at each level of the summarization pipeline.
        Haiku is recommended while testing — it's the fastest and cheapest.
      </p>

      <div className="row" style={{ gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
        <ModelLevel
          label="Level 1 — Document group"
          hint="Summarises all docs at each agenda item"
          value={draft.document_model}
          onChange={(v) => setDraft({ ...draft, document_model: v })}
        />
        <ModelLevel
          label="Level 2 — Item rollup"
          hint="Synthesises child items into parent summaries"
          value={draft.item_model}
          onChange={(v) => setDraft({ ...draft, item_model: v })}
        />
        <ModelLevel
          label="Level 3 — Meeting briefing"
          hint="Generates the full meeting briefing"
          value={draft.meeting_model}
          onChange={(v) => setDraft({ ...draft, meeting_model: v })}
        />
      </div>

      <hr className="divider" style={{ margin: "16px 0" }} />

      <div className="row" style={{ gap: 8 }}>
        <button
          className="btn btn-sm btn-accent"
          disabled={save.isPending}
          onClick={() => save.mutate()}
        >
          <Icon name="check" size={12} />{" "}
          {save.isPending ? "Saving…" : "Save model config"}
        </button>
        {save.isSuccess && <span className="text-xs muted">✓ saved</span>}
      </div>
    </div>
  );
}

function ModelLevel({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div style={{ flex: "1 1 240px", minWidth: 240 }}>
      <label className="field-label">{label}</label>
      <select
        className="select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ marginBottom: 4 }}
      >
        {MODEL_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
        {!MODEL_OPTIONS.find((o) => o.value === value) && (
          <option value={value}>{value}</option>
        )}
      </select>
      <div className="muted text-xs" style={{ lineHeight: 1.4 }}>
        {hint}
      </div>
    </div>
  );
}
