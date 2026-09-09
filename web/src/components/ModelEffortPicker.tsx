import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { AskDetail, AskEffort, AskOptions } from "../lib/api";

const EFFORT_LABEL: Record<AskEffort, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Very high",
  max: "Max",
};

const EFFORT_HINT: Record<AskEffort, string> = {
  low: "Quick read of the sources. Fine for lookups.",
  medium: "A little more deliberation.",
  high: "Weighs conflicting sources carefully.",
  xhigh: "Slow, thorough; for synthesis across many sources.",
  max: "Slowest and most expensive; correctness over speed.",
};

const DETAIL_LABEL: Record<AskDetail, string> = {
  brief: "Brief",
  standard: "Standard",
  deep: "Deep",
};

const DETAIL_HINT: Record<AskDetail, string> = {
  brief: "A direct answer in a paragraph or two (≤400 words).",
  standard:
    "A structured answer with the supporting detail: dates, positions, figures (500–800 words).",
  deep:
    "A full analyst memo with sections. On a docket it reads every filing, not just the best matches; pair with Documents for verbatim language. Slower and costs more.",
};

/** Compact trigger ("Sonnet 5 · Low · Standard") that opens a popover with
 *  a model list, a Faster ↔ Smarter effort slider and a detail level.
 *  Controlled: the parent owns the picks and their persistence. */
export function ModelEffortPicker({
  options,
  modelId,
  effort,
  detail,
  onChange,
}: {
  options: AskOptions;
  modelId: string;
  effort: AskEffort;
  detail: AskDetail;
  onChange: (patch: {
    model?: string;
    effort?: AskEffort;
    detail?: AskDetail;
  }) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const model = options.models.find((m) => m.id === modelId) ?? options.models[0];
  const effortSupported = model?.effort ?? true;
  const levels = options.efforts;
  const idx = Math.max(0, levels.indexOf(effort));

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pct = levels.length > 1 ? (idx / (levels.length - 1)) * 100 : 0;

  return (
    <div className="mep" ref={rootRef}>
      <button
        type="button"
        className={`mep-trigger${open ? " on" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Model, effort and detail"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="mep-trigger-model">{model?.label ?? modelId}</span>
        <span className="mep-trigger-sep">·</span>
        <span className="mep-trigger-effort">
          {effortSupported ? EFFORT_LABEL[effort] : "n/a"}
        </span>
        <span className="mep-trigger-sep">·</span>
        <span className="mep-trigger-detail">{DETAIL_LABEL[detail]}</span>
        <Icon name="chev-d" size={10} />
      </button>

      {open && (
        <div className="mep-pop" role="dialog" aria-label="Model, effort and detail">
          <div className="mep-section">
            <div className="mep-head">
              <span className="mep-label">Model</span>
              <span className="mep-value">{model?.label}</span>
            </div>
            <div className="mep-models" role="radiogroup" aria-label="Model">
              {options.models.map((m) => {
                const selected = m.id === modelId;
                return (
                  <button
                    key={m.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    className={`mep-model${selected ? " on" : ""}`}
                    onClick={() =>
                      onChange({
                        model: m.id === options.default_model ? undefined : m.id,
                      })
                    }
                  >
                    <span className="mep-model-check">
                      {selected && <Icon name="check" size={11} />}
                    </span>
                    <span className="mep-model-main">
                      <span className="mep-model-name">
                        {m.label}
                        {m.id === options.default_model && (
                          <span className="mep-default">default</span>
                        )}
                      </span>
                      <span className="mep-model-note">{m.note}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className={`mep-section${effortSupported ? "" : " mep-disabled"}`}>
            <div className="mep-head">
              <span className="mep-label">Effort</span>
              <span className="mep-value">
                {effortSupported ? EFFORT_LABEL[effort] : "Not available"}
              </span>
            </div>
            <div className="mep-ends" aria-hidden="true">
              <span>Faster</span>
              <span>Smarter</span>
            </div>
            <div className="mep-track" style={{ "--mep-fill": `${pct}%` } as React.CSSProperties}>
              <div className="mep-dots" aria-hidden="true">
                {levels.map((lvl) => (
                  <span key={lvl} className="mep-dot" />
                ))}
              </div>
              <input
                type="range"
                className="mep-range"
                min={0}
                max={levels.length - 1}
                step={1}
                value={idx}
                disabled={!effortSupported}
                aria-label="Effort"
                aria-valuetext={EFFORT_LABEL[effort]}
                onChange={(e) => {
                  const lvl = levels[Number(e.target.value)];
                  onChange({
                    effort: lvl === options.default_effort ? undefined : lvl,
                  });
                }}
              />
            </div>
            <div className="mep-hint">
              {effortSupported
                ? EFFORT_HINT[effort] +
                  (detail === "deep" && effort === options.default_effort
                    ? " Deep raises this to Medium unless you choose otherwise."
                    : "")
                : `${model?.label} takes no effort setting.`}
            </div>
          </div>

          <div className="mep-section">
            <div className="mep-head">
              <span className="mep-label">Detail</span>
              <span className="mep-value">{DETAIL_LABEL[detail]}</span>
            </div>
            <div className="seg mep-seg" role="radiogroup" aria-label="Detail">
              {options.details.map((lvl) => (
                <button
                  key={lvl}
                  type="button"
                  role="radio"
                  aria-checked={detail === lvl}
                  className={detail === lvl ? "on" : ""}
                  onClick={() =>
                    onChange({
                      detail: lvl === options.default_detail ? undefined : lvl,
                    })
                  }
                >
                  {DETAIL_LABEL[lvl]}
                </button>
              ))}
            </div>
            <div className="mep-hint">{DETAIL_HINT[detail]}</div>
          </div>
        </div>
      )}
    </div>
  );
}
