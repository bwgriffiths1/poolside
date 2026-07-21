"""Venue-hosted URLs for a meeting.

Mirrors web/src/lib/links.ts — keep the two in sync. Only ISO-NE has these
URLs today; every helper returns None for other venues so callers can render
conditionally without knowing the venue's conventions.
"""
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
    if venue_short != "ISO-NE" or not external_id:
        return None
    return f"https://www.iso-ne.com/event-details?eventId={quote(str(external_id))}"
