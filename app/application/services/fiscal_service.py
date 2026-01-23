import uuid
import os
from typing import Optional, Dict
from domain.fiscal import FiscalConfig, Invoice, InvoiceStatus, FiscalEnvironment
from domain.interfaces import FiscalRepositoryInterface, MarketRepositoryInterface, SaleRepositoryInterface, FiscalProviderInterface
from domain.shared import BusinessRuleException
from application.dtos import FiscalConfigCreateDTO, FiscalConfigResponseDTO, InvoiceResponseDTO
from infra.providers.focus_nfe_provider import FocusNFeProvider

class FiscalService:
    def __init__(
        self, 
        fiscal_repo: FiscalRepositoryInterface,
        market_repo: MarketRepositoryInterface,
        sale_repo: SaleRepositoryInterface,
        provider: FiscalProviderInterface = None 
    ):
        self.fiscal_repo = fiscal_repo
        self.market_repo = market_repo
        self.sale_repo = sale_repo
        self.provider = provider or FocusNFeProvider()

    async def save_config(self, market_id: uuid.UUID, dto: FiscalConfigCreateDTO, cert_file_path: str = None) -> FiscalConfigResponseDTO:
        config = await self.fiscal_repo.get_config(market_id)
        
        if not config:
            config = FiscalConfig(
                market_id=market_id,
                certificate_path="",
                certificate_password="",
                csc_token="",
                csc_id="",
                environment=FiscalEnvironment(dto.environment)
            )

        config.csc_token = dto.csc_token
        config.csc_id = dto.csc_id
        config.environment = FiscalEnvironment(dto.environment)
        if cert_file_path:
            config.certificate_path = cert_file_path
        if dto.certificate_password:
            config.certificate_password = dto.certificate_password
            
        config.default_ncm = dto.default_ncm
        
        await self.fiscal_repo.save_config(config)
        
        return FiscalConfigResponseDTO(
            environment=config.environment.value,
            csc_id=config.csc_id,
            has_certificate=bool(config.certificate_path),
            default_ncm=config.default_ncm
        )
    
    async def get_config(self, market_id: uuid.UUID) -> Optional[FiscalConfig]:
        return await self.fiscal_repo.get_config(market_id)

    async def emit_invoice(self, market_id: uuid.UUID, sale_id: uuid.UUID) -> InvoiceResponseDTO:
        # 1. Validações Básicas
        config = await self.fiscal_repo.get_config(market_id)
        if not config:
            raise BusinessRuleException("Emissão Fiscal não configurada para esta loja.")
        
        config.validate_for_emission()
        
        sale = await self.sale_repo.get_by_id(sale_id)
        if not sale:
            raise BusinessRuleException("Venda não encontrada.")

        # 2. Verifica se já existe nota
        existing_invoice = await self.fiscal_repo.get_invoice_by_sale(sale_id)
        if existing_invoice and existing_invoice.status == InvoiceStatus.AUTHORIZED:
            return self._map_invoice_dto(existing_invoice)

        if not existing_invoice:
            existing_invoice = Invoice(market_id=market_id, sale_id=sale_id)
        
        # 3. Monta Payload JSON para o Provider
        # Mapeamento de Pagamentos SEFAZ
        payment_forms = []
        for pay in sale.payments:
            code = self._map_payment_method(pay.method.value)
            payment_forms.append({
                "forma_pagamento": code,
                "valor_pagamento": float(pay.amount)
            })

        items_payload = []
        for i, item in enumerate(sale.items):
            # Fallback para NCM padrão se o produto não tiver
            ncm = item.ncm_snapshot or config.default_ncm
            if not ncm:
                raise BusinessRuleException(f"Produto '{item.product_name}' sem NCM e sem padrão configurado.")

            items_payload.append({
                "numero_item": i + 1,
                "codigo_produto": str(item.product_id), # Ou código interno se disponível
                "descricao": item.product_name,
                "codigo_ncm": ncm,
                "cfop": config.default_cfop, 
                "valor_unitario": float(item.unit_price),
                "quantidade": float(item.quantity),
                "valor_total_bruto": float(item.total),
                # TRIBUTAÇÃO SIMPLIFICADA (SIMPLES NACIONAL)
                # Em produção real, isso deve vir do cadastro do produto (ICMS, PIS, COFINS)
                "icms_origem": "0", # 0 - Nacional
                "icms_csosn": "102", # 102 - Tributada sem permissão de crédito (Venda Consumidor)
                "unidade_comercial": "UN"
            })

        sale_data = {
            "ref": str(sale.id),
            "data_emissao": sale.created_at.isoformat(),
            "natureza_operacao": "VENDA AO CONSUMIDOR",
            "tipo_documento": 1, # 1 - Saída
            "local_destino": 1, # 1 - Interna
            "presenca_comprador": 1, # 1 - Presencial
            "consumidor_final": 1,
            "finalidade_emissao": 1, # 1 - Normal
            
            "items": items_payload,
            "formas_pagamento": payment_forms
        }

        # Adiciona CPF na nota se existir
        if sale.customer_cpf:
            sale_data["cpf_destinatario"] = sale.customer_cpf

        # 4. Chama o Provider Real (Assíncrono)
        response = await self.provider.emit(sale_data, config)
        
        # 5. Processa Resposta
        if response["success"]:
            existing_invoice.set_authorized(
                key=response.get("access_key"),
                protocol=response.get("protocol"),
                number=response.get("number"),
                xml=response.get("xml_url"),
                pdf=response.get("pdf_url")
            )
        else:
            existing_invoice.status = InvoiceStatus.ERROR
            existing_invoice.error_message = response.get("error")
            
        await self.fiscal_repo.save_invoice(existing_invoice)
        return self._map_invoice_dto(existing_invoice)

    def _map_invoice_dto(self, inv: Invoice) -> InvoiceResponseDTO:
        return InvoiceResponseDTO(
            id=inv.id,
            status=inv.status.value,
            access_key=inv.access_key,
            number=inv.number,
            series=inv.series,
            xml_url=inv.xml_url,
            pdf_url=inv.pdf_url,
            error_message=inv.error_message,
            emitted_at=inv.emitted_at
        )

    def _map_payment_method(self, method_str: str) -> str:
        """
        Converte o Enum interno para código SEFAZ.
        01=Dinheiro, 03=Crédito, 04=Débito, 17=Pix, 99=Outros
        """
        mapping = {
            "dinheiro": "01",
            "cartao_credito": "03",
            "cartao_debito": "04",
            "pix": "17",
            "fiado": "99" # Fiado tecnicamente é "Outros" ou "Sem Pagamento" na NFC-e até quitação? Geralmente 99.
        }
        return mapping.get(method_str, "99")