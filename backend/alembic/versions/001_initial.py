"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-08-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEED_CATEGORIES = [
    "AI",
    "Маркетинг",
    "Продажи",
    "Программирование",
    "Дизайн",
    "Бизнес",
    "Фриланс",
]


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _uuid_type():
    return sa.String(36).with_variant(postgresql.UUID(as_uuid=False), "postgresql")


def _tsvector_type():
    return sa.Text().with_variant(postgresql.TSVECTOR(), "postgresql")


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    now_default = sa.text("now()") if is_pg else sa.text("CURRENT_TIMESTAMP")
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
    )
    op.create_table(
        "materials",
        sa.Column("id", _uuid_type(), primary_key=True),
        sa.Column("title", sa.String(500)),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(2000)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("user_id", sa.Integer()),
        sa.Column("telegram_chat_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now_default),
        sa.Column("raw_text", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("main_idea", sa.Text()),
        sa.Column("key_ideas", _json_type(), server_default="[]"),
        sa.Column("practical_takeaways", _json_type(), server_default="[]"),
        sa.Column("tools", _json_type(), server_default="[]"),
        sa.Column("technologies", _json_type(), server_default="[]"),
        sa.Column("links", _json_type(), server_default="[]"),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id")),
        sa.Column("usefulness_score", sa.Integer()),
        sa.Column("usefulness_reason", sa.Text()),
        sa.Column("usefulness_updated_at", sa.DateTime(timezone=True)),
        sa.Column("processing_status", sa.String(30), server_default="queued"),
        sa.Column("processing_stage", sa.String(20)),
        sa.Column("error_message", sa.Text()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("file_path", sa.String(1000)),
        sa.Column("usage_tokens_in", sa.Integer(), server_default="0"),
        sa.Column("usage_tokens_out", sa.Integer(), server_default="0"),
        sa.Column("usage_audio_seconds", sa.Float(), server_default="0"),
        sa.Column("usage_meta", _json_type(), server_default="{}"),
        sa.Column("search_vector", _tsvector_type()),
    )
    op.create_index("ix_materials_content_hash", "materials", ["content_hash"])
    op.create_index("ix_materials_user_id", "materials", ["user_id"])
    if is_pg:
        op.create_index(
            "ix_materials_search_vector",
            "materials",
            ["search_vector"],
            postgresql_using="gin",
        )

    op.create_table(
        "material_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_id", _uuid_type(), sa.ForeignKey("materials.id", ondelete="CASCADE")),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE")),
        sa.Column("proposed", sa.Boolean(), server_default=sa.text("false") if is_pg else sa.text("0")),
        sa.UniqueConstraint("material_id", "tag_id", name="uq_material_tag"),
    )

    categories = sa.table("categories", sa.column("name", sa.String))
    op.bulk_insert(categories, [{"name": n} for n in SEED_CATEGORIES])


def downgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    op.drop_table("material_tags")
    if is_pg:
        op.drop_index("ix_materials_search_vector", table_name="materials")
    op.drop_index("ix_materials_user_id", table_name="materials")
    op.drop_index("ix_materials_content_hash", table_name="materials")
    op.drop_table("materials")
    op.drop_table("tags")
    op.drop_table("categories")
