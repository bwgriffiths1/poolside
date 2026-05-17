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
    name: str
    email: str
    initials: str


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


class MeetingDetail(MeetingListItem):
    one_line: str = ""
    agenda: list[AgendaItem]


class BriefingBlockP(BaseModel):
    kind: Literal["p"]
    text: str


class BriefingBlockH(BaseModel):
    kind: Literal["h"]
    text: str


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
    title: str
    vote: Optional[str] = None
    body: list[BriefingBlock]
    next_steps: Optional[list[str]] = None


class Briefing(BaseModel):
    title: str
    subtitle: str
    headline: str
    generated_at: str
    model: str
    word_count: int
    reading_time: int
    tldr: list[str]
    sections: list[BriefingSection]


class IngestJob(BaseModel):
    id: str
    meeting_id: int
    status: Literal["running", "complete", "failed"]
    started: str
    finished: Optional[str] = None
    label: str
    docs: int
    agenda_items: int
