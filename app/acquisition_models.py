"""Validated, bounded settings for book acquisition (old page mode remains available)."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AcquisitionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["pages", "books"] = "pages"
    queries: list[str] = Field(default_factory=list, max_length=5)
    book_count: int = Field(default=3, ge=1, le=10)
    max_candidates: int = Field(default=12, ge=1, le=40)
    max_discovery_pages: int = Field(default=20, ge=1, le=100)
    max_toc_pages: int = Field(default=30, ge=1, le=100)
    max_chapters: int = Field(default=3000, ge=1, le=10000)
    max_chapter_pages: int = Field(default=5, ge=1, le=20)
    max_book_chars: int = Field(default=8000000, ge=1000, le=30000000)
    max_total_chars: int = Field(default=20000000, ge=1000, le=100000000)
    min_chapter_chars: int = Field(default=120, ge=20, le=1000)
    delay_seconds: float = Field(default=1.0, ge=0.2, le=30)
    retries: int = Field(default=2, ge=0, le=5)
    backend: Literal["auto", "native", "lncrawl"] = "auto"
    allow_partial: bool = False
    analysis: Literal["sample", "full"] = "sample"
    acquire_only: bool = False

    @field_validator("queries")
    @classmethod
    def clean_queries(cls, values):
        result = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if any(len(v) > 300 for v in result):
            raise ValueError("单条搜索词不能超过300字符")
        return result
