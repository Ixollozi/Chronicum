from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MaterialListItem(BaseModel):
    id: str
    title: str | None
    source_type: str
    entry_kind: str = "knowledge"
    source_url: str | None
    summary: str | None
    main_idea: str | None
    category: str | None
    tags: list[str]
    usefulness_score: int | None
    processing_status: str
    processing_stage: str | None
    trust_level: str | None = None
    analysis_confidence: float | None = None
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
    evidence: list[Any] = Field(default_factory=list)
    locked_fields: list[Any] = Field(default_factory=list)
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


class MaterialPatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    main_idea: str | None = None
    digest: str | None = None
    tools: list[Any] | None = None
    links: list[Any] | None = None
    usefulness_score: int | None = Field(default=None, ge=1, le=10)
    note: str | None = None


class CorrectionCreate(BaseModel):
    field: str  # alias | repo | banned_tool
    value: dict[str, Any]
    note: str | None = None


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class ComposerRequest(BaseModel):
    text: str = Field(..., min_length=1)
    mode: str = Field(
        default="auto",
        description="auto | ask | knowledge | task | rule",
    )


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=12000)
    voice: str = Field(default="svetlana", description="svetlana | dmitry")


class ComposerBatchItem(BaseModel):
    id: str
    status: str
    already_exists: bool = False


class ComposerResponse(BaseModel):
    intent: str
    message: str
    material_id: str | None = None
    answer: str | None = None
    sources: list[MaterialListItem] = Field(default_factory=list)
    already_exists: bool = False
    items: list[ComposerBatchItem] = Field(default_factory=list)


class GraphCluster(BaseModel):
    id: str
    label: str
    category: str | None = None
    color: str
    color_slot: str
    count: int
    avg_score: float | None = None
    x: float
    y: float
    radius: float
    hull: list[list[float]] = Field(default_factory=list)
    top_tags: list[str] = Field(default_factory=list)
    top_tools: list[str] = Field(default_factory=list)


class GraphClusterEdge(BaseModel):
    source: str
    target: str
    weight: float
    shared_edges: int
    similarity: float | None = None
    top_tools: list[str] = Field(default_factory=list)


class GraphFacetItem(BaseModel):
    key: str
    label: str
    count: int


class GraphFacets(BaseModel):
    categories: list[GraphFacetItem] = Field(default_factory=list)
    tags: list[GraphFacetItem] = Field(default_factory=list)
    tools: list[GraphFacetItem] = Field(default_factory=list)
    source_types: list[GraphFacetItem] = Field(default_factory=list)


class GraphMeta(BaseModel):
    total_materials: int = 0
    total_nodes: int = 0
    total_edges: int = 0
    total_clusters: int = 0
    uncategorized_count: int = 0
    untagged_count: int = 0
    max_degree: int = 0
    median_degree: float = 0.0
    edge_counts: dict[str, int] = Field(default_factory=dict)
    facets: GraphFacets = Field(default_factory=GraphFacets)


class GraphNode(BaseModel):
    id: str
    type: str = "material"  # material | client
    title: str
    category: str | None = None
    cluster_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    score: int | None = None
    status: str
    source_type: str
    url: str | None = None
    summary: str | None = None
    created_at: str | None = None
    x: float = 0.0
    y: float = 0.0
    degree: int = 0


class GraphLink(BaseModel):
    source: str
    target: str
    reason: str
    weight: int | None = None
    label: str | None = None
    similarity: float | None = None


class GraphResponse(BaseModel):
    clusters: list[GraphCluster] = Field(default_factory=list)
    nodes: list[GraphNode]
    edges: list[GraphLink] = Field(default_factory=list)
    links: list[GraphLink] = Field(default_factory=list)
    cluster_edges: list[GraphClusterEdge] = Field(default_factory=list)
    meta: GraphMeta = Field(default_factory=GraphMeta)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)


class SearchResponse(BaseModel):
    answer: str
    sources: list[MaterialListItem]


class SearchFeedbackCreate(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    answer: str | None = Field(default=None, max_length=12000)
    source_ids: list[str] = Field(default_factory=list, max_length=30)
    helpful: bool | None = None
    useful: bool | None = None
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _normalize_flag(self) -> "SearchFeedbackCreate":
        if self.helpful is None and self.useful is None:
            raise ValueError("Either helpful or useful must be provided")
        flag = bool(self.helpful if self.helpful is not None else self.useful)
        self.helpful = flag
        self.useful = flag
        return self


class SearchFeedbackOut(BaseModel):
    id: int
    query: str
    helpful: bool
    useful: bool = True
    ok: bool = True
    source_ids: list[str] = Field(default_factory=list)
    comment: str | None = None
    created_at: datetime | None = None


class CookiesStatusOut(BaseModel):
    configured: bool
    source: str
    path: str | None = None
    browser: str | None = None
    entries: int = 0
    domains: list[str] = Field(default_factory=list)
    platforms: dict[str, bool] = Field(default_factory=dict)
    writable: bool = True


class CookiesUpdate(BaseModel):
    content: str = Field(..., min_length=1)


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
    already_exists: bool = False


class MaterialsBusyResponse(BaseModel):
    queued: int = 0
    processing: int = 0
    total: int = 0


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
    video_full: bool = True
    moments_vision_gate: bool = False
    provider_order: list[str] = Field(default_factory=lambda: ["groq", "google"])
    personal_profile: PersonalProfile = Field(default_factory=PersonalProfile)


class AppSettingsUpdate(BaseModel):
    default_depth: str | None = None
    auto_moments: bool | None = None
    video_full: bool | None = None
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

class ClientCreate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)
    contacts: dict[str, Any] = Field(default_factory=dict)
    context_line: str | None = None
    canon_facts: str | None = None
    force: bool = False


class ClientUpdate(BaseModel):
    display_name: str | None = None
    aliases: list[str] | None = None
    contacts: dict[str, Any] | None = None
    context_line: str | None = None
    canon_facts: str | None = None


class ClientMessageOut(BaseModel):
    id: str
    body: str
    source: str
    speaker: str = "unknown"
    speaker_name: str | None = None
    kind: str = "text"
    event_at: datetime | None = None
    created_at: datetime | None = None


class ClientOut(BaseModel):
    id: str
    display_name: str
    aliases: list[Any] = Field(default_factory=list)
    contacts: dict[str, Any] = Field(default_factory=dict)
    context_line: str | None = None
    canon_facts: str | None = None
    summary: str | None = None
    summary_version: int = 0
    summary_updated_at: datetime | None = None
    summary_up_to_message_id: str | None = None
    summary_stale: bool = False
    messages_since_summary: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    message_count: int = 0


class ClientDetail(ClientOut):
    messages: list[ClientMessageOut] = Field(default_factory=list)


class ClientMessageCreate(BaseModel):
    body: str = Field(..., min_length=1)
    refresh_summary: bool = False
    source: str = "web"
    speaker: str = "unknown"
    speaker_name: str | None = None
    kind: str = "text"


class ClientMessageUpdate(BaseModel):
    body: str = Field(..., min_length=1)


class PastePreviewRequest(BaseModel):
    text: str = Field(..., min_length=1)


class PasteCommitRequest(BaseModel):
    text: str = Field(..., min_length=1)
    speaker_roles: dict[str, str] = Field(default_factory=dict)
    source: str = "web"


class ClientMergeRequest(BaseModel):
    from_id: str
    into_id: str


class MaterialClientLinkCreate(BaseModel):
    material_id: str
    relation: str | None = None


class ClientContextResponse(BaseModel):
    client: dict[str, Any]
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    search_hits: list[dict[str, Any]] = Field(default_factory=list)
    linked_materials: list[dict[str, Any]] = Field(default_factory=list)
