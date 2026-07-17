"""Pydantic models for retrieval output."""

from typing import Optional, Union
from pydantic import BaseModel


class Citation(BaseModel):
    series: str
    part: str
    lesson: Union[int, str]
    speaker: str
    date: str
    file_path: str


class SearchResult(BaseModel):
    child_chunk_id: str
    parent_chunk_id: str
    child_chunk_index: Optional[int] = None
    source_tag: Optional[str] = None
    collection: Optional[str] = None
    book_slug: Optional[str] = None
    lesson_code: Optional[str] = None
    node_path: Optional[str] = None
    section_path: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    is_front_matter: bool = False
    is_primary: bool = True
    context_only: bool = False
    parent_text: str
    child_text: Optional[str] = None
    score: float
    rerank_score: Optional[float] = None
    section_title: Optional[str] = None
    citation_ref: Optional[str] = None
    citation: Citation
