from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Provider(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    label: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=600)
    model: str = Field(min_length=1, max_length=160)
    api_key: str = Field(default="", max_length=3000)
    max_output_tokens: int = Field(default=8192, ge=512, le=32768)
    context_tokens: int = Field(default=64000, ge=4096, le=2000000)
    temperature: float | None = Field(default=0.8, ge=0, le=2)
    timeout_seconds: int = Field(default=240, ge=10, le=900)


class SettingsInput(Strict):
    providers: list[Provider] = Field(default_factory=list, max_length=12)
    roles: dict[str, str] = Field(default_factory=dict)


class ProjectInput(Strict):
    title: str = Field(default="未命名新书", min_length=1, max_length=100)
    brief: str = Field(default="研究参考材料中的抽象写作方法，创作独立的新小说。", max_length=5000)
    genre: str = Field(default="自动选择", max_length=100)
    urls: list[str] = Field(default_factory=list, max_length=10)
    chapter_count: int = Field(default=20, ge=1, le=1000)
    chapter_chars: int = Field(default=2000, ge=300, le=6000)
    token_budget: int = Field(default=2000000, ge=1000, le=2000000000)
    max_revisions: int = Field(default=2, ge=0, le=8)
    quality_threshold: int = Field(default=75, ge=0, le=100)
    polish: bool = True
    humanizer: bool = True
    style: str = Field(default="自然、具体、人物说话有区别；允许留白，不做章末说教，不强行套用固定句式。", max_length=4000)
    max_pages: int = Field(default=15, ge=1, le=100)
    max_source_chars: int = Field(default=200000, ge=1000, le=2000000)
    sample_chars_per_source: int = Field(default=30000, ge=1000, le=200000)
    demo: bool = False

    @field_validator("urls")
    @classmethod
    def urls_clean(cls, value):
        return list(dict.fromkeys(s.strip() for s in value if s.strip()))


class BudgetInput(Strict):
    token_budget: int = Field(ge=1000, le=2000000000)
    max_revisions: int = Field(ge=0, le=8)


class SourceInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=200, max_length=2000000)


# Model outputs use explicit schemas; a refusal, empty body or prose-only response is not success.
class Output(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Mechanism(Output):
    name: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=1500)
    evidence: str = Field(min_length=1, max_length=1000)
    transferable: str = Field(min_length=1, max_length=1000)


class Analysis(Output):
    summary: str = Field(min_length=1, max_length=3500)
    mechanisms: list[Mechanism] = Field(min_length=1, max_length=12)
    uncertainties: list[str] = Field(default_factory=list, max_length=15)


class Synthesis(Output):
    audience: str = Field(min_length=1, max_length=1500)
    methods: list[str] = Field(min_length=1, max_length=20)
    avoid_copying: list[str] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class Concept(Output):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,20}$")
    title: str = Field(min_length=1, max_length=100)
    premise: str = Field(min_length=30, max_length=2500)
    difference: str = Field(min_length=10, max_length=2000)
    conflict: str = Field(min_length=10, max_length=2000)
    ending: str = Field(min_length=5, max_length=1000)


class Concepts(Output):
    candidates: list[Concept] = Field(min_length=3, max_length=3)


class Rating(Output):
    id: str
    score: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=1000)


class Selection(Output):
    ratings: list[Rating] = Field(min_length=3, max_length=3)


class Character(Output):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    goal: str = Field(min_length=1, max_length=500)
    voice: str = Field(min_length=1, max_length=500)
    constraints: str = Field(min_length=1, max_length=1000)


class Arc(Output):
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    goal: str = Field(min_length=1, max_length=1000)
    turning_point: str = Field(min_length=1, max_length=1000)


class Bible(Output):
    title: str = Field(min_length=1, max_length=100)
    premise: str = Field(min_length=1, max_length=2000)
    rules: list[str] = Field(min_length=1, max_length=20)
    characters: list[Character] = Field(min_length=1, max_length=15)
    arcs: list[Arc] = Field(min_length=1, max_length=20)
    ending: str = Field(min_length=1, max_length=1500)
    style_guide: str = Field(min_length=1, max_length=1500)


class ChapterPlan(Output):
    title: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=1000)
    scenes: list[str] = Field(min_length=1, max_length=10)
    must_preserve: list[str] = Field(default_factory=list, max_length=20)
    ending_state: str = Field(min_length=1, max_length=1500)


class Draft(Output):
    title: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=100, max_length=80000)


class Issue(Output):
    severity: Literal["minor", "major", "critical"]
    category: str = Field(min_length=1, max_length=100)
    detail: str = Field(min_length=1, max_length=1500)


class Review(Output):
    score: int = Field(ge=0, le=100)
    issues: list[Issue] = Field(default_factory=list, max_length=30)
    repair_brief: str = Field(default="", max_length=5000)


class Fidelity(Output):
    preserved: bool
    issues: list[str] = Field(default_factory=list, max_length=20)


class Fact(Output):
    key: str = Field(min_length=1, max_length=100)
    kind: Literal["character", "world", "timeline", "thread", "relationship"]
    content: str = Field(min_length=1, max_length=1200)
    closed: bool = False


class Memory(Output):
    summary: str = Field(min_length=1, max_length=1600)
    updates: list[Fact] = Field(default_factory=list, max_length=30)


class BookAudit(Output):
    summary: str = Field(min_length=1, max_length=2500)
    issues: list[Issue] = Field(default_factory=list, max_length=40)


class CrawlChoice(Output):
    ids: list[int] = Field(default_factory=list, max_length=6)
    reason: str = Field(min_length=1, max_length=1500)
