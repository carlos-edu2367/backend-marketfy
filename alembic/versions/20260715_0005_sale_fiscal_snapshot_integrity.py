"""Persist trusted receipt time and immutable fiscal snapshot integrity.

Revision ID: 20260715_0005
Revises: 20260715_0004
Create Date: 2026-07-15 00:05:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260715_0005"
down_revision: Union[str, Sequence[str], None] = "20260715_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable preserves legacy sales; emission rejects them fail-closed because
    # their historical client time/snapshot integrity cannot be reconstructed.
    op.add_column("sales", sa.Column("received_at", sa.DateTime(), nullable=True))
    op.add_column("sale_items", sa.Column("snapshot_sha256", sa.String(length=64), nullable=True))
    op.add_column("sale_items", sa.Column("fiscal_calculation_version", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("sale_items", "fiscal_calculation_version")
    op.drop_column("sale_items", "snapshot_sha256")
    op.drop_column("sales", "received_at")
