"""Plan presentation fields: description, is_recommended, display_order."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0021"
down_revision: Union[str, Sequence[str], None] = "20260810_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("description", sa.String(length=280), nullable=True))
    op.add_column(
        "plans",
        sa.Column("is_recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "plans",
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("plans", "display_order")
    op.drop_column("plans", "is_recommended")
    op.drop_column("plans", "description")
