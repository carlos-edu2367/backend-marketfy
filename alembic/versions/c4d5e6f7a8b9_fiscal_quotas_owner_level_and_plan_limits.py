"""fiscal_quotas_owner_level_and_plan_limits

Revision ID: c4d5e6f7a8b9
Revises: a1b2c3d4e5f6
Create Date: 2026-05-26 00:00:00.000000
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. fiscal_monthly_limit no PlanModel
    op.add_column(
        "plans",
        sa.Column("fiscal_monthly_limit", sa.Integer(), nullable=False, server_default="0"),
    )

    # 2. market_id nullable no FiscalUsageLedgerModel
    op.alter_column("fiscal_usage_ledger", "market_id", nullable=True)

    # 3. market_id nullable no FiscalUsageCounterModel (remove FK, mantém coluna)
    op.drop_constraint("uq_fuc_owner_market_period", "fiscal_usage_counters", type_="unique")

    # Tenta remover FK se existir (pode não existir dependendo do banco de dados de teste)
    try:
        op.drop_constraint("fiscal_usage_counters_market_id_fkey", "fiscal_usage_counters", type_="foreignkey")
    except Exception:
        pass

    op.alter_column("fiscal_usage_counters", "market_id", nullable=True)

    # 4. Nova unique constraint owner-level
    op.create_unique_constraint(
        "uq_fuc_owner_period", "fiscal_usage_counters", ["owner_id", "period_yyyymm"]
    )

    # 5. Índice já existe (ix_fuc_owner_period foi criado na migration anterior)
    # Apenas garantir que está correto — sem recriar se já existe
    # op.create_index("ix_fuc_owner_period", "fiscal_usage_counters", ["owner_id", "period_yyyymm"])

    # 6. market_id (nullable, sem FK) no FiscalEmissionPackageModel
    op.add_column(
        "fiscal_emission_packages",
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_fep_owner_status", "fiscal_emission_packages", ["owner_id", "payment_status"])

    # 7. Atualizar limites dos planos existentes
    op.execute("UPDATE plans SET fiscal_monthly_limit = 50  WHERE type IN ('cortesia', 'trial')")
    op.execute("UPDATE plans SET fiscal_monthly_limit = 200 WHERE name ILIKE '%basico%' OR name ILIKE '%básico%'")
    op.execute("UPDATE plans SET fiscal_monthly_limit = 500 WHERE name ILIKE '%pro%'")


def downgrade() -> None:
    op.drop_index("ix_fep_owner_status", table_name="fiscal_emission_packages")
    op.drop_column("fiscal_emission_packages", "market_id")

    op.drop_constraint("uq_fuc_owner_period", "fiscal_usage_counters", type_="unique")
    op.alter_column("fiscal_usage_counters", "market_id", nullable=False)
    op.create_unique_constraint(
        "uq_fuc_owner_market_period", "fiscal_usage_counters", ["owner_id", "market_id", "period_yyyymm"]
    )

    op.alter_column("fiscal_usage_ledger", "market_id", nullable=False)
    op.drop_column("plans", "fiscal_monthly_limit")
