import type { ReactNode } from "react";

interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
}

interface SegmentedProps<T extends string> {
  value: T;
  onChange: (v: T) => void;
  options: SegmentedOption<T>[];
  style?: React.CSSProperties;
}

export function Segmented<T extends string>({ value, onChange, options, style }: SegmentedProps<T>) {
  return (
    <div className="seg" style={style}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={value === o.value ? "on" : ""}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
