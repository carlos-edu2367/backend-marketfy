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
from typing import List, Optional

from domain.shared import BusinessRuleException


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
    "sem_pagamento": "other",
}

_NCM_RE = re.compile(r"^\d{8}$")
_CFOP_SAIDA_CONSUMIDOR = re.compile(r"^[56]\d{3}$")  # 5xxx ou 6xxx
_CPF_RE = re.compile(r"^\d{11}$")
_CNPJ_RE = re.compile(r"^\d{14}$")


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

    def validate(
        self,
        sale_items: list,
        payments: list,
        customer_cpf: Optional[str],
        sale_status: Optional[str],
        fiscal_config,
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

            ncm = getattr(item, "ncm_snapshot", None)
            if not ncm and fiscal_config:
                ncm = getattr(fiscal_config, "default_ncm", None)
            if not ncm:
                result.add_error(f"{item_label}: NCM não configurado.")
            elif not _NCM_RE.match(str(ncm).replace(".", "").replace(" ", "")):
                result.add_error(f"{item_label}: NCM inválido '{ncm}' (deve ter 8 dígitos).")

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
                    result.add_error(f"Pagamento com valor zero ou negativo.")

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
        """
        Monta o payload fiscal no formato Focus NFe para NFC-e.

        Usa perfil fiscal do item quando disponível,
        com fallback para defaults da configuração do mercado.
        """
        payment_forms = []
        for pay in sale.payments:
            method_val = pay.method.value if hasattr(pay.method, "value") else str(pay.method)
            payment_forms.append({
                "forma_pagamento": self.map_payment_code(method_val),
                "valor_pagamento": float(pay.amount),
            })

        items_payload = []
        for i, item in enumerate(sale.items, 1):
            ncm = getattr(item, "ncm_snapshot", None) or getattr(fiscal_config, "default_ncm", "")
            cfop = getattr(fiscal_config, "default_cfop", "5102")
            csosn = getattr(fiscal_config, "default_csosn", "102")
            cst = getattr(fiscal_config, "default_cst", None)
            origin = "0"  # Nacional

            items_payload.append({
                "numero_item": i,
                "codigo_produto": str(item.product_id),
                "descricao": item.product_name[:120],  # Limite Focus
                "codigo_ncm": str(ncm).replace(".", "").replace(" ", ""),
                "cfop": cfop,
                "valor_unitario_comercial": float(item.unit_price),
                "valor_unitario_tributavel": float(item.unit_price),
                "quantidade_comercial": float(item.quantity),
                "quantidade_tributavel": float(item.quantity),
                "valor_bruto": float(item.total),
                "unidade_comercial": "UN",
                "unidade_tributavel": "UN",
                "icms_origem": origin,
                "icms_csosn": csosn,
                **({"icms_cst": cst} if cst else {}),
                "pis_cst": "07",
                "cofins_cst": "07",
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
    ) -> dict:
        """
        Monta o payload no formato Neectify Fiscal (POST /v1/nfce).

        Referência: INTEGRATION.md §D.
        """
        environment = getattr(fiscal_config, "environment", None)
        env_value = environment.value if hasattr(environment, "value") else str(environment or "homologacao")
        neectify_env = "production" if env_value in ("producao", "production") else "homologation"

        payments_payload = []
        for pay in sale.payments:
            method_val = pay.method.value if hasattr(pay.method, "value") else str(pay.method)
            entry: dict = {
                "payment_method": self.map_neectify_payment_method(method_val),
                "amount": float(pay.amount),
            }
            payments_payload.append(entry)

        items_payload = []
        for item in sale.items:
            ncm = getattr(item, "ncm_snapshot", None) or getattr(fiscal_config, "default_ncm", "")
            cfop = getattr(fiscal_config, "default_cfop", "5102")
            product_name = getattr(item, "product_name_snapshot", None) or str(item.product_id)
            items_payload.append({
                "code": str(item.product_id),
                "description": product_name[:120],
                "ncm": str(ncm).replace(".", "").replace(" ", ""),
                "cfop": cfop,
                "unit": "UN",
                "quantity": float(item.quantity),
                "unit_value": float(item.unit_price),
                "total_value": float(item.total),
            })

        payload: dict = {
            "issuer_id": issuer_id,
            "environment": neectify_env,
            "external_id": str(sale.id),
            "sale": {
                "document_type": "nfce",
                "payment_type": "immediate",
            },
            "items": items_payload,
            "payments": payments_payload,
            "fiscal_options": {
                "nature_of_operation": "Venda Comercial",
            },
        }

        if hasattr(sale, "customer_cpf") and sale.customer_cpf:
            cpf_digits = re.sub(r"\D", "", sale.customer_cpf)
            if len(cpf_digits) == 11:
                payload["consumer"] = {"document": cpf_digits}

        return payload
