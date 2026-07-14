"""
PR4 — Testes do FiscalPreValidator.

Cobre:
- NCM obrigatório e formato (8 dígitos)
- Preço e quantidade positivos
- Mapeamento de métodos de pagamento para código SEFAZ
- CPF do consumidor — formato
- Venda cancelada não emite
- build_fiscal_payload — estrutura mínima
"""
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_pre_validator import (
    FiscalPreValidator,
    SEFAZ_PAYMENT_CODES,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _tax_snapshot() -> dict:
    return {
        "rule_id": str(uuid.uuid4()), "rule_version": 1,
        "ncm": "22021000", "cest": None, "cfop": "5102", "origin": "0",
        "icms": {
            "group": "ICMSSN102", "cst": None, "csosn": "102",
            "own_base": Decimal("10.00"), "reduction_rate": Decimal("0.00"),
            "own_rate": Decimal("0.00"), "own_amount": Decimal("0.00"),
            "st_base": Decimal("0.00"), "st_rate": Decimal("0.00"),
            "st_amount": Decimal("0.00"), "fcp_rate": Decimal("0.00"),
            "fcp_amount": Decimal("0.00"),
        },
        "pis": {"group": "PIS07", "cst": "07", "base": Decimal("10.00"), "rate": Decimal("0.00"), "amount": Decimal("0.00")},
        "cofins": {"group": "COFINS07", "cst": "07", "base": Decimal("10.00"), "rate": Decimal("0.00"), "amount": Decimal("0.00")},
    }

@dataclass
class FakeItem:
    product_id: uuid.UUID = field(default_factory=uuid.uuid4)
    product_name: str = "Produto Teste"
    ncm_snapshot: Optional[str] = "22021000"  # NCM válido
    unit_price: Decimal = Decimal("10.00")
    quantity: Decimal = Decimal("1")
    total: Decimal = Decimal("10.00")
    tax_rule_version_snapshot: int | None = 1
    fiscal_tax_snapshot: Optional[dict] = field(default_factory=_tax_snapshot)


@dataclass
class FakePayment:
    method: str = "dinheiro"
    amount: Decimal = Decimal("10.00")


@dataclass
class FakeConfig:
    default_ncm: Optional[str] = "22021000"
    default_cfop: str = "5102"
    default_csosn: Optional[str] = "102"
    default_cst: Optional[str] = None


@dataclass
class FakeSale:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    items: List[FakeItem] = field(default_factory=lambda: [FakeItem()])
    payments: List[FakePayment] = field(default_factory=lambda: [FakePayment()])
    customer_cpf: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


def _validator() -> FiscalPreValidator:
    return FiscalPreValidator()


# ---------------------------------------------------------------------------
# Validate — casos válidos
# ---------------------------------------------------------------------------

def test_valid_sale_passes():
    v = _validator()
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status="completed",
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is True
    assert result.errors == []


def test_valid_with_cpf_passes():
    v = _validator()
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[FakePayment()],
        customer_cpf="123.456.789-00",
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# NCM
# ---------------------------------------------------------------------------

def test_missing_ncm_no_default_fails():
    v = _validator()
    item = FakeItem(ncm_snapshot=None)
    item.fiscal_tax_snapshot["ncm"] = None
    result = v.validate(
        sale_items=[item],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(default_ncm=None),
    )
    assert result.is_valid is False
    assert any("field=ncm" in e for e in result.errors)


def test_item_without_snapshot_does_not_use_config_default():
    v = _validator()
    item = FakeItem(ncm_snapshot=None, fiscal_tax_snapshot=None)
    result = v.validate(
        sale_items=[item],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(default_ncm="22021000"),
    )
    assert result.is_valid is False
    assert any("fiscal_tax_snapshot_missing" in error for error in result.errors)


def test_invalid_ncm_7_digits_fails():
    v = _validator()
    item = FakeItem(ncm_snapshot="2202100")  # 7 dígitos
    item.fiscal_tax_snapshot["ncm"] = "2202100"
    result = v.validate(
        sale_items=[item],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(default_ncm=None),
    )
    assert result.is_valid is False
    assert any("field=ncm" in e for e in result.errors)


def test_invalid_ncm_letters_fails():
    v = _validator()
    item = FakeItem(ncm_snapshot="2202100A")
    item.fiscal_tax_snapshot["ncm"] = "2202100A"
    result = v.validate(
        sale_items=[item],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(default_ncm=None),
    )
    assert result.is_valid is False


def test_valid_ncm_with_dots_passes():
    v = _validator()
    item = FakeItem(ncm_snapshot="2202.10.00")  # Pontuação deve ser ignorada
    item.fiscal_tax_snapshot["ncm"] = "2202.10.00"
    result = v.validate(
        sale_items=[item],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(default_ncm=None),
    )
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# Preço e quantidade
# ---------------------------------------------------------------------------

def test_zero_unit_price_fails():
    v = _validator()
    item = FakeItem(unit_price=Decimal("0"))
    result = v.validate(
        sale_items=[item],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is False
    assert any("Valor unitário" in e for e in result.errors)


def test_negative_quantity_fails():
    v = _validator()
    item = FakeItem(quantity=Decimal("-1"))
    result = v.validate(
        sale_items=[item],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is False
    assert any("Quantidade" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Pagamentos
# ---------------------------------------------------------------------------

def test_no_payments_fails():
    v = _validator()
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is False
    assert any("pagamento" in e.lower() for e in result.errors)


def test_zero_payment_amount_fails():
    v = _validator()
    pay = FakePayment(amount=Decimal("0"))
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[pay],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is False


def test_unknown_payment_method_adds_warning_not_error():
    v = _validator()
    pay = FakePayment(method="criptomoeda")
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[pay],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is True  # warning, not error
    assert any("99" in w or "outros" in w.lower() for w in result.warnings)


def test_payment_code_mapping_known_methods():
    v = _validator()
    assert v.map_payment_code("dinheiro") == "01"
    assert v.map_payment_code("cartao_credito") == "03"
    assert v.map_payment_code("pix") == "17"
    assert v.map_payment_code("fiado") == "99"


def test_payment_code_mapping_unknown_returns_99():
    v = _validator()
    assert v.map_payment_code("criptomoeda") == "99"
    assert v.map_payment_code("qualquer_coisa") == "99"


def test_all_documented_methods_are_in_map():
    expected_keys = [
        "dinheiro", "cheque", "cartao_credito", "cartao_debito", "pix",
        "boleto", "vale_alimentacao", "vale_refeicao", "sem_pagamento", "fiado",
    ]
    for key in expected_keys:
        assert key in SEFAZ_PAYMENT_CODES, f"'{key}' deve estar em SEFAZ_PAYMENT_CODES"


# ---------------------------------------------------------------------------
# CPF do consumidor
# ---------------------------------------------------------------------------

def test_invalid_cpf_10_digits_fails():
    v = _validator()
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[FakePayment()],
        customer_cpf="1234567890",  # 10 dígitos
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is False
    assert any("CPF" in e for e in result.errors)


def test_valid_cpf_11_digits_passes():
    v = _validator()
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[FakePayment()],
        customer_cpf="12345678900",  # 11 dígitos sem formatação
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is True


def test_cpf_error_masks_digits():
    """Erros de CPF não devem expor número completo."""
    v = _validator()
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[FakePayment()],
        customer_cpf="1234567890",
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    for err in result.errors:
        if "CPF" in err:
            # Não deve conter os últimos 7 dígitos completos
            assert "34567890" not in err


# ---------------------------------------------------------------------------
# Venda cancelada
# ---------------------------------------------------------------------------

def test_canceled_sale_fails_immediately():
    v = _validator()
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status="cancelada",
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is False
    assert any("cancelada" in e.lower() for e in result.errors)


def test_canceled_english_variant_also_fails():
    v = _validator()
    result = v.validate(
        sale_items=[FakeItem()],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status="canceled",
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is False


def test_empty_items_fails():
    v = _validator()
    result = v.validate(
        sale_items=[],
        payments=[FakePayment()],
        customer_cpf=None,
        sale_status=None,
        fiscal_config=FakeConfig(),
    )
    assert result.is_valid is False
    assert any("sem itens" in e.lower() or "itens" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# build_fiscal_payload
# ---------------------------------------------------------------------------

def test_build_fiscal_payload_structure():
    v = _validator()
    sale = FakeSale()
    cfg = FakeConfig()
    ref = "marketfy-abc-def"
    payload = v.build_fiscal_payload(sale, cfg, ref)

    assert payload["ref"] == ref
    assert "items" in payload
    assert "formas_pagamento" in payload
    assert payload["tipo_documento"] == "1"
    assert payload["consumidor_final"] == "1"
    assert len(payload["items"]) == 1
    assert len(payload["formas_pagamento"]) == 1


def test_build_fiscal_payload_item_fields():
    v = _validator()
    sale = FakeSale()
    cfg = FakeConfig()
    payload = v.build_fiscal_payload(sale, cfg, "ref-test")

    item = payload["items"][0]
    assert item["codigo_ncm"] == "22021000"
    assert item["cfop"] == "5102"
    assert item["icms_csosn"] == "102"
    assert item["numero_item"] == 1
    assert item["pis_cst"] == "07"
    assert item["cofins_cst"] == "07"


def test_build_fiscal_payload_includes_cpf_when_present():
    v = _validator()
    sale = FakeSale(customer_cpf="12345678900")
    cfg = FakeConfig()
    payload = v.build_fiscal_payload(sale, cfg, "ref-test")
    assert payload.get("cpf_destinatario") == "12345678900"


def test_build_fiscal_payload_excludes_cpf_when_absent():
    v = _validator()
    sale = FakeSale(customer_cpf=None)
    cfg = FakeConfig()
    payload = v.build_fiscal_payload(sale, cfg, "ref-test")
    assert "cpf_destinatario" not in payload


def test_build_fiscal_payload_payment_maps_to_sefaz_code():
    v = _validator()
    sale = FakeSale(payments=[FakePayment(method="pix", amount=Decimal("10"))])
    cfg = FakeConfig()
    payload = v.build_fiscal_payload(sale, cfg, "ref-test")
    assert payload["formas_pagamento"][0]["forma_pagamento"] == "17"


def test_build_payload_description_truncated_at_120():
    long_name = "A" * 200
    v = _validator()
    sale = FakeSale(items=[FakeItem(product_name=long_name)])
    cfg = FakeConfig()
    payload = v.build_fiscal_payload(sale, cfg, "ref-test")
    assert len(payload["items"][0]["descricao"]) <= 120
