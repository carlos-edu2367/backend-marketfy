"""add_neectify_fields_to_fiscal_tenant_config

Revision ID: a1b2c3d4e5f6
Revises: d1e2f3a4b5c6
Create Date: 2026-05-26 00:00:00.000000
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fiscal_tenant_configs",
        sa.Column("neectify_issuer_id", sa.String(), nullable=True),
    )
    op.add_column(
        "fiscal_tenant_configs",
        sa.Column("neectify_cert_id", sa.String(), nullable=True),
    )
    op.add_column(
        "fiscal_tenant_configs",
        sa.Column("neectify_config_id", sa.String(), nullable=True),
    )
    op.add_column(
        "fiscal_tenant_configs",
        sa.Column(
            "neectify_onboarding_step",
            sa.String(),
            nullable=True,
            server_default="pending",
        ),
    )


def downgrade() -> None:
    op.drop_column("fiscal_tenant_configs", "neectify_onboarding_step")
    op.drop_column("fiscal_tenant_configs", "neectify_config_id")
    op.drop_column("fiscal_tenant_configs", "neectify_cert_id")
    op.drop_column("fiscal_tenant_configs", "neectify_issuer_id")
