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
            sa.Column("analysis_confidence", sa.Float(), nullable=True),
            sa.Column("trust_level", sa.String(20), nullable=True),
            sa.Column("evidence", _json_type(), nullable=True, server_default="[]"),
            sa.Column("locked_fields", _json_type(), nullable=True, server_default="[]"),
        ]
        for col in missing_material_columns:
            if col.name not in cols:
                op.add_column("materials", col)

    if "search_feedback" in tables:
        sf_cols = _existing_columns(bind, "search_feedback")
        if "useful" not in sf_cols:
            op.add_column("search_feedback", sa.Column("useful", sa.Boolean(), nullable=True))

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("google_sub", sa.String(64), nullable=False, unique=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("name", sa.String(200), nullable=True),
            sa.Column("picture_url", sa.String(1000), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
        )
        op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)
        op.create_index("ix_users_email", "users", ["email"])

    if "user_api_tokens" not in tables:
        op.create_table(
            "user_api_tokens",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("token_prefix", sa.String(12), nullable=False),
            sa.Column("label", sa.String(120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_user_api_tokens_user_id", "user_api_tokens", ["user_id"])
        op.create_index("ix_user_api_tokens_token_hash", "user_api_tokens", ["token_hash"], unique=True)

    if "clients" not in tables:
        op.create_table(
            "clients",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=True),
            sa.Column("display_name", sa.String(200), nullable=False),
            sa.Column("aliases", _json_type(), nullable=True, server_default="[]"),
            sa.Column("contacts", _json_type(), nullable=True, server_default="{}"),
            sa.Column("context_line", sa.String(500), nullable=True),
            sa.Column("canon_facts", sa.Text(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("summary_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("summary_up_to_message_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now_default),
        )
        op.create_index("ix_clients_user_id", "clients", ["user_id"])
        op.create_index("ix_clients_display_name", "clients", ["display_name"])

    if "client_messages" not in tables:
        op.create_table(
            "client_messages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("source", sa.String(20), nullable=False, server_default="web"),
            sa.Column("speaker", sa.String(20), nullable=False, server_default="unknown"),
            sa.Column("speaker_name", sa.String(200), nullable=True),
            sa.Column("kind", sa.String(20), nullable=False, server_default="text"),
            sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("content_hash", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
        )
        op.create_index("ix_client_messages_client_id", "client_messages", ["client_id"])
        op.create_index("ix_client_messages_content_hash", "client_messages", ["content_hash"])

    if "client_summary_versions" not in tables:
        op.create_table(
            "client_summary_versions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("covered_up_to_message_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
        )
        op.create_index("ix_client_summary_versions_client_id", "client_summary_versions", ["client_id"])

    if "corrections" not in tables:
        op.create_table(
            "corrections",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("scope", sa.String(20), nullable=False, server_default="material"),
            sa.Column("material_id", sa.String(36), nullable=True),
            sa.Column("field", sa.String(40), nullable=False),
            sa.Column("value", _json_type(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
        )
        op.create_index("ix_corrections_material_id", "corrections", ["material_id"])

    if "material_client_links" not in tables:
        op.create_table(
            "material_client_links",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("material_id", sa.String(36), sa.ForeignKey("materials.id", ondelete="CASCADE"), nullable=False),
            sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
            sa.Column("relation", sa.String(100), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default),
            sa.UniqueConstraint("material_id", "client_id", name="uq_material_client"),
        )
        op.create_index("ix_material_client_links_material_id", "material_client_links", ["material_id"])
        op.create_index("ix_material_client_links_client_id", "material_client_links", ["client_id"])

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
