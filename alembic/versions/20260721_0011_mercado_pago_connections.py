"""Pix (Mercado Pago): cria tabela mercado_pago_connections.

Revision ID: 20260721_0011
Revises: 20260720_0010
Create Date: 2026-07-21 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0011"
down_revision: Union[str, Sequence[str], None] = "20260720_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mercado_pago_connections",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("market_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="mercado_pago"),
        sa.Column("status", sa.String(), nullable=False, server_default="not_connected"),
        sa.Column("mp_user_id", sa.String(), nullable=True),
        sa.Column("mp_nickname", sa.String(), nullable=True),
        sa.Column("mp_email_masked", sa.String(), nullable=True),
        sa.Column("scopes", sa.String(), nullable=True),
        sa.Column("pix_enabled", sa.Boolean(), nullable=True),
        sa.Column("access_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("refresh_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
    )
    op.create_unique_constraint("uq_mp_conn_market", "mercado_pago_connections", ["market_id"])
    op.create_index("ix_mp_conn_status", "mercado_pago_connections", ["status"])
    op.create_index("ix_mp_conn_mp_user_id", "mercado_pago_connections", ["mp_user_id"])


def downgrade() -> None:
    op.drop_table("mercado_pago_connections")
