// Domain types — mirror the shape the API will return (derived from pipeline/db_new.py rows)

export type MeetingStatus = "scheduled" | "materials" | "summarized" | "updated";

export type LifecycleStatus =
  | "discovered"
  | "agenda_posted"
  | "materials_posted"
  | "summarized"
  | "approved";

export interface MeetingListItem {
  id: number;
  venue: string;           // short_name e.g. "ISO-NE"
  type_short: string;      // e.g. "MC"
  type_name: string;       // e.g. "Markets Committee"
  title: string;
  meeting_date: string;    // ISO date
  end_date?: string;
  location: string;
  external_id: string;
  status: MeetingStatus;
  lifecycle_status?: LifecycleStatus;
  last_scraped_at?: string;
  agenda_parsed_at?: string;
  doc_count: number;
  item_count: number;
  tags: string[];
}

export interface DocumentRef {
  id: number;
  filename: string;
  type: string;            // pdf / pptx / xlsx / docx
  assigned: boolean;
  ceii?: boolean;
  source_url?: string;
}

export interface AgendaItem {
  id: number;
  item_id: string;         // outline id e.g. "3", "3.1"
  depth: number;
  title: string;
  presenter?: string;
  org?: string;
  time_slot?: string;
  vote_status?: string | null;
  has_summary: boolean;
  wmpp_id?: string;
  docs: DocumentRef[];
  one_line?: string;
  detailed?: string;
}

export interface MeetingDetail extends MeetingListItem {
  one_line: string;
  agenda: AgendaItem[];
}

// Briefing block types (typed AST for renderer)
export type BriefingBlock =
  | { kind: "p"; text: string }
  | { kind: "h"; text: string }
  | { kind: "callout"; label: string; text: string }
  | { kind: "data"; title: string; rows: string[][] };

export interface BriefingSection {
  id: string;
  kind: "agenda" | "rollup";
  item_id: string;
  title: string;
  vote?: string;
  body: BriefingBlock[];
  next_steps?: string[];
}

export interface Briefing {
  title: string;
  subtitle: string;
  headline: string;
  generated_at: string;
  model: string;
  word_count: number;
  reading_time: number;
  tldr: string[];
  sections: BriefingSection[];
}

export interface IngestJob {
  id: string;
  meeting_id: number;
  status: "running" | "complete" | "failed";
  started: string;
  finished?: string;
  label: string;
  docs: number;
  agenda_items: number;
}

export interface CurrentUser {
  name: string;
  email: string;
  initials: string;
}
