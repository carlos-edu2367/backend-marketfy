"""Store fiscal receipt audit time as timezone-aware PostgreSQL timestamptz.

Revision ID: 20260715_0006
Revises: 20260715_0005
Create Date: 2026-07-15 00:06:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260715_0006"
down_revision: Union[str, Sequence[str], None] = "20260715_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Historical non-null values were written by the server as UTC-naive.
    # Preserve their instant explicitly; null legacy values stay null.
    op.execute(
        "ALTER TABLE sales ALTER COLUMN received_at TYPE TIMESTAMP WITH TIME ZONE "
        "USING received_at AT TIME ZONE 'UTC'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE sales ALTER COLUMN received_at TYPE TIMESTAMP WITHOUT TIME ZONE "
        "USING received_at AT TIME ZONE 'UTC'"
    )
