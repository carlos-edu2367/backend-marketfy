"""Persist versioned product tax rules without assigning legacy profiles.

Revision ID: 20260714_0001
Revises: b3b0647296ed
Create Date: 2026-07-14 00:01:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0001"
down_revision: Union[str, Sequence[str], None] = "b3b0647296ed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_tax_rules",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("ncm", sa.String(), nullable=True),
        sa.Column("cest", sa.String(), nullable=True),
        sa.Column("origin", sa.String(), nullable=True),
        sa.Column("cfop", sa.String(), nullable=True),
        sa.Column("icms_group", sa.String(), nullable=True),
        sa.Column("icms_cst", sa.String(), nullable=True),
        sa.Column("icms_csosn", sa.String(), nullable=True),
        sa.Column("icms_mod_bc", sa.String(), nullable=True),
        sa.Column("icms_rate", sa.Numeric(9, 4), nullable=True),
        sa.Column("icms_reduction_rate", sa.Numeric(9, 4), nullable=True),
        sa.Column("icms_st_mod_bc", sa.String(), nullable=True),
        sa.Column("icms_st_mva_rate", sa.Numeric(9, 4), nullable=True),
        sa.Column("icms_st_rate", sa.Numeric(9, 4), nullable=True),
        sa.Column("fcp_rate", sa.Numeric(9, 4), nullable=True),
        sa.Column("pis_cst", sa.String(), nullable=True),
        sa.Column("pis_rate", sa.Numeric(9, 4), nullable=True),
        sa.Column("cofins_cst", sa.String(), nullable=True),
        sa.Column("cofins_rate", sa.Numeric(9, 4), nullable=True),
        sa.Column("approved_by", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_ptr_market_status_effective",
        "product_tax_rules",
        ["market_id", "status", "effective_from"],
    )
    op.create_index("ix_ptr_market_name_version", "product_tax_rules", ["market_id", "name", "version"])

    op.add_column(
        "products",
        sa.Column(
            "tax_rule_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_tax_rules.id", name="fk_products_tax_rule_id"),
            nullable=True,
        ),
    )
    op.add_column("sale_items", sa.Column("fiscal_tax_snapshot_json", sa.Text(), nullable=True))
    op.add_column("sale_items", sa.Column("tax_rule_version_snapshot", sa.Integer(), nullable=True))

    # Legacy profiles are preserved as drafts only. Their rate JSON is intentionally
    # not interpreted and no product receives a rule assignment in this migration.
    op.execute(
        """
        INSERT INTO product_tax_rules (
            id, market_id, name, status, version, effective_from,
            ncm, cest, origin, cfop, icms_cst, icms_csosn,
            pis_cst, cofins_cst, created_at, updated_at
        )
        SELECT
            md5(random()::text || clock_timestamp()::text || id::text)::uuid,
            market_id, name, 'draft', 1, effective_from::date,
            ncm, cest, origin, cfop, icms_cst, icms_csosn,
            pis_cst, cofins_cst, created_at, updated_at
        FROM product_tax_profiles
        """
    )


def downgrade() -> None:
    op.drop_column("sale_items", "tax_rule_version_snapshot")
    op.drop_column("sale_items", "fiscal_tax_snapshot_json")
    op.drop_constraint("fk_products_tax_rule_id", "products", type_="foreignkey")
    op.drop_column("products", "tax_rule_id")
    op.drop_index("ix_ptr_market_name_version", table_name="product_tax_rules")
    op.drop_index("ix_ptr_market_status_effective", table_name="product_tax_rules")
    op.drop_table("product_tax_rules")
