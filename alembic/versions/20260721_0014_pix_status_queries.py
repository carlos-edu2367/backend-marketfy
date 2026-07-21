"""Pix (Mercado Pago): cria tabela pix_status_queries.

Revision ID: 20260721_0014
Revises: 20260721_0013
Create Date: 2026-07-21 11:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0014"
down_revision: Union[str, Sequence[str], None] = "20260721_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pix_status_queries",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("attempt_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("market_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("received_status", sa.String(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["attempt_id"], ["pix_payment_attempts.id"]),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
    )
    op.create_index("ix_pix_query_attempt", "pix_status_queries", ["attempt_id", "created_at"])


def downgrade() -> None:
    op.drop_table("pix_status_queries")
