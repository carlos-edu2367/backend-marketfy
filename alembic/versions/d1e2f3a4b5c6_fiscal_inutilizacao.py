"""fiscal_inutilizacao - tabela de inutilizacao de numeracao NFC-e

Revision ID: d1e2f3a4b5c6
Revises: b9f2e1a4c5d8
Create Date: 2026-05-21 14:00:00.000000
"""

from typing import Sequence, Union
import uuid
import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "b9f2e1a4c5d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fiscal_inutilizacoes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("cnpj", sa.String(), nullable=False),
        sa.Column("document_type", sa.String(), nullable=False, server_default="nfce"),
        sa.Column("series", sa.Integer(), nullable=False),
        sa.Column("start_number", sa.Integer(), nullable=False),
        sa.Column("end_number", sa.Integer(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("requested_by_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("protocol", sa.String(), nullable=True),
        sa.Column("provider_message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("authorized_at", sa.DateTime(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_index("ix_finu_market", "fiscal_inutilizacoes", ["market_id"])
    op.create_index("ix_finu_market_status", "fiscal_inutilizacoes", ["market_id", "status"])
    op.create_index("ix_finu_created_at", "fiscal_inutilizacoes", ["created_at"])
    op.create_unique_constraint("uq_finu_idempotency", "fiscal_inutilizacoes", ["idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_finu_idempotency", "fiscal_inutilizacoes", type_="unique")
    op.drop_index("ix_finu_created_at", table_name="fiscal_inutilizacoes")
    op.drop_index("ix_finu_market_status", table_name="fiscal_inutilizacoes")
    op.drop_index("ix_finu_market", table_name="fiscal_inutilizacoes")
    op.drop_table("fiscal_inutilizacoes")
