"""Pix (Mercado Pago): cria tabela mercado_pago_oauth_states.

Revision ID: 20260721_0012
Revises: 20260721_0011
Create Date: 2026-07-21 10:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0012"
down_revision: Union[str, Sequence[str], None] = "20260721_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mercado_pago_oauth_states",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("market_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("initiated_by_user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("code_verifier_ciphertext", sa.Text(), nullable=True),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"]),
    )
    op.create_unique_constraint("uq_mp_oauth_state", "mercado_pago_oauth_states", ["state"])
    op.create_index("ix_mp_oauth_state_expires", "mercado_pago_oauth_states", ["expires_at"])


def downgrade() -> None:
    op.drop_table("mercado_pago_oauth_states")
