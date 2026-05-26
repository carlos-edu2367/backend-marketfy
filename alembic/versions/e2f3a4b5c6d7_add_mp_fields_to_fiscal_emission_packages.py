"""add_mp_fields_to_fiscal_emission_packages

Revision ID: e2f3a4b5c6d7
Revises: c4d5e6f7a8b9
Create Date: 2026-05-26 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("fiscal_emission_packages", sa.Column("package_slug", sa.String(), nullable=True))
    op.add_column("fiscal_emission_packages", sa.Column("mp_preference_id", sa.String(), nullable=True))
    op.add_column("fiscal_emission_packages", sa.Column("mp_payment_id", sa.String(), nullable=True))
    op.add_column("fiscal_emission_packages", sa.Column("mp_external_reference", sa.String(), nullable=True))
    op.add_column("fiscal_emission_packages", sa.Column("price_gross", sa.Numeric(10, 2), nullable=True))
    op.add_column("fiscal_emission_packages", sa.Column("price_net_target", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "fiscal_emission_packages",
        sa.Column("purchased_at_market_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_fep_mp_external_ref",
        "fiscal_emission_packages",
        ["mp_external_reference"],
    )
    op.create_index(
        "ix_fep_owner_payment_status",
        "fiscal_emission_packages",
        ["owner_id", "payment_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_fep_owner_payment_status", table_name="fiscal_emission_packages")
    op.drop_constraint("uq_fep_mp_external_ref", "fiscal_emission_packages", type_="unique")
    op.drop_column("fiscal_emission_packages", "purchased_at_market_id")
    op.drop_column("fiscal_emission_packages", "price_net_target")
    op.drop_column("fiscal_emission_packages", "price_gross")
    op.drop_column("fiscal_emission_packages", "mp_external_reference")
    op.drop_column("fiscal_emission_packages", "mp_payment_id")
    op.drop_column("fiscal_emission_packages", "mp_preference_id")
    op.drop_column("fiscal_emission_packages", "package_slug")
