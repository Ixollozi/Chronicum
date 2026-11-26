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


class TagStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


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
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
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


class SearchFeedback(Base):
    __tablename__ = "search_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ids: Mapped[list[str] | None] = mapped_column(JSON, default=list)
    helpful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
