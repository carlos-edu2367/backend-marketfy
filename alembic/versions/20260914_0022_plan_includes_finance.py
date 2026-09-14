"""Plan.includes_finance: per-plan gate for the Financeiro module."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0022"
down_revision: Union[str, Sequence[str], None] = "20260914_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("includes_finance", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("plans", "includes_finance")
