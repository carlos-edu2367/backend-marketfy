"""Admin-granted NFC-e emission credits."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260810_0020"
down_revision: Union[str, Sequence[str], None] = "20260721_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fiscal_emission_packages",
        sa.Column("grant_reason_code", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "fiscal_emission_packages",
        sa.Column("grant_note", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "fiscal_emission_packages",
        sa.Column("granted_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_fep_granted_by",
        "fiscal_emission_packages",
        "users",
        ["granted_by_id"],
        ["id"],
    )
    op.create_index(
        "ix_fep_owner_type",
        "fiscal_emission_packages",
        ["owner_id", "package_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_fep_owner_type", table_name="fiscal_emission_packages")
    op.drop_constraint("fk_fep_granted_by", "fiscal_emission_packages", type_="foreignkey")
    op.drop_column("fiscal_emission_packages", "granted_by_id")
    op.drop_column("fiscal_emission_packages", "grant_note")
    op.drop_column("fiscal_emission_packages", "grant_reason_code")
