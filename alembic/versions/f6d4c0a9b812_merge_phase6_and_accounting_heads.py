"""merge phase6 and accounting heads

Revision ID: f6d4c0a9b812
Revises: 740995c1fda9, e6b7a2f1c904
Create Date: 2026-05-21 00:00:00.000000
"""

from typing import Sequence, Union

revision: str = "f6d4c0a9b812"
down_revision: Union[str, Sequence[str], None] = ("740995c1fda9", "e6b7a2f1c904")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
