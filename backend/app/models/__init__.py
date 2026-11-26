from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SourceType(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    ARTICLE = "article"
    PDF = "pdf"
    VOICE = "voice"
    NOTE = "note"
    IMAGE = "image"
    VIDEO = "video"


class EntryKind(StrEnum):
    """Why the user saved this — independent of source_type."""

    KNOWLEDGE = "knowledge"
    TASK = "task"
    RULE = "rule"


class ProcessingStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    PREVIEW = "preview"
    AWAITING_FULL = "awaiting_full"
    DONE = "done"
    FAILED = "failed"


class ProcessingStage(StrEnum):
    PREVIEW = "preview"
    FULL = "full"


class ProcessingProgress(StrEnum):
    """Coarse UI steps written into processing_stage while status=processing."""

    FETCH = "fetch"
    ASR = "asr"
    CARD = "card"
    MOMENTS = "moments"


class TagStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    google_sub: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    picture_url: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    api_tokens: Mapped[list["UserApiToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserApiToken(Base):
    """Personal API tokens for MCP / scripts (raw secret shown once at mint)."""

    __tablename__ = "user_api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    token_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="api_tokens")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TagStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str | None] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entry_kind: Mapped[str] = mapped_column(String(20), default=EntryKind.KNOWLEDGE.value)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    raw_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    main_idea: Mapped[str | None] = mapped_column(Text)
    digest: Mapped[str | None] = mapped_column(Text)
    detailed_explanation: Mapped[str | None] = mapped_column(Text)
    objective_evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ai_opinion: Mapped[str | None] = mapped_column(Text)
    ai_opinion_sources: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    ai_opinion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_moments: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    code_blocks: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    key_ideas: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    practical_takeaways: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    tools: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    technologies: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    links: Mapped[list[Any] | None] = mapped_column(JSON, default=list)

    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    usefulness_score: Mapped[int | None] = mapped_column(Integer)
    # Confidence of the analysis itself (0..1), separate from material quality
    analysis_confidence: Mapped[float | None] = mapped_column(Float)
    trust_level: Mapped[str | None] = mapped_column(String(20))  # mcp_safe|review|weak
    evidence: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    locked_fields: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    usefulness_reason: Mapped[str | None] = mapped_column(Text)
    usefulness_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    processing_status: Mapped[str] = mapped_column(String(30), default=ProcessingStatus.QUEUED.value)
    processing_stage: Mapped[str | None] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    file_path: Mapped[str | None] = mapped_column(String(1000))

    usage_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    usage_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    usage_audio_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    usage_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)

    # Postgres FTS only; unused on SQLite lite mode
    search_vector: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Hybrid semantic search + graph similarity vector: {"model": str, "dim": int, "vec": list[float]}
    embedding: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    category: Mapped[Category | None] = relationship()
    tags: Mapped[list["MaterialTag"]] = relationship(back_populates="material", cascade="all, delete-orphan")


class MaterialTag(Base):
    __tablename__ = "material_tags"
    __table_args__ = (UniqueConstraint("material_id", "tag_id", name="uq_material_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"))
    proposed: Mapped[bool] = mapped_column(Boolean, default=False)

    material: Mapped[Material] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # groq | google
    label: Mapped[str | None] = mapped_column(String(120))
    key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(20), default="crm")  # crm | env
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Client(Base):
    """Living correspondence memory — one person (not CRM / Agency OS)."""

    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    aliases: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    contacts: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    context_line: Mapped[str | None] = mapped_column(String(500))
    canon_facts: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_version: Mapped[int] = mapped_column(Integer, default=0)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_up_to_message_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["ClientMessage"]] = relationship(
        back_populates="client", cascade="all, delete-orphan", order_by="ClientMessage.created_at"
    )


class ClientMessage(Base):
    __tablename__ = "client_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="web")  # web | tg | mcp
    speaker: Mapped[str] = mapped_column(String(20), default="unknown")  # me | client | other | unknown
    speaker_name: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="text")  # text | note | voice | transcript
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped[Client] = relationship(back_populates="messages")


class ClientSummaryVersion(Base):
    __tablename__ = "client_summary_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    covered_up_to_message_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Correction(Base):
    """Owner canon: a human-confirmed fact that outranks LLM output."""

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(20), default="material")  # material | global
    material_id: Mapped[str | None] = mapped_column(String(36), index=True)
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[Any | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaterialClientLink(Base):
    """Explicit, human-confirmed material <-> client link (never auto-created)."""

    __tablename__ = "material_client_links"
    __table_args__ = (UniqueConstraint("material_id", "client_id", name="uq_material_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchFeedback(Base):
    """User feedback on search answers (supports both helpful and useful fields)."""

    __tablename__ = "search_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ids: Mapped[list[str] | None] = mapped_column(JSON, default=list)
    helpful: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    useful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
