from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MaterialListItem(BaseModel):
    id: str
    title: str | None
    source_type: str
    source_url: str | None
    summary: str | None
    main_idea: str | None
    category: str | None
    tags: list[str]
    usefulness_score: int | None
    processing_status: str
    processing_stage: str | None
    created_at: datetime | None

    model_config = {"from_attributes": True}


class MaterialDetail(MaterialListItem):
    raw_text: str | None = None
    key_ideas: list[Any] = Field(default_factory=list)
    practical_takeaways: list[Any] = Field(default_factory=list)
    tools: list[Any] = Field(default_factory=list)
    technologies: list[Any] = Field(default_factory=list)
    links: list[Any] = Field(default_factory=list)
    code_blocks: list[Any] = Field(default_factory=list)
    usefulness_reason: str | None = None
    error_message: str | None = None
    duration_seconds: float | None = None
    has_audio: bool = False
    usage_tokens_in: int = 0
    usage_tokens_out: int = 0
    usage_audio_seconds: float = 0
    digest: str | None = None
    detailed_explanation: str | None = None
    objective_evaluation: dict[str, Any] | None = None
    ai_opinion: str | None = None
    ai_opinion_sources: list[Any] = Field(default_factory=list)
    ai_opinion_at: datetime | None = None
    key_moments: list[Any] = Field(default_factory=list)


class MaterialListResponse(BaseModel):
    items: list[MaterialListItem]
    total: int


class NoteCreate(BaseModel):
    text: str = Field(..., min_length=1)


class UrlCreate(BaseModel):
    url: str = Field(..., min_length=8)


class ScoreUpdate(BaseModel):
    score: int = Field(..., ge=1, le=10)
    reason: str | None = None


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class ComposerRequest(BaseModel):
    text: str = Field(..., min_length=1)


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=12000)
    voice: str = Field(default="svetlana", description="svetlana | dmitry")


class ComposerResponse(BaseModel):
    intent: str
    message: str
    material_id: str | None = None
    answer: str | None = None
    sources: list[MaterialListItem] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    type: str = "material"  # material | tag | category
    title: str
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    score: int | None = None
    status: str
    source_type: str
    summary: str | None = None
    degree: int = 0


class GraphLink(BaseModel):
    source: str
    target: str
    reason: str
    weight: int | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)


class SearchResponse(BaseModel):
    answer: str
    sources: list[MaterialListItem]


class CategoryOut(BaseModel):
    id: int
    name: str


class TagOut(BaseModel):
    id: int
    name: str
    status: str


class EnqueueResponse(BaseModel):
    id: str
    status: str
    message: str


class MaterialDeleteRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=200)


class MaterialDeleteResponse(BaseModel):
    deleted: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    count: int = 0


class PersonalProfile(BaseModel):
    role: str = ""
    goals: list[str] = Field(default_factory=list)
    focuses: list[str] = Field(default_factory=list)
    anti_focuses: list[str] = Field(default_factory=list)
    horizon: str = "90d"  # now | 90d | long
    current_priorities: str = ""


class AppSettingsOut(BaseModel):
    default_depth: str = "standard"  # compact | standard | detailed
    auto_moments: bool = True
    moments_vision_gate: bool = False
    provider_order: list[str] = Field(default_factory=lambda: ["groq", "google"])
    personal_profile: PersonalProfile = Field(default_factory=PersonalProfile)


class AppSettingsUpdate(BaseModel):
    default_depth: str | None = None
    auto_moments: bool | None = None
    moments_vision_gate: bool | None = None
    personal_profile: PersonalProfile | None = None


class ApiKeyOut(BaseModel):
    id: int | str
    provider: str
    label: str | None = None
    hint: str
    enabled: bool
    source: str
    last_ok_at: datetime | None = None
    last_error: str | None = None


class ApiKeyCreate(BaseModel):
    provider: str = Field(..., pattern="^(groq|google)$")
    keys: str = Field(..., min_length=8)
    label: str | None = None


class ApiKeyPatch(BaseModel):
    enabled: bool | None = None
    label: str | None = None


class KeysCheckResponse(BaseModel):
    provider: str
    ok: int
    failed: int
    details: list[dict[str, Any]] = Field(default_factory=list)
