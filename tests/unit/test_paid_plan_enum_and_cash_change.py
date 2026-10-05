"""Regressões do relatório E2E: Enum PlanType no gating, CPF e troco no caixa."""

from types import SimpleNamespace

from application.services.plan_access_service import _is_paid_plan
from domain.identity import PlanType
from domain.shared import is_valid_cpf


def test_paid_plan_check_accepts_real_enum_and_raw_string():
    assert _is_paid_plan(SimpleNamespace(type=PlanType.PAID))
    assert _is_paid_plan(SimpleNamespace(type=PlanType.TRIAL))
    assert _is_paid_plan(SimpleNamespace(type="pago"))
    assert not _is_paid_plan(SimpleNamespace(type=PlanType.FREE))
    assert not _is_paid_plan(SimpleNamespace(type=None))


def test_cpf_validation_checks_digits_and_rejects_repeated_sequences():
    assert is_valid_cpf("529.982.247-25")
    assert not is_valid_cpf("11111111111")
    assert not is_valid_cpf("12345678900")
    assert not is_valid_cpf("123")
