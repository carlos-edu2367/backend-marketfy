"""phase6 sessions and performance indexes

Revision ID: e6b7a2f1c904
Revises: c9f2d1a8475b
Create Date: 2026-05-21 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6b7a2f1c904"
down_revision: Union[str, None] = "c9f2d1a8475b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti_hash", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti_hash"),
    )
    op.create_index("ix_refresh_sessions_user", "refresh_sessions", ["user_id"])
    op.create_index("ix_refresh_sessions_expires_at", "refresh_sessions", ["expires_at"])

    op.execute("CREATE INDEX IF NOT EXISTS ix_products_market_code ON products (market_id, code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_products_market_updated ON products (market_id, updated_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_market_created_at ON sales (market_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_market_offline_id ON sales (market_id, offline_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_customers_market_cpf ON customers (market_id, cpf)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_customers_market_updated ON customers (market_id, updated_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_financial_market_created_at ON financial_transactions (market_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_financial_market_due_date ON financial_transactions (market_id, due_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_requester_status ON tickets (requester_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_market_status ON tickets (market_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_billing_sub_owner_status ON billing_subscriptions (owner_id, status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_billing_sub_owner_status")
    op.execute("DROP INDEX IF EXISTS ix_tickets_market_status")
    op.execute("DROP INDEX IF EXISTS ix_tickets_requester_status")
    op.execute("DROP INDEX IF EXISTS ix_financial_market_due_date")
    op.execute("DROP INDEX IF EXISTS ix_financial_market_created_at")
    op.execute("DROP INDEX IF EXISTS ix_customers_market_updated")
    op.execute("DROP INDEX IF EXISTS ix_customers_market_cpf")
    op.execute("DROP INDEX IF EXISTS ix_sales_market_offline_id")
    op.execute("DROP INDEX IF EXISTS ix_sales_market_created_at")
    op.execute("DROP INDEX IF EXISTS ix_products_market_updated")
    op.execute("DROP INDEX IF EXISTS ix_products_market_code")

    op.drop_index("ix_refresh_sessions_expires_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
