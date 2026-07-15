"""Add the persisted Marketfy-to-Fiscal v2 evidence contract.

Revision ID: 20260715_0008
Revises: 20260715_0007
Create Date: 2026-07-15 00:08:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0008"
down_revision: Union[str, Sequence[str], None] = "20260715_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in (
        sa.Column("issuer_regime", sa.String(length=32), nullable=True),
        sa.Column("destination_uf", sa.String(length=2), nullable=True),
        sa.Column("document_model", sa.String(length=2), nullable=True),
        sa.Column("cbenef", sa.String(length=16), nullable=True),
        sa.Column("tax_parameters_json", sa.JSON(), nullable=True),
        sa.Column("approval_json", sa.JSON(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
    ):
        op.add_column("product_tax_rules", column)

    op.create_unique_constraint(
        "uq_ptr_market_name_version",
        "product_tax_rules",
        ["market_id", "name", "version"],
    )
    op.drop_index("ix_ptr_market_status_effective", table_name="product_tax_rules")
    op.create_index(
        "ix_ptr_market_status_effective",
        "product_tax_rules",
        ["market_id", "status", "effective_from", "effective_to"],
    )

    op.alter_column(
        "sale_items",
        "fiscal_tax_snapshot_json",
        existing_type=sa.Text(),
        type_=sa.JSON(),
        existing_nullable=True,
        postgresql_using=(
            "CASE WHEN fiscal_tax_snapshot_json IS NULL THEN NULL "
            "ELSE fiscal_tax_snapshot_json::json END"
        ),
    )
    op.add_column(
        "sale_items",
        sa.Column(
            "tax_rule_id_snapshot",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    op.add_column(
        "fiscal_documents",
        sa.Column("request_contract_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "fiscal_documents",
        sa.Column("request_payload_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "fiscal_documents",
        sa.Column("request_payload_sha256", sa.String(length=64), nullable=True),
    )

    op.drop_constraint("fk_products_tax_rule_id", "products", type_="foreignkey")
    op.create_foreign_key(
        "fk_products_tax_rule_id",
        "products",
        "product_tax_rules",
        ["tax_rule_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "product_tax_rule_assignments_tax_rule_id_fkey",
        "product_tax_rule_assignments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_product_tax_rule_assignments_tax_rule_id",
        "product_tax_rule_assignments",
        "product_tax_rules",
        ["tax_rule_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_product_tax_rule_assignments_tax_rule_id",
        "product_tax_rule_assignments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "product_tax_rule_assignments_tax_rule_id_fkey",
        "product_tax_rule_assignments",
        "product_tax_rules",
        ["tax_rule_id"],
        ["id"],
    )
    op.drop_constraint("fk_products_tax_rule_id", "products", type_="foreignkey")
    op.create_foreign_key(
        "fk_products_tax_rule_id",
        "products",
        "product_tax_rules",
        ["tax_rule_id"],
        ["id"],
    )

    op.drop_column("fiscal_documents", "request_payload_sha256")
    op.drop_column("fiscal_documents", "request_payload_json")
    op.drop_column("fiscal_documents", "request_contract_version")
    op.drop_column("sale_items", "tax_rule_id_snapshot")
    op.alter_column(
        "sale_items",
        "fiscal_tax_snapshot_json",
        existing_type=sa.JSON(),
        type_=sa.Text(),
        existing_nullable=True,
        postgresql_using="fiscal_tax_snapshot_json::text",
    )

    op.drop_index("ix_ptr_market_status_effective", table_name="product_tax_rules")
    op.create_index(
        "ix_ptr_market_status_effective",
        "product_tax_rules",
        ["market_id", "status", "effective_from"],
    )
    op.drop_constraint(
        "uq_ptr_market_name_version", "product_tax_rules", type_="unique"
    )
    for column_name in (
        "retired_at",
        "published_at",
        "approval_json",
        "tax_parameters_json",
        "cbenef",
        "document_model",
        "destination_uf",
        "issuer_regime",
    ):
        op.drop_column("product_tax_rules", column_name)
