"""Fase 3 (PR 10): cria tabela market_members e backfill de owners.

Revision ID: f3a1c7d92b01
Revises: 7fd4676a4361
Create Date: 2026-05-21 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3a1c7d92b01"
down_revision: Union[str, Sequence[str], None] = "7fd4676a4361"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_members",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("market_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("market_id", "user_id", name="uq_market_member"),
    )

    op.create_index("ix_market_members_user", "market_members", ["user_id"])
    op.create_index("ix_market_members_market", "market_members", ["market_id"])

    # Backfill: cria vínculo owner para cada loja existente.
    # Usa gen_random_uuid() do Postgres; alternativa para outros bancos:
    # gere via aplicação após migrar.
    op.execute(
        """
        INSERT INTO market_members (id, market_id, user_id, role, is_active, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            m.id,
            m.owner_id,
            'owner',
            TRUE,
            NOW(),
            NOW()
        FROM markets m
        ON CONFLICT (market_id, user_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_market_members_market", table_name="market_members")
    op.drop_index("ix_market_members_user", table_name="market_members")
    op.drop_table("market_members")
