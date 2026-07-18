/** Stable DOM id derived from an agenda item_id, e.g. "7.a" → "item-7-a".
 *  Used both by AgendaRow (to tag rows) and Meeting (to deep-link scroll). */
export function idForAnchor(itemId: string | null | undefined): string {
  return `item-${(itemId || "").replace(/[^a-zA-Z0-9_-]/g, "-") || "0"}`;
}
