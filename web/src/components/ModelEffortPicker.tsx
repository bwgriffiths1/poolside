import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { AskEffort, AskOptions } from "../lib/api";

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

/** Compact trigger ("Sonnet 5 · Low") that opens a popover with a model
 *  list and a Faster ↔ Smarter effort slider. Controlled: the parent owns
 *  the picks and their persistence. */
export function ModelEffortPicker({
  options,
  modelId,
  effort,
  onChange,
}: {
  options: AskOptions;
  modelId: string;
  effort: AskEffort;
  onChange: (patch: { model?: string; effort?: AskEffort }) => void;
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
        title="Model and effort"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="mep-trigger-model">{model?.label ?? modelId}</span>
        <span className="mep-trigger-sep">·</span>
        <span className="mep-trigger-effort">
          {effortSupported ? EFFORT_LABEL[effort] : "n/a"}
        </span>
        <Icon name="chev-d" size={10} />
      </button>

      {open && (
        <div className="mep-pop" role="dialog" aria-label="Model and effort">
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
                ? EFFORT_HINT[effort]
                : `${model?.label} takes no effort setting.`}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
