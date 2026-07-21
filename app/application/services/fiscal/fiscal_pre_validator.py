"""
FiscalPreValidator — valida os dados da venda ANTES de enviar ao provider.

Objetivo: evitar rejeições SEFAZ óbvias, que consomem cota e geram UX ruim.
Não substitui a validação do provider — é uma camada de proteção prévia.

Regras mínimas (Simples Nacional NFC-e):
- NCM obrigatório (8 dígitos)
- CFOP válido para saída ao consumidor
- CSOSN ou CST obrigatório
- Unidade de medida válida
- Pagamentos mapeados para código SEFAZ
- CPF/CNPJ do destinatário com formato correto (quando presente)
- Venda não cancelada
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, List, Mapping, Optional

from domain.shared import BusinessRuleException
from application.services.fiscal.snapshot_integrity import (
    LEGACY_CALCULATION_VERSION,
    fiscal_snapshot_sha256,
)


SEFAZ_PAYMENT_CODES = {
    "dinheiro": "01",
    "cheque": "02",
    "cartao_credito": "03",
    "cartao_debito": "04",
    "credito_loja": "05",
    "vale_alimentacao": "10",
    "vale_refeicao": "11",
    "vale_presente": "12",
    "vale_combustivel": "13",
    "boleto": "15",
    "deposito_bancario": "16",
    "pix": "17",
    "transferencia_bancaria": "18",
    "programa_fidelidade": "19",
    "sem_pagamento": "90",
    "fiado": "99",
    "outros": "99",
}

# Mapeamento Marketfy → método de pagamento Neectify Fiscal
NEECTIFY_PAYMENT_METHODS = {
    "dinheiro": "cash",
    "cheque": "check",
    "cartao_credito": "credit_card",
    "cartao_debito": "debit_card",
    "credito_loja": "store_credit",
    "vale_alimentacao": "food_voucher",
    "vale_refeicao": "meal_voucher",
    "pix": "pix",
    "boleto": "bank_slip",
    "deposito_bancario": "bank_transfer",
    "transferencia_bancaria": "bank_transfer",
    "fiado": "other",
    "outros": "other",
    "sem_pagamento": "no_payment",
}

# Descrição (xPag) enviada quando o método cai em "other" (tPag=99 na SEFAZ).
# Sem isso a SEFAZ rejeita com cStat 441.
NEECTIFY_OTHER_PAYMENT_LABELS = {
    "fiado": "Fiado",
    "outros": "Outros",
}

_NCM_RE = re.compile(r"^\d{8}$")
_CFOP_SAIDA_CONSUMIDOR = re.compile(r"^[56]\d{3}$")  # 5xxx ou 6xxx
_CPF_RE = re.compile(r"^\d{11}$")
_CNPJ_RE = re.compile(r"^\d{14}$")
_HUNDRED = Decimal("100")
_MONEY = Decimal("0.01")
_ZERO = Decimal("0.00")
_TAX_CONTRACT_VERSION = "marketfy.fiscal-tax-snapshot.v1"
_ZERO_VALUE_ICMS_GROUPS = frozenset({"ICMSSN102", "ICMS40"})
_ZERO_VALUE_PIS_GROUPS = frozenset({"PIS07"})
_ZERO_VALUE_COFINS_GROUPS = frozenset({"COFINS07"})


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _decimal(value: Any, *, sku: str, field: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BusinessRuleException(
            f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field={field}; reason=decimal_invalid"
        ) from exc
    if not decimal.is_finite():
        raise BusinessRuleException(
            f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field={field}; reason=decimal_invalid"
        )
    return _money(decimal)


def _rate_decimal(value: Any, *, sku: str, field: str) -> Decimal:
    """Validate approved rate input without applying currency rounding."""
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BusinessRuleException(
            f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field={field}; reason=decimal_invalid"
        ) from exc
    if not decimal.is_finite() or decimal.as_tuple().exponent < -4:
        raise BusinessRuleException(
            f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field={field}; reason=rate_precision_invalid"
        )
    return decimal


def _text(value: Any, *, sku: str, field: str, required: bool = True) -> Optional[str]:
    if value is None:
        if required:
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field={field}; reason=required"
            )
        return None
    value = str(value).strip()
    if not value and required:
        raise BusinessRuleException(
            f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field={field}; reason=required"
        )
    return value or None


def _canonical_decimal(value: Decimal) -> str:
    """The only Decimal-to-string boundary for the Fiscal contract."""
    return f"{_money(value):.2f}"


def _canonical_rate(value: Decimal) -> str:
    return format(value, "f")


@dataclass
class PreValidationResult:
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)


class FiscalPreValidator:
    """Valida dados de venda antes de construir o payload fiscal."""

    fiscal_snapshot_sha256 = staticmethod(fiscal_snapshot_sha256)

    def _verify_snapshot_integrity(self, item, snapshot: Mapping[str, Any], *, sku: str) -> bool:
        """Reject persisted snapshots altered after sale finalisation.

        The v1 calculation marker is the explicit rollout boundary. Legacy
        supported emissions keep their prior validation path, while v1 sales
        are fail-closed for a missing or altered integrity hash.
        """
        if (
            getattr(item, "fiscal_calculation_version", None)
            != LEGACY_CALCULATION_VERSION
        ):
            return False
        stored_hash = getattr(item, "snapshot_sha256", None)
        if not isinstance(stored_hash, str) or stored_hash != fiscal_snapshot_sha256(snapshot):
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=snapshot_sha256; reason=mismatch"
            )
        return True

    def _normalise_item_tax_snapshot(self, item, fiscal_config) -> dict:
        """Validate and serialise one immutable sale-item tax snapshot.

        A provider payload is deliberately built only from this persisted
        snapshot. Product fields and tenant defaults are mutable and therefore
        cannot safely reconstruct an already completed sale.
        """
        sku = str(getattr(item, "product_id", "unknown"))
        snapshot = getattr(item, "fiscal_tax_snapshot", None)
        if not isinstance(snapshot, Mapping):
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_missing; sku={sku}; action=assign_published_tax_rule"
            )

        is_v1_snapshot = self._verify_snapshot_integrity(item, snapshot, sku=sku)

        rule_id = _text(snapshot.get("rule_id"), sku=sku, field="rule_id")
        rule_version = snapshot.get("rule_version")
        item_version = getattr(item, "tax_rule_version_snapshot", None)
        if not isinstance(rule_version, int) or rule_version <= 0:
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=rule_version; reason=invalid"
            )
        if item_version != rule_version:
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=rule_version; reason=inconsistent"
            )

        ncm = re.sub(r"[.\s]", "", _text(snapshot.get("ncm"), sku=sku, field="ncm") or "")
        cfop = _text(snapshot.get("cfop"), sku=sku, field="cfop")
        origin = _text(snapshot.get("origin"), sku=sku, field="origin")
        cest = _text(snapshot.get("cest"), sku=sku, field="cest", required=False)
        if not _NCM_RE.match(ncm):
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=ncm; reason=must_have_8_digits"
            )
        if not _CFOP_SAIDA_CONSUMIDOR.match(cfop or ""):
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=cfop; reason=consumer_output_required"
            )
        if origin not in {str(value) for value in range(9)}:
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=origin; reason=invalid"
            )
        if cest is not None:
            cest = re.sub(r"[.\s]", "", cest)
            if not re.fullmatch(r"\d{7}", cest):
                raise BusinessRuleException(
                    f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=cest; reason=must_have_7_digits"
                )

        icms = snapshot.get("icms")
        pis = snapshot.get("pis")
        cofins = snapshot.get("cofins")
        if not all(isinstance(section, Mapping) for section in (icms, pis, cofins)):
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=tax; reason=sections_required"
            )

        icms_group = _text(icms.get("group"), sku=sku, field="icms.group")
        icms_cst = _text(icms.get("cst"), sku=sku, field="icms.cst", required=False)
        icms_csosn = _text(icms.get("csosn"), sku=sku, field="icms.csosn", required=False)
        if bool(icms_cst) == bool(icms_csosn):
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=icms.cst_or_csosn; reason=exactly_one_required"
            )
        crt = getattr(fiscal_config, "crt", None)
        if isinstance(crt, str) and crt in {"1", "2"} and not icms_group.startswith("ICMSSN"):
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=icms.group; reason=incompatible_with_crt_{crt}"
            )
        if isinstance(crt, str) and crt == "3" and not icms_group.startswith("ICMS"):
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=icms.group; reason=incompatible_with_crt_3"
            )

        icms_money = {
            name: _decimal(icms.get(name), sku=sku, field=f"icms.{name}")
            for name in (
                "own_base", "own_amount", "st_base", "st_amount", "fcp_amount",
            )
        }
        rate_fields = ("reduction_rate", "own_rate", "st_mva_rate", "st_rate", "fcp_rate")
        icms_rates = (
            {name: _rate_decimal(icms.get(name), sku=sku, field=f"icms.{name}") for name in rate_fields}
            if is_v1_snapshot
            else {
                **{
                    name: _decimal(icms.get(name), sku=sku, field=f"icms.{name}")
                    for name in rate_fields if name != "st_mva_rate"
                },
                "st_mva_rate": Decimal("0.00"),
            }
        )
        is_zero_value_icms_group = is_v1_snapshot and icms_group in _ZERO_VALUE_ICMS_GROUPS
        expected_own_base = (
            _ZERO
            if is_zero_value_icms_group
            else _money(
                _decimal(getattr(item, "total", None), sku=sku, field="item.total")
                * (Decimal("1") - icms_rates["reduction_rate"] / _HUNDRED)
            )
        )
        if icms_money["own_base"] != expected_own_base:
            raise BusinessRuleException(
                f"fiscal.snapshot_amount_mismatch; sku={sku}; field=icms.own_base; "
                f"expected={_canonical_decimal(expected_own_base)}"
            )
        expected_item_total = _money(
            _decimal(getattr(item, "quantity", None), sku=sku, field="item.quantity")
            * _decimal(getattr(item, "unit_price", None), sku=sku, field="item.unit_price")
        )
        if expected_item_total != _decimal(getattr(item, "total", None), sku=sku, field="item.total"):
            raise BusinessRuleException(
                f"fiscal.snapshot_amount_mismatch; sku={sku}; field=item.total; "
                f"expected={_canonical_decimal(expected_item_total)}"
            )
        if is_v1_snapshot:
            expected_own_amount = _money(icms_money["own_base"] * icms_rates["own_rate"] / _HUNDRED)
            expected_st_base = _money(
                icms_money["own_base"] * (Decimal("1") + icms_rates["st_mva_rate"] / _HUNDRED)
            ) if icms_rates["st_rate"] > Decimal("0.00") else Decimal("0.00")
            expected_st_amount = max(
                _money(expected_st_base * icms_rates["st_rate"] / _HUNDRED) - expected_own_amount,
                Decimal("0.00"),
            ) if icms_rates["st_rate"] > Decimal("0.00") else Decimal("0.00")
            expected_fcp_base = expected_st_base if icms_rates["st_rate"] > Decimal("0.00") else expected_own_base
            expected_fcp_amount = _money(expected_fcp_base * icms_rates["fcp_rate"] / _HUNDRED)
            expected_icms = {
                "own_amount": expected_own_amount,
                "st_base": expected_st_base,
                "st_amount": expected_st_amount,
                "fcp_amount": expected_fcp_amount,
            }
            for field, expected in expected_icms.items():
                if icms_money[field] != expected:
                    raise BusinessRuleException(
                        f"fiscal.snapshot_amount_mismatch; sku={sku}; field=icms.{field}; "
                        f"expected={_canonical_decimal(expected)}"
                    )
        if (icms_money["st_base"] > Decimal("0.00") or icms_money["st_amount"] > Decimal("0.00")) and not cest:
            raise BusinessRuleException(
                f"sale.fiscal_tax_snapshot_invalid; sku={sku}; field=cest; reason=required_for_st"
            )

        def contribution(name: str, section: Mapping[str, Any]) -> dict:
            group = _text(section.get("group"), sku=sku, field=f"{name}.group")
            cst = _text(section.get("cst"), sku=sku, field=f"{name}.cst")
            numbers = {
                key: _decimal(section.get(key), sku=sku, field=f"{name}.{key}")
                for key in ("base", "amount")
            }
            rate = (
                _rate_decimal(section.get("rate"), sku=sku, field=f"{name}.rate")
                if is_v1_snapshot else _decimal(section.get("rate"), sku=sku, field=f"{name}.rate")
            )
            zero_value_group = is_v1_snapshot and (
                group in _ZERO_VALUE_PIS_GROUPS or group in _ZERO_VALUE_COFINS_GROUPS
            )
            expected_base = (
                _ZERO
                if zero_value_group
                else _decimal(getattr(item, "total", None), sku=sku, field="item.total")
            )
            expected_rate = _ZERO if zero_value_group else rate
            expected_amount = _money(expected_base * expected_rate / _HUNDRED)
            if is_v1_snapshot and numbers["base"] != expected_base:
                raise BusinessRuleException(
                    f"fiscal.snapshot_amount_mismatch; sku={sku}; field={name}.base; "
                    f"expected={_canonical_decimal(expected_base)}"
                )
            if is_v1_snapshot and rate != expected_rate:
                raise BusinessRuleException(
                    f"fiscal.snapshot_amount_mismatch; sku={sku}; field={name}.rate; "
                    f"expected={_canonical_rate(expected_rate)}"
                )
            if is_v1_snapshot and numbers["amount"] != expected_amount:
                raise BusinessRuleException(
                    f"fiscal.snapshot_amount_mismatch; sku={sku}; field={name}.amount; "
                    f"expected={_canonical_decimal(expected_amount)}"
                )
            rate_value = _canonical_rate(rate) if is_v1_snapshot else _canonical_decimal(rate)
            return {"group": group, "cst": cst, **{key: _canonical_decimal(value) for key, value in numbers.items()}, "rate": rate_value}

        return {
            "rule_id": rule_id,
            "rule_version": rule_version,
            "ncm": ncm,
            "cest": cest,
            "cfop": cfop,
            "origin": origin,
            "icms": {
                "group": icms_group,
                "cst": icms_cst,
                "csosn": icms_csosn,
                **{name: _canonical_decimal(value) for name, value in icms_money.items()},
                **{
                    name: _canonical_rate(value) if is_v1_snapshot else _canonical_decimal(value)
                    for name, value in icms_rates.items() if is_v1_snapshot or name != "st_mva_rate"
                },
            },
            "pis": contribution("pis", pis),
            "cofins": contribution("cofins", cofins),
        }

    def _normalise_sale_tax_snapshots(self, sale, fiscal_config) -> list[dict]:
        snapshots = [self._normalise_item_tax_snapshot(item, fiscal_config) for item in sale.items]
        if not all(
            getattr(item, "fiscal_calculation_version", None)
            == LEGACY_CALCULATION_VERSION
            for item in sale.items
        ):
            return snapshots
        products_amount = _money(sum(
            (_decimal(getattr(item, "total", None), sku=str(getattr(item, "product_id", "unknown")), field="item.total") for item in sale.items),
            Decimal("0.00"),
        ))
        discount = _decimal(getattr(sale, "discount", Decimal("0.00")), sku="sale", field="discount")
        acrescimo = _decimal(getattr(sale, "acrescimo", Decimal("0.00")), sku="sale", field="acrescimo")
        expected_sale_total = _money(products_amount - discount + acrescimo)
        actual_sale_total = _decimal(getattr(sale, "total_amount", expected_sale_total), sku="sale", field="total_amount")
        if actual_sale_total != expected_sale_total:
            raise BusinessRuleException(
                f"fiscal.snapshot_amount_mismatch; field=sale.total_amount; expected={_canonical_decimal(expected_sale_total)}"
            )
        return snapshots

    def validate(
        self,
        sale_items: list,
        payments: list,
        customer_cpf: Optional[str],
        sale_status: Optional[str],
        fiscal_config,
        *,
        require_tax_snapshot: bool = True,
    ) -> PreValidationResult:
        result = PreValidationResult(is_valid=True)

        # Venda cancelada não emite
        if sale_status and sale_status in ("cancelada", "canceled", "cancelled"):
            result.add_error("Venda cancelada não pode ter NFC-e emitida.")
            return result

        # Itens
        if not sale_items:
            result.add_error("Venda sem itens não pode gerar NFC-e.")
            return result

        for i, item in enumerate(sale_items, 1):
            item_label = f"Item {i} ({getattr(item, 'product_name', '?')})"
            if require_tax_snapshot:
                try:
                    self._normalise_item_tax_snapshot(item, fiscal_config)
                except BusinessRuleException as exc:
                    result.add_error(f"{item_label}: {exc}")
            else:
                ncm = getattr(item, "ncm_snapshot", None) or getattr(
                    fiscal_config, "default_ncm", None
                )
                if not ncm:
                    result.add_error(f"{item_label}: NCM não configurado.")
                elif not _NCM_RE.match(str(ncm).replace(".", "").replace(" ", "")):
                    result.add_error(
                        f"{item_label}: NCM inválido '{ncm}' (deve ter 8 dígitos)."
                    )

            unit_price = getattr(item, "unit_price", 0)
            quantity = getattr(item, "quantity", 0)
            if float(unit_price) <= 0:
                result.add_error(f"{item_label}: Valor unitário deve ser positivo.")
            if float(quantity) <= 0:
                result.add_error(f"{item_label}: Quantidade deve ser positiva.")

        # Pagamentos
        if not payments:
            result.add_error("Nenhuma forma de pagamento registrada.")
        else:
            for pay in payments:
                method = getattr(pay, "method", None)
                method_val = method.value if hasattr(method, "value") else str(method)
                if method_val not in SEFAZ_PAYMENT_CODES:
                    result.add_warning(
                        f"Método de pagamento '{method_val}' mapeado como 'outros' (99)."
                    )
                amount = getattr(pay, "amount", 0)
                if float(amount) <= 0:
                    result.add_error("Pagamento com valor zero ou negativo.")

        # CPF do consumidor
        if customer_cpf:
            cpf_digits = re.sub(r"\D", "", customer_cpf)
            if not _CPF_RE.match(cpf_digits):
                result.add_error(f"CPF do consumidor inválido: {customer_cpf[:4]}****")

        return result

    def map_payment_code(self, method_str: str) -> str:
        return SEFAZ_PAYMENT_CODES.get(str(method_str).lower(), "99")

    def build_fiscal_payload(
        self,
        sale,
        fiscal_config,
        provider_ref: str,
    ) -> dict:
        """Build the legacy Focus payload from immutable item snapshots only."""
        payment_forms = []
        for pay in sale.payments:
            method_val = pay.method.value if hasattr(pay.method, "value") else str(pay.method)
            payment_forms.append({
                "forma_pagamento": self.map_payment_code(method_val),
                "valor_pagamento": float(pay.amount),
            })

        item_taxes = self._normalise_sale_tax_snapshots(sale, fiscal_config)
        items_payload = []
        for i, (item, tax) in enumerate(zip(sale.items, item_taxes), 1):
            icms = tax["icms"]

            items_payload.append({
                "numero_item": i,
                "codigo_produto": str(item.product_id),
                "descricao": item.product_name[:120],  # Limite Focus
                "codigo_ncm": tax["ncm"],
                "cfop": tax["cfop"],
                "valor_unitario_comercial": _canonical_decimal(_decimal(item.unit_price, sku=str(item.product_id), field="item.unit_price")),
                "valor_unitario_tributavel": _canonical_decimal(_decimal(item.unit_price, sku=str(item.product_id), field="item.unit_price")),
                "quantidade_comercial": f"{Decimal(str(item.quantity)):.4f}",
                "quantidade_tributavel": f"{Decimal(str(item.quantity)):.4f}",
                "valor_bruto": _canonical_decimal(_decimal(item.total, sku=str(item.product_id), field="item.total")),
                "unidade_comercial": "UN",
                "unidade_tributavel": "UN",
                "icms_origem": tax["origin"],
                **({"icms_csosn": icms["csosn"]} if icms["csosn"] else {}),
                **({"icms_cst": icms["cst"]} if icms["cst"] else {}),
                "pis_cst": tax["pis"]["cst"],
                "cofins_cst": tax["cofins"]["cst"],
                "inclui_no_total": "1",
            })

        payload = {
            "ref": provider_ref,
            "data_emissao": sale.created_at.isoformat(),
            "natureza_operacao": "VENDA AO CONSUMIDOR FINAL",
            "tipo_documento": "1",
            "finalidade_emissao": "1",
            "consumidor_final": "1",
            "presenca_comprador": "1",
            "local_destino": "1",
            "items": items_payload,
            "formas_pagamento": payment_forms,
        }

        if hasattr(sale, "customer_cpf") and sale.customer_cpf:
            cpf_digits = re.sub(r"\D", "", sale.customer_cpf)
            if len(cpf_digits) == 11:
                payload["cpf_destinatario"] = cpf_digits

        return payload

    def map_neectify_payment_method(self, method_str: str) -> str:
        return NEECTIFY_PAYMENT_METHODS.get(str(method_str).lower(), "other")

    def build_neectify_payload(
        self,
        sale,
        fiscal_config,
        issuer_id: str,
        provider_ref: Optional[str] = None,
    ) -> dict:
        """Build the versioned item-tax contract for ``POST /v1/nfce``."""
        environment = getattr(fiscal_config, "environment", None)
        env_value = environment.value if hasattr(environment, "value") else str(environment or "homologacao")
        neectify_env = "production" if env_value in ("producao", "production") else "homologation"

        payments_payload = []
        for pay in sale.payments:
            method_val = pay.method.value if hasattr(pay.method, "value") else str(pay.method)
            neectify_method = self.map_neectify_payment_method(method_val)
            entry: dict = {
                "method": neectify_method,
                "amount": _canonical_decimal(_decimal(pay.amount, sku="payment", field="amount")),
            }
            # tPag=99 (Outros) exige descrição (xPag) ou a SEFAZ rejeita (cStat 441).
            if neectify_method == "other":
                entry["description"] = NEECTIFY_OTHER_PAYMENT_LABELS.get(
                    str(method_val).lower(), "Outros"
                )
            payments_payload.append(entry)

        item_taxes = self._normalise_sale_tax_snapshots(sale, fiscal_config)
        items_payload = []
        for item, tax in zip(sale.items, item_taxes):
            product_name = getattr(item, "product_name_snapshot", None) or getattr(item, "product_name", None) or str(item.product_id)
            items_payload.append({
                "sku": str(item.product_id),
                "description": product_name[:120],
                "quantity": f"{Decimal(str(item.quantity)):.4f}",
                "unit": "UN",
                "unit_amount": _canonical_decimal(_decimal(item.unit_price, sku=str(item.product_id), field="item.unit_price")),
                "total_amount": _canonical_decimal(_decimal(item.total, sku=str(item.product_id), field="item.total")),
                "cfop": tax["cfop"],
                "ncm": tax["ncm"],
                "cest": tax["cest"],
                "origin": tax["origin"],
                "tax": tax,
            })

        def total(field: str) -> str:
            return _canonical_decimal(sum(
                (Decimal(item["tax"]["icms"][field]) for item in items_payload),
                Decimal("0.00"),
            ))

        occurred_at = sale.created_at.isoformat()
        if occurred_at.endswith("+00:00"):
            occurred_at = f"{occurred_at[:-6]}Z"
        elif "T" in occurred_at and "+" not in occurred_at[10:] and not occurred_at.endswith("Z"):
            occurred_at = f"{occurred_at}Z"

        payload: dict = {
            "contract_version": _TAX_CONTRACT_VERSION,
            "issuer_id": issuer_id,
            "environment": neectify_env,
            "external_id": str(sale.id),
            "correlation": {
                "sale_id": str(sale.id),
                "market_id": str(getattr(sale, "market_id", "")),
                "provider_ref": provider_ref or str(sale.id),
            },
            "sale": {
                "occurred_at": occurred_at,
            },
            "items": items_payload,
            "payments": payments_payload,
            "totals": {
                "products_amount": _canonical_decimal(sum(
                    (Decimal(item["total_amount"]) for item in items_payload), Decimal("0.00")
                )),
                "icms_base": total("own_base"),
                "icms_amount": total("own_amount"),
                "icms_st_base": total("st_base"),
                "icms_st_amount": total("st_amount"),
                "fcp_amount": total("fcp_amount"),
                "pis_amount": _canonical_decimal(sum(
                    (Decimal(item["tax"]["pis"]["amount"]) for item in items_payload), Decimal("0.00")
                )),
                "cofins_amount": _canonical_decimal(sum(
                    (Decimal(item["tax"]["cofins"]["amount"]) for item in items_payload), Decimal("0.00")
                )),
            },
        }

        if hasattr(sale, "customer_cpf") and sale.customer_cpf:
            cpf_digits = re.sub(r"\D", "", sale.customer_cpf)
            if len(cpf_digits) == 11:
                payload["consumer"] = {"document": cpf_digits}

        return payload

    def build_legacy_neectify_payload(
        self,
        sale,
        fiscal_config,
        issuer_id: str,
        provider_ref: Optional[str] = None,
    ) -> dict:
        """Build the pre-rollout contract without requiring item-tax snapshots."""
        environment = getattr(fiscal_config, "environment", None)
        env_value = environment.value if hasattr(environment, "value") else str(environment or "homologacao")
        neectify_env = "production" if env_value in ("producao", "production") else "homologation"
        payments = []
        for pay in sale.payments:
            method = pay.method.value if hasattr(pay.method, "value") else str(pay.method)
            entry = {"method": self.map_neectify_payment_method(method), "amount": f"{float(pay.amount):.2f}"}
            if entry["method"] == "other":
                entry["description"] = NEECTIFY_OTHER_PAYMENT_LABELS.get(method.lower(), "Outros")
            payments.append(entry)

        items = []
        for item in sale.items:
            ncm = getattr(item, "ncm_snapshot", None) or getattr(fiscal_config, "default_ncm", "")
            items.append({
                "sku": str(item.product_id),
                "description": (getattr(item, "product_name_snapshot", None) or item.product_name)[:120],
                "quantity": f"{float(item.quantity):.4f}",
                "unit": "UN",
                "unit_amount": f"{float(item.unit_price):.2f}",
                "total_amount": f"{float(item.total):.2f}",
                "cfop": getattr(fiscal_config, "default_cfop", "5102"),
                "ncm": str(ncm).replace(".", "").replace(" ", ""),
            })

        occurred_at = sale.created_at.isoformat().replace("+00:00", "Z")
        if "T" in occurred_at and not occurred_at.endswith("Z") and "+" not in occurred_at[10:]:
            occurred_at = f"{occurred_at}Z"
        payload = {
            "issuer_id": issuer_id,
            "environment": neectify_env,
            "external_id": str(sale.id),
            "correlation": {"sale_id": str(sale.id), "market_id": str(getattr(sale, "market_id", "")), "provider_ref": provider_ref or str(sale.id)},
            "sale": {"occurred_at": occurred_at},
            "items": items,
            "payments": payments,
            "fiscal_options": {},
            "metadata": {},
        }
        if getattr(sale, "customer_cpf", None):
            cpf_digits = re.sub(r"\D", "", sale.customer_cpf)
            if len(cpf_digits) == 11:
                payload["consumer"] = {"document": cpf_digits}
        return payload
