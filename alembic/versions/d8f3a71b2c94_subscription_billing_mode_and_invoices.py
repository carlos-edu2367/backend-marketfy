"""subscription billing_mode and billing_invoices

Revision ID: d8f3a71b2c94
Revises: b3b0647296ed
Create Date: 2026-07-20 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8f3a71b2c94"
down_revision: Union[str, None] = "b3b0647296ed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "billing_subscriptions",
        sa.Column("billing_mode", sa.String(), nullable=False, server_default="recurring"),
    )
    op.create_table(
        "billing_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("billing_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("due_date", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("bc_job_id", sa.String(), nullable=True),
        sa.Column("bc_payment_id", sa.String(), nullable=True),
        sa.Column("checkout_url", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint("uq_billing_invoice_idem", "billing_invoices", ["idempotency_key"])
    op.create_index("ix_billing_invoice_owner", "billing_invoices", ["owner_id"])
    op.create_index("ix_billing_invoice_sub", "billing_invoices", ["subscription_id"])
    op.create_index("ix_billing_invoice_sub_status", "billing_invoices", ["subscription_id", "status"])
    op.create_index("ix_billing_invoice_status_due", "billing_invoices", ["status", "due_date"])


def downgrade() -> None:
    op.drop_index("ix_billing_invoice_status_due", table_name="billing_invoices")
    op.drop_index("ix_billing_invoice_sub_status", table_name="billing_invoices")
    op.drop_index("ix_billing_invoice_sub", table_name="billing_invoices")
    op.drop_index("ix_billing_invoice_owner", table_name="billing_invoices")
    op.drop_constraint("uq_billing_invoice_idem", "billing_invoices", type_="unique")
    op.drop_table("billing_invoices")
    op.drop_column("billing_subscriptions", "billing_mode")
