"""Add index to bc_job_id

Revision ID: b3b0647296ed
Revises: 8c3c55fda46c
Create Date: 2026-05-27 01:15:08.966767

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3b0647296ed'
down_revision: Union[str, Sequence[str], None] = '8c3c55fda46c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_fep_bc_job_id", "fiscal_emission_packages", ["bc_job_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_fep_bc_job_id", table_name="fiscal_emission_packages")
