"""Pydantic schemas — frontend contract.

Shapes mirror web/src/types/index.ts. Keep them in sync.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel

MeetingStatus = Literal["scheduled", "materials", "summarized", "updated"]
LifecycleStatus = Literal[
    "discovered", "agenda_posted", "materials_posted", "summarized", "approved"
]


class CurrentUser(BaseModel):
    id: int
    name: str
    email: str
    initials: str
    role: Literal["admin", "editor", "viewer"] = "viewer"


class MeetingListItem(BaseModel):
    id: int
    venue: str
    type_short: str
    type_name: str
    title: str
    meeting_date: str
    end_date: Optional[str] = None
    location: str
    external_id: str
    status: MeetingStatus
    lifecycle_status: LifecycleStatus = "discovered"
    last_scraped_at: Optional[str] = None
    agenda_parsed_at: Optional[str] = None
    doc_count: int
    unassigned_doc_count: int = 0
    item_count: int
    tags: list[str]


class DocumentRef(BaseModel):
    id: int
    filename: str
    type: str
    assigned: bool
    ceii: bool = False
    source_url: Optional[str] = None
    manual: bool = False


class AgendaItem(BaseModel):
    id: int
    item_id: str
    depth: int
    title: str
    presenter: Optional[str] = None
    org: Optional[str] = None
    time_slot: Optional[str] = None
    vote_status: Optional[str] = None
    has_summary: bool
    wmpp_id: Optional[str] = None
    docs: list[DocumentRef]
    one_line: Optional[str] = ""
    detailed: Optional[str] = ""
    summary_version: Optional[int] = None
    summary_status: Optional[str] = None
    summary_updated_at: Optional[str] = None
    summary_is_manual: bool = False
    initiative_codes: list[str] = []


class MeetingDetail(MeetingListItem):
    one_line: str = ""
    agenda: list[AgendaItem]


class BriefingDoc(BaseModel):
    """A source document as shown in the briefing, carrying the agenda item
    it was filed under so unmatched docs can still identify themselves."""
    id: int
    filename: str
    type: str
    source_url: Optional[str] = None
    ceii: bool = False
    item_id: str = ""
    item: str = ""


class BriefingBlockP(BaseModel):
    kind: Literal["p"]
    text: str


class BriefingBlockH(BaseModel):
    kind: Literal["h"]
    text: str
    # Sub-item heading inside a section ("7.a — Resource Qualification").
    # Numbered sub-headings anchor their own materials; set by
    # adapters.attach_briefing_docs, empty for prose sub-heads.
    item_id: str = ""
    docs: list[BriefingDoc] = []


class BriefingBlockCallout(BaseModel):
    kind: Literal["callout"]
    label: str
    text: str


class BriefingBlockData(BaseModel):
    kind: Literal["data"]
    title: str
    rows: list[list[str]]


BriefingBlock = BriefingBlockP | BriefingBlockH | BriefingBlockCallout | BriefingBlockData


class BriefingSection(BaseModel):
    id: str
    kind: Literal["agenda", "rollup"]
    item_id: str
    # 0 = top-level agenda item (## n — Title); 1 = sub-item (### n.x — Title).
    # Depth-0 sections that carry no body of their own act as group headers.
    depth: int = 0
    title: str
    vote: Optional[str] = None
    body: list[BriefingBlock]
    next_steps: Optional[list[str]] = None
    # Materials filed under this agenda item, attached by adapters.attach_briefing_docs.
    docs: list[BriefingDoc] = []


class Briefing(BaseModel):
    title: str
    subtitle: str
    headline: str
    generated_at: str
    model: str
    word_count: int
    reading_time: int
    # Chronological neighbors that also have briefings (reader prev/next nav).
    prev_meeting_id: int | None = None
    next_meeting_id: int | None = None
    tldr: list[str]
    # Executive Summary prose (Key Developments / Critical Decisions / etc.),
    # parsed as blocks. Empty for briefings that have no exec-summary section.
    executive_summary: list[BriefingBlock] = []
    sections: list[BriefingSection]
    # Documents that map to no section — meeting-level files, or items the
    # briefing didn't write up. Listed once at the end so nothing is lost.
    other_docs: list[BriefingDoc] = []


RoundupStatus = Literal["draft", "generating", "complete", "error"]


class RoundupSource(BaseModel):
    meeting_id: int
    type_short: str
    type_name: str
    meeting_date: str
    end_date: Optional[str] = None
    title: str = ""


class Roundup(BaseModel):
    id: int
    venue: str
    month: str          # "YYYY-MM"
    month_label: str    # "June 2026"
    status: RoundupStatus
    model_id: Optional[str] = None
    report_md: Optional[str] = None
    error_message: Optional[str] = None
    progress_text: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    created_by: Optional[str] = None
    created_at: str
    updated_at: str
    sources: list[RoundupSource] = []


class RoundupMonth(BaseModel):
    """One month row on the Roundups page: what briefings exist, and the
    roundup row (report body stripped) if one has been generated."""
    month: str
    month_label: str
    briefing_count: int
    committees: list[str]
    roundup: Optional[Roundup] = None


class IngestJob(BaseModel):
    id: str
    meeting_id: int
    status: Literal["running", "complete", "failed"]
    started: str
    finished: Optional[str] = None
    label: str
    docs: int
    agenda_items: int
