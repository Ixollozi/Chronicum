"""sync full ORM schema (api_keys, app_settings, missing materials columns)

Revision ID: 003_sync_full_schema
Revises: 002_p2_embeddings_feedback
Create Date: 2026-11-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_sync_full_schema"
down_revision: Union[str, None] = "002_p2_embeddings_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _existing_tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _existing_columns(bind, table_name: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    now_default = sa.text("now()") if is_pg else sa.text("CURRENT_TIMESTAMP")
    true_default = sa.text("true") if is_pg else sa.text("1")

    tables = _existing_tables(bind)
    if "materials" in tables:
        cols = _existing_columns(bind, "materials")
        missing_material_columns: list[sa.Column] = [
            sa.Column("entry_kind", sa.String(20), nullable=False, server_default="knowledge"),
            sa.Column("digest", sa.Text(), nullable=True),
            sa.Column("detailed_explanation", sa.Text(), nullable=True),
            sa.Column("objective_evaluation", _json_type(), nullable=True),
            sa.Column("ai_opinion", sa.Text(), nullable=True),
            sa.Column("ai_opinion_sources", _json_type(), nullable=True, server_default="[]"),
            sa.Column("ai_opinion_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("key_moments", _json_type(), nullable=True, server_default="[]"),
            sa.Column("code_blocks", _json_type(), nullable=True, server_default="[]"),
        ]
        for col in missing_material_columns:
            if col.name not in cols:
                op.add_column("materials", col)

    if "api_keys" not in tables:
        op.create_table(
            "api_keys",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("label", sa.String(120), nullable=True),
            sa.Column("key_enc", sa.Text(), nullable=False),
            sa.Column("key_hint", sa.String(8), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=true_default),
            sa.Column("source", sa.String(20), nullable=False, server_default="crm"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
            sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
        )
        op.create_index("ix_api_keys_provider", "api_keys", ["provider"])

    if "app_settings" not in tables:
        op.create_table(
            "app_settings",
            sa.Column("key", sa.String(80), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _existing_tables(bind)
    if "app_settings" in tables:
        op.drop_table("app_settings")
    if "api_keys" in tables:
        op.drop_index("ix_api_keys_provider", table_name="api_keys")
        op.drop_table("api_keys")
    if "materials" in tables:
        cols = _existing_columns(bind, "materials")
        for col_name in (
            "code_blocks",
            "key_moments",
            "ai_opinion_at",
            "ai_opinion_sources",
            "ai_opinion",
            "objective_evaluation",
            "detailed_explanation",
            "digest",
            "entry_kind",
        ):
            if col_name in cols:
                op.drop_column("materials", col_name)
