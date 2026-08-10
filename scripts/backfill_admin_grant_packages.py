"""Backfill de grants administrativos legados.

O endpoint antigo POST /admin/fiscal/credits/grant incrementava addon_limit sem
criar linha em fiscal_emission_packages. Sem pacote lastreando, o reset mensal
(cron dia 1, 00:05 UTC) apaga esses créditos.

Este script encontra os ledgers órfãos e cria os pacotes correspondentes.

Uso (a partir de marketfy/backend):
    python scripts/backfill_admin_grant_packages.py            # dry-run
    python scripts/backfill_admin_grant_packages.py --apply    # aplica

PRECISA RODAR ANTES DO PRÓXIMO DIA 1º. Depois disso o addon_limit já terá sido
zerado e o remaining correto não é mais derivável do ledger.
"""
import argparse
import asyncio
import os
import sys
from datetime import timedelta

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from sqlalchemy import select

from domain.fiscal import PACKAGE_TYPE_ADMIN_GRANT
from infra.database.models import FiscalUsageLedgerModel
from infra.database.setup import async_session_factory
from infra.repositories.fiscal_repo import SQLAlchemyFiscalUsageRepository

VALID_DAYS = 365


async def find_orphan_grants(session):
    """Ledgers addon_purchased sem pacote lastreando."""
    result = await session.execute(
        select(FiscalUsageLedgerModel)
        .where(FiscalUsageLedgerModel.event_type == "addon_purchased")
        .order_by(FiscalUsageLedgerModel.created_at)
    )
    orphans = []
    for entry in result.scalars().all():
        key = entry.idempotency_key or ""
        if key.startswith("bc_payment:") or key.startswith("admin_grant:"):
            continue
        orphans.append(entry)
    return orphans


async def main(apply: bool) -> int:
    async with async_session_factory() as session:
        orphans = await find_orphan_grants(session)

        if not orphans:
            print("Nenhum grant legado encontrado. Nada a fazer.")
            return 0

        print(f"{len(orphans)} grant(s) legado(s) encontrado(s):")
        for entry in orphans:
            print(
                f"  owner={entry.owner_id} periodo={entry.period_yyyymm} "
                f"qtd={entry.quantity} chave={entry.idempotency_key} em={entry.created_at}"
            )

        if not apply:
            print("\nDry-run. Rode com --apply para criar os pacotes.")
            return 0

        repo = SQLAlchemyFiscalUsageRepository(session)
        created = 0
        for entry in orphans:
            valid_from = entry.created_at
            await repo.create_grant_package(
                owner_id=entry.owner_id,
                quantity=entry.quantity,
                valid_from=valid_from,
                valid_until=valid_from + timedelta(days=VALID_DAYS),
                grant_reason_code="migration",
                grant_note=f"backfill do grant legado (ledger {entry.id})",
                granted_by_id=None,
                idempotency_key=f"backfill_grant:{entry.id}",
                commit=False,
            )
            created += 1
        await session.commit()
        print(f"\n{created} pacote(s) criado(s) com validade de {VALID_DAYS} dias.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="aplica as mudanças")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.apply)))
