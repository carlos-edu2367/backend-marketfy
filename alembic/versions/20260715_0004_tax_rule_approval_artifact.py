"""Persist immutable approval artifact storage keys.

Revision ID: 20260715_0004
Revises: 20260714_0003
Create Date: 2026-07-15 00:04:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260715_0004"
down_revision: Union[str, Sequence[str], None] = "20260714_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "tax_rule_approvals",
        "homologation_xml_reference",
        new_column_name="homologation_xml_storage_key",
    )


def downgrade() -> None:
    op.alter_column(
        "tax_rule_approvals",
        "homologation_xml_storage_key",
        new_column_name="homologation_xml_reference",
    )
