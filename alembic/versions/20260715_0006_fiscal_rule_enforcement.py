"""Persist legacy-safe fiscal rule enforcement and immutable SEFAZ evidence.

Revision ID: 20260715_0006
Revises: 20260715_0005
Create Date: 2026-07-15 00:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0006"
down_revision: Union[str, Sequence[str], None] = "20260715_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fiscal_tenant_configs",
        sa.Column("fiscal_rule_enforcement", sa.String(length=8), nullable=True),
    )
    op.execute(
        "UPDATE fiscal_tenant_configs "
        "SET fiscal_rule_enforcement = 'off' "
        "WHERE fiscal_rule_enforcement IS NULL"
    )
    op.alter_column(
        "fiscal_tenant_configs",
        "fiscal_rule_enforcement",
        nullable=False,
        server_default="off",
    )
    op.create_check_constraint(
        "ck_fiscal_tenant_configs_rule_enforcement",
        "fiscal_tenant_configs",
        "fiscal_rule_enforcement IN ('off', 'warn', 'block')",
    )
    op.create_table(
        "tax_rule_sefaz_authorizations",
        sa.Column("rule_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("product_tax_rules.id"), primary_key=True),
        sa.Column("accountant_user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("authorized_xml_storage_key", sa.Text(), nullable=False),
        sa.Column("xml_sha256", sa.String(length=64), nullable=False),
        sa.Column("access_key", sa.String(length=44), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("authorized_at", sa.DateTime(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_tax_rule_sefaz_authorization_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'tax_rule_sefaz_authorizations are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_tax_rule_sefaz_authorizations_immutable
        BEFORE UPDATE OR DELETE ON tax_rule_sefaz_authorizations
        FOR EACH ROW EXECUTE FUNCTION prevent_tax_rule_sefaz_authorization_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_tax_rule_sefaz_authorizations_immutable "
        "ON tax_rule_sefaz_authorizations"
    )
    op.execute("DROP FUNCTION prevent_tax_rule_sefaz_authorization_mutation()")
    op.drop_table("tax_rule_sefaz_authorizations")
    op.drop_constraint(
        "ck_fiscal_tenant_configs_rule_enforcement",
        "fiscal_tenant_configs",
        type_="check",
    )
    op.drop_column("fiscal_tenant_configs", "fiscal_rule_enforcement")
