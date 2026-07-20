"""Persist actionable warn-mode fiscal rule pendencies on sales.

Revision ID: 20260715_0009
Revises: 20260715_0008
Create Date: 2026-07-15 00:09:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0009"
down_revision: Union[str, Sequence[str], None] = "20260715_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sales",
        sa.Column("fiscal_rule_pendencies_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sales", "fiscal_rule_pendencies_json")
