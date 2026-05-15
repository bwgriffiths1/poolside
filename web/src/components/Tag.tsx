import type { ReactNode } from "react";

export function Tag({ children }: { children: ReactNode }) {
  return <span className="tag">{children}</span>;
}

export function VenueTag({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <span className="venue-tag" style={style}>{children}</span>;
}

export function TypeTag({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <span className="type-tag" style={style}>{children}</span>;
}
