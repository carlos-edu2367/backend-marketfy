"""Enforce durable accountant approval and non-overlapping fiscal assignments.

Revision ID: 20260714_0003
Revises: 20260714_0002
Create Date: 2026-07-14 00:03:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0003"
down_revision: Union[str, Sequence[str], None] = "20260714_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.add_column("product_tax_rules", sa.Column("rule_family_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "product_tax_rules",
        sa.Column("supersedes_rule_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_product_tax_rules_supersedes_rule_id",
        "product_tax_rules",
        "product_tax_rules",
        ["supersedes_rule_id"],
        ["id"],
    )
    # Existing versions become the root of their own durable family. No legacy
    # classification is inferred or merged.
    op.execute("UPDATE product_tax_rules SET rule_family_id = id WHERE rule_family_id IS NULL")
    op.alter_column("product_tax_rules", "rule_family_id", nullable=False)
    op.create_unique_constraint(
        "uq_product_tax_rule_family_version",
        "product_tax_rules",
        ["rule_family_id", "version"],
    )

    op.create_table(
        "tax_rule_approvals",
        sa.Column("rule_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("product_tax_rules.id"), primary_key=True),
        sa.Column("accountant_user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("homologation_xml_reference", sa.Text(), nullable=False),
        sa.Column("homologation_xml_sha256", sa.String(length=64), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=False),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_tax_rule_approval_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'tax_rule_approvals are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_tax_rule_approvals_immutable
        BEFORE UPDATE OR DELETE ON tax_rule_approvals
        FOR EACH ROW EXECUTE FUNCTION prevent_tax_rule_approval_mutation()
        """
    )
    op.execute(
        """
        ALTER TABLE product_tax_rule_assignments
        ADD CONSTRAINT ex_product_tax_rule_assignment_effective_range
        EXCLUDE USING gist (
            product_id WITH =,
            daterange(effective_from, effective_to, '[]') WITH &&
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE product_tax_rule_assignments "
        "DROP CONSTRAINT ex_product_tax_rule_assignment_effective_range"
    )
    op.execute("DROP TRIGGER trg_tax_rule_approvals_immutable ON tax_rule_approvals")
    op.execute("DROP FUNCTION prevent_tax_rule_approval_mutation()")
    op.drop_table("tax_rule_approvals")
    op.drop_constraint("uq_product_tax_rule_family_version", "product_tax_rules", type_="unique")
    op.drop_constraint("fk_product_tax_rules_supersedes_rule_id", "product_tax_rules", type_="foreignkey")
    op.drop_column("product_tax_rules", "supersedes_rule_id")
    op.drop_column("product_tax_rules", "rule_family_id")
