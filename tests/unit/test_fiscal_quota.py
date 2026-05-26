"""
PR2 — Testes do FiscalQuotaService (owner-scoped, exception-based API).

Cobre:
- check_and_reserve: cota disponível, bloqueio, reserva atômica, addon
- consume: incrementa used, decrementa reserved, FIFO addon
- release: decrementa reserved, grava ledger
- get_usage_percentage: percentual correto
- add_addon_credits: idempotência
"""
import os
import sys
import uuid
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
from domain.fiscal import (
    FiscalQuotaExceededError,
    FiscalUsageCounter,
    QuotaReserveResult,
    UsageLedgerEventType,
)

PERIOD = "202506"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_counter(
    included_limit: int = 500,
    addon_limit: int = 0,
    reserved_count: int = 0,
    used_count: int = 0,
) -> FiscalUsageCounter:
    return FiscalUsageCounter(
        owner_id=uuid.uuid4(),
        period_yyyymm=PERIOD,
        included_limit=included_limit,
        addon_limit=addon_limit,
        reserved_count=reserved_count,
        used_count=used_count,
    )


def _make_repo(counter: Optional[FiscalUsageCounter] = None, reserve_ok: bool = True):
    repo = AsyncMock()
    repo.get_or_create_counter.return_value = counter or _make_counter()
    repo.get_counter.return_value = counter or _make_counter()
    repo.increment_reserved.return_value = reserve_ok
    repo.increment_used.return_value = None
    repo.decrement_reserved.return_value = None
    repo.append_ledger.return_value = None
    repo.get_oldest_active_package.return_value = None
    repo.get_ledger_entry_by_idempotency.return_value = None
    repo.increment_addon_limit.return_value = None
    repo.decrement_addon_limit.return_value = None
    repo.decrement_package_remaining.return_value = None
    return repo


def _service(repo=None) -> FiscalQuotaService:
    return FiscalQuotaService(usage_repo=repo or _make_repo())


# ---------------------------------------------------------------------------
# check_and_reserve — sucesso
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reserve_succeeds_returns_quota_reserve_result():
    svc = _service()
    result = await svc.check_and_reserve(uuid.uuid4(), uuid.uuid4(), PERIOD, included_limit=500)
    assert isinstance(result, QuotaReserveResult)


@pytest.mark.asyncio
async def test_reserve_calls_increment_reserved_on_success():
    repo = _make_repo()
    svc = _service(repo)
    await svc.check_and_reserve(uuid.uuid4(), uuid.uuid4(), PERIOD, included_limit=500)
    repo.increment_reserved.assert_called_once()


@pytest.mark.asyncio
async def test_reserve_consuming_addon_false_when_included_available():
    counter = _make_counter(included_limit=500, used_count=100)
    repo = _make_repo(counter=counter)
    svc = _service(repo)
    result = await svc.check_and_reserve(uuid.uuid4(), uuid.uuid4(), PERIOD, included_limit=500)
    assert result.consuming_addon is False


@pytest.mark.asyncio
async def test_reserve_consuming_addon_true_when_only_addon_remains():
    counter = _make_counter(included_limit=100, addon_limit=50, used_count=100)
    repo = _make_repo(counter=counter)
    svc = _service(repo)
    result = await svc.check_and_reserve(uuid.uuid4(), uuid.uuid4(), PERIOD, included_limit=100)
    assert result.consuming_addon is True


# ---------------------------------------------------------------------------
# check_and_reserve — falha
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reserve_raises_when_quota_exhausted():
    counter = _make_counter(included_limit=500, addon_limit=0, reserved_count=500)
    repo = _make_repo(counter=counter)
    svc = _service(repo)
    with pytest.raises(FiscalQuotaExceededError):
        await svc.check_and_reserve(uuid.uuid4(), uuid.uuid4(), PERIOD, included_limit=500)


@pytest.mark.asyncio
async def test_reserve_raises_when_atomic_reservation_fails():
    counter = _make_counter(included_limit=500, reserved_count=100)
    repo = _make_repo(counter=counter, reserve_ok=False)
    svc = _service(repo)
    with pytest.raises(FiscalQuotaExceededError):
        await svc.check_and_reserve(uuid.uuid4(), uuid.uuid4(), PERIOD, included_limit=500)


@pytest.mark.asyncio
async def test_reserve_raises_when_no_included_and_no_addon():
    counter = _make_counter(included_limit=0, addon_limit=0)
    repo = _make_repo(counter=counter)
    svc = _service(repo)
    with pytest.raises(FiscalQuotaExceededError):
        await svc.check_and_reserve(uuid.uuid4(), uuid.uuid4(), PERIOD, included_limit=0)


@pytest.mark.asyncio
async def test_reserve_exception_carries_quota_info():
    counter = _make_counter(included_limit=200, addon_limit=0, used_count=200)
    repo = _make_repo(counter=counter)
    svc = _service(repo)
    with pytest.raises(FiscalQuotaExceededError) as exc_info:
        await svc.check_and_reserve(uuid.uuid4(), uuid.uuid4(), PERIOD, included_limit=200)
    err = exc_info.value
    assert err.included_limit == 200
    assert err.used == 200


# ---------------------------------------------------------------------------
# consume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consume_calls_increment_used():
    repo = _make_repo()
    svc = _service(repo)
    await svc.consume(uuid.uuid4(), uuid.uuid4(), PERIOD, consuming_addon=False)
    repo.increment_used.assert_called_once()


@pytest.mark.asyncio
async def test_consume_calls_decrement_reserved():
    repo = _make_repo()
    svc = _service(repo)
    await svc.consume(uuid.uuid4(), uuid.uuid4(), PERIOD, consuming_addon=False)
    repo.decrement_reserved.assert_called_once()


@pytest.mark.asyncio
async def test_consume_writes_consumed_ledger_entry():
    repo = _make_repo()
    svc = _service(repo)
    await svc.consume(uuid.uuid4(), uuid.uuid4(), PERIOD, consuming_addon=False)
    repo.append_ledger.assert_called_once()
    entry = repo.append_ledger.call_args[0][0]
    assert entry.event_type == UsageLedgerEventType.CONSUMED


@pytest.mark.asyncio
async def test_consume_addon_decrements_package_fifo():
    package = MagicMock()
    package.id = uuid.uuid4()
    repo = _make_repo()
    repo.get_oldest_active_package.return_value = package
    svc = _service(repo)
    await svc.consume(uuid.uuid4(), uuid.uuid4(), PERIOD, consuming_addon=True)
    repo.decrement_package_remaining.assert_called_once_with(package.id)
    repo.decrement_addon_limit.assert_called_once()


@pytest.mark.asyncio
async def test_consume_no_package_decrement_when_not_addon():
    package = MagicMock()
    package.id = uuid.uuid4()
    repo = _make_repo()
    repo.get_oldest_active_package.return_value = package
    svc = _service(repo)
    await svc.consume(uuid.uuid4(), uuid.uuid4(), PERIOD, consuming_addon=False)
    repo.decrement_package_remaining.assert_not_called()


@pytest.mark.asyncio
async def test_consume_tolerates_ledger_failure():
    repo = _make_repo()
    repo.append_ledger.side_effect = Exception("DB down")
    svc = _service(repo)
    await svc.consume(uuid.uuid4(), uuid.uuid4(), PERIOD, consuming_addon=False)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_release_calls_decrement_reserved():
    repo = _make_repo()
    svc = _service(repo)
    await svc.release(uuid.uuid4(), PERIOD)
    repo.decrement_reserved.assert_called_once()


@pytest.mark.asyncio
async def test_release_writes_released_ledger_entry():
    repo = _make_repo()
    svc = _service(repo)
    await svc.release(uuid.uuid4(), PERIOD, reason="test_failure")
    repo.append_ledger.assert_called_once()
    entry = repo.append_ledger.call_args[0][0]
    assert entry.event_type == UsageLedgerEventType.RELEASED
    assert entry.reason == "test_failure"


# ---------------------------------------------------------------------------
# get_usage_percentage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_usage_percentage_zero_when_no_counter():
    repo = AsyncMock()
    repo.get_counter.return_value = None
    svc = _service(repo)
    pct = await svc.get_usage_percentage(uuid.uuid4())
    assert pct == 0.0


@pytest.mark.asyncio
async def test_get_usage_percentage_correct_value():
    counter = _make_counter(included_limit=500, used_count=400)
    repo = _make_repo(counter=counter)
    svc = _service(repo)
    pct = await svc.get_usage_percentage(counter.owner_id)
    assert abs(pct - 80.0) < 0.1


# ---------------------------------------------------------------------------
# add_addon_credits — idempotência
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_addon_credits_idempotent_when_key_exists():
    repo = _make_repo()
    repo.get_ledger_entry_by_idempotency.return_value = MagicMock()
    svc = _service(repo)
    await svc.add_addon_credits(uuid.uuid4(), PERIOD, amount=100, idempotency_key="key-abc")
    repo.increment_addon_limit.assert_not_called()


@pytest.mark.asyncio
async def test_add_addon_credits_calls_increment_when_new():
    repo = _make_repo()
    repo.get_ledger_entry_by_idempotency.return_value = None
    svc = _service(repo)
    await svc.add_addon_credits(uuid.uuid4(), PERIOD, amount=50, idempotency_key="key-xyz")
    repo.increment_addon_limit.assert_called_once()


# ---------------------------------------------------------------------------
# PR6 — add_addon_credits sem counter prévio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_addon_credits_creates_counter_when_missing():
    """get_or_create_counter deve ser chamado para garantir que o counter exista."""
    repo = _make_repo()
    repo.get_ledger_entry_by_idempotency.return_value = None
    svc = _service(repo)
    owner_id = uuid.uuid4()
    await svc.add_addon_credits(owner_id, PERIOD, amount=100, idempotency_key="key-new")
    repo.get_or_create_counter.assert_called_once_with(
        owner_id=owner_id,
        period=PERIOD,
        included_limit=0,
    )
    repo.increment_addon_limit.assert_called_once()


@pytest.mark.asyncio
async def test_add_addon_credits_idempotency_with_no_prior_counter():
    """Se a chave já existe no ledger, não deve chamar get_or_create_counter nem increment."""
    repo = _make_repo()
    repo.get_ledger_entry_by_idempotency.return_value = MagicMock()
    svc = _service(repo)
    await svc.add_addon_credits(uuid.uuid4(), PERIOD, amount=100, idempotency_key="key-dup")
    repo.get_or_create_counter.assert_not_called()
    repo.increment_addon_limit.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_counter_resets_failed_billable_count():
    """Segundo upsert no mesmo período deve zerar failed_billable_count."""
    existing_counter = _make_counter(included_limit=300)
    repo = _make_repo(counter=existing_counter)

    # Simula que o modelo retornado pela query tem failed_billable_count residual.
    model_mock = MagicMock()
    model_mock.failed_billable_count = 7
    model_mock.released_count = 3

    # Verifica diretamente que o campo é zerado ao executar upsert_counter.
    # Como upsert_counter opera no ORM, testamos que o campo foi atribuído.
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalUsageRepository
    from unittest.mock import patch, AsyncMock as AM

    async def fake_execute(q):
        result = MagicMock()
        result.scalars.return_value.first.return_value = model_mock
        return result

    session = MagicMock()
    session.execute = fake_execute
    session.commit = AM(return_value=None)

    usage_repo = SQLAlchemyFiscalUsageRepository(session)
    await usage_repo.upsert_counter(
        owner_id=uuid.uuid4(),
        period=PERIOD,
        included_limit=300,
        addon_limit=0,
    )
    assert model_mock.failed_billable_count == 0
