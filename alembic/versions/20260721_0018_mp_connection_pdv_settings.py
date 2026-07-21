"""Pix (Mercado Pago): colunas de settings do PDV em mercado_pago_connections.

Revision ID: 20260721_0018
Revises: 20260721_0017
Create Date: 2026-07-21 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0018"
down_revision: Union[str, Sequence[str], None] = "20260721_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mercado_pago_connections",
        sa.Column("enabled_in_pdv", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "mercado_pago_connections",
        sa.Column("allowed_terminal_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "mercado_pago_connections",
        sa.Column("fees_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mercado_pago_connections",
        sa.Column("expiration_override", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mercado_pago_connections", "expiration_override")
    op.drop_column("mercado_pago_connections", "fees_acknowledged_at")
    op.drop_column("mercado_pago_connections", "allowed_terminal_ids")
    op.drop_column("mercado_pago_connections", "enabled_in_pdv")
