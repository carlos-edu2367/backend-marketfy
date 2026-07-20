"""Merge fiscal-rule and billing migration heads.

Revision ID: 20260720_0010
Revises: 20260715_0009, d8f3a71b2c94
Create Date: 2026-07-20 19:00:00.000000
"""

from typing import Sequence, Union


revision: str = "20260720_0010"
down_revision: Union[str, Sequence[str], None] = (
    "20260715_0009",
    "d8f3a71b2c94",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join independent migration branches without changing schema."""


def downgrade() -> None:
    """Split only the Alembic graph; no schema operation is required."""
