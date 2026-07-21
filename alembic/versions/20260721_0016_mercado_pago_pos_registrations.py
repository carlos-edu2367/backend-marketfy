"""Pix (Mercado Pago): cria tabela mercado_pago_pos_registrations.

Revision ID: 20260721_0016
Revises: 20260721_0015
Create Date: 2026-07-21 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0016"
down_revision: Union[str, Sequence[str], None] = "20260721_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mercado_pago_pos_registrations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("market_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("terminal_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("mp_store_id", sa.String(), nullable=False),
        sa.Column("mp_pos_external_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["terminal_id"], ["terminals.id"], ondelete="CASCADE"),
    )
    op.create_unique_constraint(
        "uq_mp_pos_market_terminal", "mercado_pago_pos_registrations", ["market_id", "terminal_id"]
    )
    op.create_index("ix_mp_pos_market", "mercado_pago_pos_registrations", ["market_id"])


def downgrade() -> None:
    op.drop_table("mercado_pago_pos_registrations")
