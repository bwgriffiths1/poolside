import { Icon } from "../Icon";
import { ISO_NE_WEBEX_URL, isoMaterialsUrl } from "../../lib/links";

interface MeetingLinksProps {
  venue: string;
  externalId?: string | null;
  className?: string;
}

/**
 * Venue-hosted links for a meeting — join it virtually, or open the source
 * materials. Rendered in the meeting header and directly under the briefing
 * header. Only ISO-NE meetings have these URLs, so it renders nothing for
 * any other venue.
 */
export function MeetingLinks({
  venue,
  externalId,
  className,
}: MeetingLinksProps) {
  if (venue !== "ISO-NE") return null;
  const materials = isoMaterialsUrl(venue, externalId);

  return (
    <div className={`meeting-links${className ? ` ${className}` : ""}`}>
      <a
        className="meeting-link meeting-link-webex"
        href={ISO_NE_WEBEX_URL}
        target="_blank"
        rel="noopener noreferrer"
        title="Open the ISO-NE Webex site to join this meeting virtually"
      >
        <Icon name="video" size={13} /> Join on Webex
        <Icon name="external" size={11} />
      </a>
      {materials && (
        <a
          className="meeting-link meeting-link-materials"
          href={materials}
          target="_blank"
          rel="noopener noreferrer"
          title="Open this meeting's page on iso-ne.com"
        >
          <Icon name="paperclip" size={13} /> Meeting materials on ISO-NE
          <Icon name="external" size={11} />
        </a>
      )}
    </div>
  );
}
