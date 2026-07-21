"""Pix (Mercado Pago): adiciona colunas de pagamento Pix a payments.

Revision ID: 20260721_0015
Revises: 20260721_0014
Create Date: 2026-07-21 11:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0015"
down_revision: Union[str, Sequence[str], None] = "20260721_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("modality", sa.String(), nullable=False, server_default="manual"))
    op.add_column("payments", sa.Column("provider", sa.String(), nullable=True))
    op.add_column("payments", sa.Column("pix_attempt_id", sa.UUID(as_uuid=True), nullable=True))
    op.add_column("payments", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("external_reference", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_payments_pix_attempt_id", "payments", "pix_payment_attempts", ["pix_attempt_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_payments_pix_attempt_id", "payments", type_="foreignkey")
    op.drop_column("payments", "external_reference")
    op.drop_column("payments", "confirmed_at")
    op.drop_column("payments", "pix_attempt_id")
    op.drop_column("payments", "provider")
    op.drop_column("payments", "modality")
