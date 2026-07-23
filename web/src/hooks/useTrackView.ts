import { useEffect } from "react";
import { api, type ViewEntityType } from "../lib/api";

// Session-level dedupe: one beacon per (type, id) per app load. The server
// adds its own 30-minute window on top, so StrictMode double-mounts and
// route revisits can't inflate counts.
const seen = new Set<string>();

/** Fire the read-analytics beacon once when a detail page mounts. */
export function useTrackView(entityType: ViewEntityType, id: number | undefined) {
  useEffect(() => {
    if (!id || Number.isNaN(id)) return;
    const key = `${entityType}:${id}`;
    if (seen.has(key)) return;
    seen.add(key);
    api.trackView(entityType, id);
  }, [entityType, id]);
}
