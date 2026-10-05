"""billing_subscriptions: cancel_at_period_end, canceled_at, provisional."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260915_0024"
down_revision: Union[str, Sequence[str], None] = "20260914_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "billing_subscriptions",
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("billing_subscriptions", sa.Column("canceled_at", sa.DateTime(), nullable=True))
    op.add_column(
        "billing_subscriptions",
        sa.Column("provisional", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("billing_subscriptions", "provisional")
    op.drop_column("billing_subscriptions", "canceled_at")
    op.drop_column("billing_subscriptions", "cancel_at_period_end")
