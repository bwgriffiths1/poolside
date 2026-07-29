"""Venue-hosted URLs for a meeting.

Mirrors web/src/lib/links.ts — keep the two in sync. (PJM is deliberately
absent from the TS mirror while it's hidden from the main UI; the /pjm demo
page links pjm.com itself.) Helpers return None when a venue has no such
URL so callers can render conditionally without knowing venue conventions.
"""
import re
from urllib.parse import quote

# ISO-NE runs every committee meeting off one Webex site, so this is a
# permalink rather than a per-event join URL.
ISO_NE_WEBEX_URL = (
    "https://iso-newengland.webex.com/webappng/sites/iso-newengland/meeting/home"
)


def webex_url(venue_short: str | None) -> str | None:
    """Virtual-attendance link for a meeting at this venue."""
    return ISO_NE_WEBEX_URL if venue_short == "ISO-NE" else None


def materials_url(venue_short: str | None, external_id: str | None) -> str | None:
    """The venue's own event page — agenda plus posted materials."""
    if not external_id:
        return None
    if venue_short == "ISO-NE":
        return f"https://www.iso-ne.com/event-details?eventId={quote(str(external_id))}"
    if venue_short == "PJM":
        # external_id is pjm-{committee-slug}-{yyyymmdd}; materials live on
        # the committee page (one accordion entry per meeting date).
        m = re.fullmatch(r"pjm-(.+)-\d{8}", str(external_id))
        if m:
            return f"https://www.pjm.com/committees-and-groups/{m.group(1)}"
    return None
