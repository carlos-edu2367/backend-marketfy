"""Persist historical product-to-tax-rule associations.

Revision ID: 20260714_0002
Revises: 20260714_0001
Create Date: 2026-07-14 00:02:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0002"
down_revision: Union[str, Sequence[str], None] = "20260714_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_tax_rule_assignments",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("product_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("tax_rule_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("product_tax_rules.id"), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_ptra_product_effective",
        "product_tax_rule_assignments",
        ["product_id", "effective_from", "effective_to"],
    )
    op.create_index(
        "ix_ptra_market_product",
        "product_tax_rule_assignments",
        ["market_id", "product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ptra_market_product", table_name="product_tax_rule_assignments")
    op.drop_index("ix_ptra_product_effective", table_name="product_tax_rule_assignments")
    op.drop_table("product_tax_rule_assignments")
