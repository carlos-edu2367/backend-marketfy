"""replace_mp_fields_with_bc_fields

Revision ID: 8c3c55fda46c
Revises: e2f3a4b5c6d7
Create Date: 2026-05-26 18:32:19.561509

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c3c55fda46c'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Renomeia colunas em fiscal_emission_packages
    op.alter_column("fiscal_emission_packages", "mp_preference_id", new_column_name="bc_job_id")
    op.alter_column("fiscal_emission_packages", "mp_payment_id", new_column_name="bc_payment_id")
    op.alter_column("fiscal_emission_packages", "mp_external_reference", new_column_name="bc_idempotency_key")

    # Renomeia constraint única
    op.drop_constraint("uq_fep_mp_external_ref", "fiscal_emission_packages")
    op.create_unique_constraint(
        "uq_fep_bc_idempotency_key",
        "fiscal_emission_packages",
        ["bc_idempotency_key"],
    )

    # Adiciona customer_id no model de User
    op.add_column("users", sa.Column("asaas_customer_id", sa.String(length=64), nullable=True))
    op.create_index("ix_users_asaas_customer_id", "users", ["asaas_customer_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_users_asaas_customer_id", "users")
    op.drop_column("users", "asaas_customer_id")
    op.drop_constraint("uq_fep_bc_idempotency_key", "fiscal_emission_packages")
    op.create_unique_constraint(
        "uq_fep_mp_external_ref",
        "fiscal_emission_packages",
        ["bc_idempotency_key"],
    )
    op.alter_column("fiscal_emission_packages", "bc_idempotency_key", new_column_name="mp_external_reference")
    op.alter_column("fiscal_emission_packages", "bc_payment_id", new_column_name="mp_payment_id")
    op.alter_column("fiscal_emission_packages", "bc_job_id", new_column_name="mp_preference_id")
