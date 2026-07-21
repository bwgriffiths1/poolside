// External (venue-hosted) links for a meeting.

/**
 * ISO-NE runs every committee meeting off one Webex site, so this is a
 * permalink rather than a per-event join URL — it lands on the site's
 * meeting list, where the day's session is picked.
 */
export const ISO_NE_WEBEX_URL =
  "https://iso-newengland.webex.com/webappng/sites/iso-newengland/meeting/home";

/** ISO-NE's own event page for a meeting — the agenda + posted materials. */
export function isoMaterialsUrl(
  venue: string,
  externalId?: string | null,
): string | null {
  if (venue !== "ISO-NE" || !externalId) return null;
  return `https://www.iso-ne.com/event-details?eventId=${encodeURIComponent(externalId)}`;
}
