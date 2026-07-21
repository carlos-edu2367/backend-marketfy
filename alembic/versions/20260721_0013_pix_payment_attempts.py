"""Pix (Mercado Pago): cria tabela pix_payment_attempts.

Revision ID: 20260721_0013
Revises: 20260721_0012
Create Date: 2026-07-21 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0013"
down_revision: Union[str, Sequence[str], None] = "20260721_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pix_payment_attempts",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("market_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("sale_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("box_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("terminal_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("operator_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="mercado_pago"),
        sa.Column("modality", sa.String(), nullable=False, server_default="qr_dynamic"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("external_status", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("external_reference", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("order_id", sa.String(), nullable=True),
        sa.Column("payment_id", sa.String(), nullable=True),
        sa.Column("receiver_account_id", sa.String(), nullable=True),
        sa.Column("qr_data", sa.Text(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_query_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"]),
        sa.ForeignKeyConstraint(["box_id"], ["boxes.id"]),
        sa.ForeignKeyConstraint(["terminal_id"], ["terminals.id"]),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
    )
    op.create_unique_constraint("uq_pix_attempt_idempotency", "pix_payment_attempts", ["idempotency_key"])
    op.create_unique_constraint("uq_pix_attempt_provider_order", "pix_payment_attempts", ["provider", "order_id"])
    op.create_unique_constraint("uq_pix_attempt_external_reference", "pix_payment_attempts", ["external_reference"])
    op.create_index("ix_pix_attempt_market_status", "pix_payment_attempts", ["market_id", "status"])
    op.create_index("ix_pix_attempt_expires", "pix_payment_attempts", ["expires_at"])
    op.create_index("ix_pix_attempt_sale", "pix_payment_attempts", ["sale_id"])
    op.create_index(
        "uq_pix_attempt_active_sale", "pix_payment_attempts", ["sale_id"], unique=True,
        postgresql_where=sa.text("status IN ('pending','in_analysis','confirmation_pending')"),
    )


def downgrade() -> None:
    op.drop_table("pix_payment_attempts")
