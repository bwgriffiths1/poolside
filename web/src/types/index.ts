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
  unassigned_doc_count?: number;
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
  manual?: boolean;        // user-attached material (vs scraped)
}

export interface Attachment {
  id: number;
  meeting_id: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
  note?: string | null;
  uploaded_by?: string | null;
  created_at?: string | null;
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
  summary_version?: number | null;
  summary_status?: string | null;
  summary_updated_at?: string | null;
  summary_is_manual?: boolean;
  initiative_codes?: string[];
}

export interface MeetingDetail extends MeetingListItem {
  one_line: string;
  agenda: AgendaItem[];
}

// Briefing block types (typed AST for renderer)
export type BriefingBlock =
  | { kind: "p"; text: string }
  // Numbered sub-headings ("7.a — …") anchor their own materials.
  | { kind: "h"; text: string; item_id?: string; docs?: BriefingDoc[] }
  | { kind: "callout"; label: string; text: string }
  | { kind: "data"; title: string; rows: string[][] };

export interface BriefingDoc {
  id: number;
  filename: string;
  type: string;
  source_url?: string | null;
  ceii?: boolean;
  item_id: string;
  item: string;
}

export interface BriefingSection {
  id: string;
  kind: "agenda" | "rollup";
  item_id: string;
  // 0 = top-level agenda item (group header); 1 = sub-item nested beneath it.
  depth?: number;
  title: string;
  vote?: string;
  body: BriefingBlock[];
  next_steps?: string[];
  // Materials filed under this agenda item, distributed by the API.
  docs?: BriefingDoc[];
}

export interface Briefing {
  title: string;
  subtitle: string;
  headline: string;
  generated_at: string;
  model: string;
  word_count: number;
  reading_time: number;
  prev_meeting_id?: number | null;
  next_meeting_id?: number | null;
  tldr: string[];
  executive_summary?: BriefingBlock[];
  sections: BriefingSection[];
  // Documents belonging to no section — meeting-level files, or items the
  // briefing didn't write up.
  other_docs?: BriefingDoc[];
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

export type RoundupStatus = "draft" | "generating" | "complete" | "error";

export interface RoundupSource {
  meeting_id: number;
  type_short: string;
  type_name: string;
  meeting_date: string;
  end_date?: string | null;
  title: string;
}

export interface Roundup {
  id: number;
  venue: string;
  month: string; // "YYYY-MM"
  month_label: string; // "June 2026"
  status: RoundupStatus;
  model_id?: string | null;
  report_md?: string | null;
  error_message?: string | null;
  progress_text?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_usd?: number | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  sources: RoundupSource[];
}

export interface RoundupMonth {
  month: string;
  month_label: string;
  briefing_count: number;
  committees: string[];
  roundup: Roundup | null;
}
