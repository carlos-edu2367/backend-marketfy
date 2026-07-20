import uuid
from decimal import Decimal
from typing import List, Optional
from datetime import datetime, timedelta, timezone

from domain.sales import (
    Sale, SaleItem, SaleItemFiscalEvidence, SaleStatus, PaymentMethod,
    Box, BoxStatus, Terminal,
)
from domain.inventory import StockMovementType
from domain.shared import BusinessRuleException
from domain.interfaces import (
    SaleRepositoryInterface, BoxRepositoryInterface, ProductRepositoryInterface,
    MarketRepositoryInterface, TerminalRepositoryInterface, CustomerRepositoryInterface,
    UserRepositoryInterface, PlanRepositoryInterface, FinancialTransactionRepositoryInterface, FinancialTransaction
)
from domain.finance import TransactionType
from application.dtos import (
    SaleCreateDTO, MarketDashboardStatsDTO, DailySalesDTO, 
    TerminalCreateDTO, TerminalResponseDTO, BoxResponseDTO, 
    SaleResponseDTO, SaleItemDTO, PaymentDTO, CloseBoxDTO
)
from application.services.fiscal.tax_rule_calculator import TaxRuleCalculator
from application.services.fiscal.snapshot_integrity import canonical_sha256
from application.services.fiscal.tax_rule_service import (
    FiscalRuleAmbiguousError,
    FiscalRuleMissingError,
    TaxContext,
    TaxRuleNotFoundError,
    TaxRuleService,
)
from application.services.fiscal.fiscal_rollout_service import (
    effective_fiscal_rule_enforcement,
)
from domain.fiscal import FiscalRuleEnforcement, FiscalRuleError
from infra.config.logger import get_logger
from infra.observability.metrics import metrics_registry

logger = get_logger("sales_service")

class SalesService:
    def __init__(self, 
                 sale_repo: SaleRepositoryInterface, 
                 box_repo: BoxRepositoryInterface,
                 product_repo: ProductRepositoryInterface,
                 market_repo: MarketRepositoryInterface,
                 terminal_repo: TerminalRepositoryInterface, 
                 user_repo: UserRepositoryInterface,
                 plan_repo: PlanRepositoryInterface,
                 financial_repo: FinancialTransactionRepositoryInterface,
                 customer_repo: CustomerRepositoryInterface = None,
                 fiscal_config_repo=None,
                 tax_rule_service: TaxRuleService = None,
                 tax_rule_calculator: TaxRuleCalculator = None,
                 environment: str = "development",
                 fiscal_offline_max_age_minutes: int = 30,
                 fiscal_product_rules_enabled: bool = True,
                 metrics=None,
                 ): 
        self.sale_repo = sale_repo
        self.box_repo = box_repo
        self.product_repo = product_repo
        self.market_repo = market_repo
        self.terminal_repo = terminal_repo
        self.user_repo = user_repo
        self.plan_repo = plan_repo
        self.customer_repo = customer_repo
        self.financial_repo = financial_repo
        self.fiscal_config_repo = fiscal_config_repo
        self.tax_rule_service = tax_rule_service
        self.tax_rule_calculator = tax_rule_calculator or TaxRuleCalculator()
        self.environment = environment
        self.fiscal_offline_max_age_minutes = fiscal_offline_max_age_minutes
        self.fiscal_product_rules_enabled = fiscal_product_rules_enabled
        self.metrics = metrics or metrics_registry

    # ==================================================================================
    # TERMINAIS (PDVs)
    # ==================================================================================

    async def list_terminals(self, market_id: uuid.UUID) -> List[TerminalResponseDTO]:
        terminals = await self.terminal_repo.list_by_market(market_id)
        return [TerminalResponseDTO.model_validate(t) for t in terminals]

    async def create_terminal(self, market_id: uuid.UUID, dto: TerminalCreateDTO) -> TerminalResponseDTO:
        # 1. Verifica limites do plano
        market = await self.market_repo.get_by_id(market_id)
        if not market:
            raise BusinessRuleException("Mercado não encontrado.")
            
        owner = await self.user_repo.get_by_id(market.owner_id)
        if not owner:
            raise BusinessRuleException("Proprietário do mercado não encontrado.")
        
        if owner.plan_id:
            plan = await self.plan_repo.get_by_id(owner.plan_id)
            current_count = await self.terminal_repo.count_by_market(market_id)
            
            if plan and plan.is_limit_reached(current_count, 'terminals'):
                raise BusinessRuleException(f"Limite de terminais ({plan.max_terminals}) atingido para o plano {plan.name}.")

        # 2. Cria terminal
        terminal = Terminal(
            market_id=market_id,
            name=dto.name,
            active=dto.active
        )
        saved = await self.terminal_repo.save(terminal)
        return TerminalResponseDTO.model_validate(saved)

    # ==================================================================================
    # GESTÃO DE CAIXA (BOX)
    # ==================================================================================

    async def open_box(self, market_id: uuid.UUID, terminal_id: uuid.UUID, operator_id: uuid.UUID, initial_balance: Decimal) -> BoxResponseDTO:
        # Verifica se já existe caixa aberto neste terminal
        existing = await self.box_repo.get_open_box_by_terminal(terminal_id)
        if existing:
            raise BusinessRuleException("Já existe um caixa aberto neste terminal.")

        # Verifica se o operador já tem caixa aberto em outro lugar
        op_box = await self.box_repo.get_open_box_by_operator(operator_id)
        if op_box:
            raise BusinessRuleException("Este operador já possui um caixa aberto.")

        box = Box(
            market_id=market_id,
            operator_id=operator_id,
            terminal_id=terminal_id,
            initial_balance=initial_balance,
            current_balance=initial_balance # CORREÇÃO: O saldo atual começa com o saldo inicial
        )
        saved = await self.box_repo.save(box)
        return BoxResponseDTO.model_validate(saved)

    async def close_box(
        self,
        market_id: uuid.UUID,
        terminal_id: uuid.UUID,
        dto: CloseBoxDTO,
        box_id: Optional[uuid.UUID] = None,
    ) -> BoxResponseDTO:
        box = await self.box_repo.get_open_box_by_terminal(terminal_id)
        if not box:
            raise BusinessRuleException("Não há caixa aberto neste terminal para fechar.")
        
        if box.market_id != market_id:
            raise BusinessRuleException("Terminal não pertence a esta loja.")

        if box_id and box.id != box_id:
            raise BusinessRuleException("Caixa informado nÃ£o corresponde ao caixa aberto deste terminal.")

        box.close(dto.final_balance_reported, dto.closing_observation)
        saved = await self.box_repo.save(box)
        return BoxResponseDTO.model_validate(saved)

    async def get_open_box(self, market_id: uuid.UUID, terminal_id: uuid.UUID) -> Optional[BoxResponseDTO]:
        box = await self.box_repo.get_open_box_by_terminal(terminal_id)
        if box and box.market_id == market_id:
            return BoxResponseDTO.model_validate(box)
        return None

    # ==================================================================================
    # VENDAS E SYNC
    # ==================================================================================

    async def process_sync(self, market_id: uuid.UUID, sales_dtos: List[SaleCreateDTO]) -> List[SaleResponseDTO]:
        """
        Processa um lote de vendas vindas do frontend (Offline-First).
        Atualiza estoque, financeiro (fiado), SALDO DO CAIXA e persiste a venda.
        """
        results = []

        if len(sales_dtos) > 1:
            batch_enforcement = await self._get_fiscal_rule_enforcement(market_id)
            if batch_enforcement is FiscalRuleEnforcement.BLOCK:
                raise FiscalRuleError(
                    "sale.fiscal_single_checkout_required",
                    "O modo fiscal bloqueio aceita uma venda online por requisição.",
                )
        
        for dto in sales_dtos:
            try:
                # 1. Verifica se venda já existe (idempotência pelo offline_id ou id)
                existing_sale = None
                if dto.id:
                    existing_sale = await self.sale_repo.get_by_id(dto.id)
                if not existing_sale and dto.offline_id:
                    existing_sale = await self.sale_repo.get_by_offline_id(market_id, dto.offline_id)
                
                if existing_sale:
                    # Se já existe e está concluída, ignora para evitar duplicação de estoque
                    if existing_sale.status == SaleStatus.COMPLETED:
                        logger.info(f"Venda {dto.id} já sincronizada. Pulando.")
                        results.append(self._map_sale_to_response(existing_sale))
                        continue

                enforcement = await self._get_fiscal_rule_enforcement(market_id)
                if enforcement is FiscalRuleEnforcement.BLOCK and dto.offline_id:
                    raise FiscalRuleError(
                        "sale.fiscal_connection_required",
                        "Vendas fiscais em modo bloqueio exigem conexão online.",
                        [{
                            "code": "sale.fiscal_connection_required",
                            "offline_id": dto.offline_id,
                        }],
                    )
                
                # 2. Cria Entidade Venda
                received_at = datetime.now(timezone.utc)
                sale = Sale(
                    market_id=market_id,
                    box_id=dto.box_id,
                    operator_id=dto.operator_id,
                    status=SaleStatus.COMPLETED,
                    total_amount=dto.total_amount,
                    discount=dto.discount,
                    acrescimo=dto.acrescimo,
                    customer_cpf=dto.customer_cpf,
                    offline_id=dto.offline_id,
                    synced_at=received_at,
                    received_at=received_at,
                    created_at=dto.created_at # Mantém data original da venda
                )
                # Força o ID se vier do front para manter rastreabilidade
                if dto.id:
                    sale.id = dto.id

                if enforcement is not FiscalRuleEnforcement.BLOCK:
                    self._validate_offline_sale_clock(sale)

                # 3. Carrega todos os produtos e resolve o fiscal antes da fase comercial.
                # Isso impede movimentação de estoque ou venda parcial quando há regra ausente.
                prepared_items = []
                for item_dto in dto.items:
                    product = await self.product_repo.get_by_id(item_dto.product_id)
                    if not product or product.market_id != market_id:
                        raise BusinessRuleException(
                            f"Erro na venda {dto.offline_id or str(dto.id)[:8]}: produto nao encontrado na loja."
                        )

                    prepared_items.append((item_dto, product))

                sale._fiscal_rule_enforcement = enforcement
                fiscal_snapshots = await self._resolve_fiscal_snapshots(
                    market_id=market_id,
                    sale=sale,
                    prepared_items=prepared_items,
                )

                # Freeze every item before entering any commercial mutation phase.
                for (item_dto, product), fiscal_evidence in zip(
                    prepared_items, fiscal_snapshots
                ):
                    sale.add_item(
                        product,
                        item_dto.quantity,
                        fiscal_evidence=fiscal_evidence,
                    )

                # 4. Validação e Processamento de Pagamentos
                box = await self.box_repo.get_by_id(dto.box_id)
                if not box or box.market_id != market_id or box.status != BoxStatus.OPEN:
                    raise BusinessRuleException(
                        f"Erro na venda {dto.offline_id or str(dto.id)[:8]}: caixa invalido ou fechado."
                    )

                fiado_pending_list = [] # Armazena dados para processar fiado apos salvar a venda
                cash_amount_to_add_to_box = Decimal("0.00") # CORREÇÃO: Acumula valor em dinheiro

                for pay_dto in dto.payments:
                    try:
                        method = PaymentMethod(pay_dto.method)
                    except ValueError:
                        method = PaymentMethod.CASH # Fallback seguro

                    # --- SOMA AO CAIXA SE FOR DINHEIRO ---
                    if method == PaymentMethod.CASH:
                        cash_amount_to_add_to_box += pay_dto.amount

                    # --- VALIDAÇÃO DE FIADO ---
                    if method == PaymentMethod.FIADO:
                        if not dto.customer_cpf:
                            raise BusinessRuleException(
                                f"Erro na venda {dto.offline_id or str(dto.id)[:8]}: "
                                "Pagamento 'Fiado' exige CPF do cliente informado."
                            )

                        if not self.customer_repo:
                            raise BusinessRuleException("Serviço de clientes indisponível para processar Fiado.")

                        customer = await self.customer_repo.get_by_cpf(market_id, dto.customer_cpf)
                        if not customer:
                            raise BusinessRuleException(
                                f"Erro na venda {dto.offline_id or str(dto.id)[:8]}: "
                                f"Cliente com CPF '{dto.customer_cpf}' não encontrado. Cadastre o cliente antes de vender Fiado."
                            )

                        # Armazena para adicionar o débito APÓS salvar a venda (evita erro de FK no autoflush)
                        fiado_pending_list.append({
                            "customer": customer,
                            "amount": pay_dto.amount
                        })

                    sale.add_payment(method, pay_dto.amount, pay_dto.installments)

                # 5. Adiciona itens e baixa o estoque somente após validar o lote fiscal.
                for item_dto, product in prepared_items:

                    # Baixa Estoque (Movimentação do tipo VENDA)
                    product.add_movement(
                        movement_type=StockMovementType.SALE, 
                        quantity=item_dto.quantity,
                        reason=f"Venda {sale.id}"
                    )
                    await self.product_repo.save(product, commit=False) # Commit no final
                    
                # 6. CORREÇÃO: Atualiza Saldo do Caixa (Se houve dinheiro)
                if cash_amount_to_add_to_box > 0:
                    box.current_balance += cash_amount_to_add_to_box
                    await self.box_repo.save(box, commit=False)

                # 7. Salva Venda e Processa Fiado
                if fiado_pending_list:
                    # Salva a venda SEM commit para disponibilizar o ID na sessão
                    saved_sale = await self.sale_repo.save(sale, commit=False)
                    
                    # Agora que a venda está na sessão, adicionamos os débitos
                    for entry in fiado_pending_list:
                        cust = entry["customer"]
                        amt = entry["amount"]
                        # Passa o ID da venda recém criada para o histórico
                        cust.add_debt(amt, sale.id, f"Compra em {sale.created_at.strftime('%d/%m/%Y %H:%M')}")
                        
                        # Salva o cliente
                        await self.customer_repo.save(cust, commit=False)
                    
                    # Commit final de tudo (Venda + Estoque + Caixa + Fiado)

                    await self.sale_repo.save(sale, commit=True) # Este método já faz refresh, usamos ele para finalizar a transação
                else:
                    # Fluxo normal sem fiado (Venda + Estoque + Caixa)
                    financial = FinancialTransaction(
                        market_id=market_id,
                        description="Venda realizada",
                        amount=sale.total_amount,
                        type=TransactionType.CREDIT,
                        due_date=datetime.now(),
                        paid_at=sale.synced_at
                    )
                    await self.financial_repo.save(financial, commit=False)
                    saved_sale = await self.sale_repo.save(sale, commit=True)

                results.append(self._map_sale_to_response(saved_sale))
            
            except BusinessRuleException as bre:
                logger.error(f"Regra de negócio violada na venda {dto.id}: {str(bre)}")
                # Relança a exceção para que o Controller retorne 400 Bad Request
                # em vez de suprimir o erro e retornar 200 OK.
                raise bre
            except Exception as e:
                logger.error(f"Erro inesperado ao sincronizar venda {dto.id}: {str(e)}")
                # Relança erro inesperado para evitar 200 OK com falha silenciosa
                raise e

        return results

    async def _resolve_fiscal_snapshots(
        self,
        *,
        market_id,
        sale: Sale,
        prepared_items,
    ) -> list[Optional[SaleItemFiscalEvidence]]:
        enforcement = sale._fiscal_rule_enforcement
        if enforcement is FiscalRuleEnforcement.OFF:
            return [None] * len(prepared_items)

        snapshots, fiscal_errors = await self._collect_fiscal_snapshots(
            market_id=market_id,
            sale_id=sale.id,
            occurred_at=sale.created_at,
            prepared_items=prepared_items,
        )

        if fiscal_errors and enforcement is FiscalRuleEnforcement.BLOCK:
            error_codes = {item["code"] for item in fiscal_errors}
            code = (
                next(iter(error_codes))
                if len(error_codes) == 1
                else "sale.fiscal_rule_invalid"
            )
            if code == "sale.fiscal_rule_missing":
                raise FiscalRuleMissingError(
                    [
                        {"id": item["product_id"], "name": item["product_name"]}
                        for item in fiscal_errors
                    ],
                    items=fiscal_errors,
                )
            raise FiscalRuleError(
                code,
                "Há itens sem evidência fiscal v2 válida para concluir a venda.",
                fiscal_errors,
            )
        if fiscal_errors and enforcement is FiscalRuleEnforcement.WARN:
            sale.fiscal_rule_pendencies = [
                {
                    "code": item["code"],
                    "product_id": item["product_id"],
                    "product_name": item["product_name"],
                }
                for item in fiscal_errors
            ]
        return snapshots

    async def fiscal_preflight(
        self,
        *,
        market_id: uuid.UUID,
        occurred_at: datetime,
        items,
    ) -> tuple[FiscalRuleEnforcement, list[dict]]:
        """Evaluate the same rule resolver/calculator as checkout, without writes."""
        enforcement = await self._get_fiscal_rule_enforcement(market_id)

        prepared_items = []
        fiscal_errors = []
        for item in items:
            product = await self.product_repo.get_by_id(item.product_id)
            if product is None or product.market_id != market_id:
                fiscal_errors.append(
                    {
                        "code": "sale.product_not_found",
                        "product_id": str(item.product_id),
                        "product_name": "Produto não encontrado",
                    }
                )
                continue
            prepared_items.append((item, product))

        _snapshots, resolution_errors = await self._collect_fiscal_snapshots(
            market_id=market_id,
            sale_id=uuid.uuid4(),
            occurred_at=occurred_at,
            prepared_items=prepared_items,
        )
        fiscal_errors.extend(resolution_errors)
        self._record_fiscal_preflight_metrics(
            market_id=market_id,
            enforcement=enforcement,
            fiscal_errors=fiscal_errors,
        )
        return enforcement, fiscal_errors

    def _record_fiscal_preflight_metrics(
        self,
        *,
        market_id: uuid.UUID,
        enforcement: FiscalRuleEnforcement,
        fiscal_errors: list[dict],
    ) -> None:
        """Record bounded rollout outcomes without product or tenant labels."""
        events_by_error_code = {
            "sale.fiscal_rule_missing": "fiscal_rule_missing",
            "sale.fiscal_rule_ambiguous": "fiscal_rule_ambiguous",
            "sale.fiscal_rule_invalid": "snapshot_invalid",
        }
        for error in fiscal_errors:
            result_code = str(error.get("code") or "unknown")
            self.metrics.record_fiscal_contract(
                market_id=str(market_id),
                contract_version="marketfy.fiscal-tax-snapshot.v2",
                enforcement_mode=enforcement.value,
                result_code=result_code,
                path="v2",
            )
            event = events_by_error_code.get(result_code)
            if event is not None:
                self.metrics.record_fiscal_rule_event(
                    market_id=str(market_id),
                    mode=enforcement.value,
                    event=event,
                )

    async def _collect_fiscal_snapshots(
        self,
        *,
        market_id: uuid.UUID,
        sale_id: uuid.UUID,
        occurred_at: datetime,
        prepared_items,
    ) -> tuple[list[Optional[SaleItemFiscalEvidence]], list[dict]]:
        fiscal_errors = []
        snapshots: list[Optional[SaleItemFiscalEvidence]] = []
        context = TaxContext.go_nfce_consumer_final()

        for item_dto, product in prepared_items:
            item_error = {
                "product_id": str(product.id),
                "product_name": product.name,
            }
            try:
                if not self.tax_rule_service:
                    raise TaxRuleNotFoundError("Serviço de regras fiscais indisponível.")
                rule = await self.tax_rule_service.resolve_for_sale_item(
                    market_id=market_id,
                    product_id=product.id,
                    occurred_at=occurred_at,
                    context=context,
                )
            except TaxRuleNotFoundError:
                fiscal_errors.append({
                    "code": "sale.fiscal_rule_missing",
                    **item_error,
                })
                snapshots.append(None)
                continue
            except FiscalRuleAmbiguousError as exc:
                fiscal_errors.append({
                    "code": exc.code,
                    **item_error,
                    **exc.details(),
                })
                snapshots.append(None)
                continue
            except FiscalRuleError as exc:
                fiscal_errors.append({
                    "code": exc.code,
                    **item_error,
                    "details": exc.items,
                })
                snapshots.append(None)
                continue

            try:
                fiscal_snapshot = self.tax_rule_calculator.calculate(
                    item=SaleItem(
                        sale_id=sale_id,
                        product_id=product.id,
                        product_name=product.name,
                        quantity=item_dto.quantity,
                        unit_price=product.price,
                        total=product.price * item_dto.quantity,
                    ),
                    rule=rule,
                )
                snapshot_json = fiscal_snapshot.as_persistence_dict()
                snapshots.append(SaleItemFiscalEvidence(
                    tax_rule_id_snapshot=uuid.UUID(fiscal_snapshot.rule_id),
                    tax_rule_version_snapshot=fiscal_snapshot.rule_version,
                    fiscal_calculation_version=fiscal_snapshot.calculation_version,
                    fiscal_tax_snapshot=snapshot_json,
                    snapshot_sha256=canonical_sha256(snapshot_json),
                ))
            except (BusinessRuleException, TypeError, ValueError) as exc:
                fiscal_errors.append({
                    "code": "sale.fiscal_snapshot_invalid",
                    **item_error,
                    "reason": str(exc),
                })
                snapshots.append(None)

        return snapshots, fiscal_errors

    def _validate_offline_sale_clock(self, sale: Sale) -> None:
        """Accept client time only inside the bounded production fiscal window."""
        claimed_at = sale.created_at
        received_at = sale.received_at
        if (
            not isinstance(claimed_at, datetime)
            or not isinstance(received_at, datetime)
            or claimed_at.tzinfo is None
            or received_at.tzinfo is None
        ):
            raise BusinessRuleException("sale.offline_clock_out_of_window; reason=timezone_required")
        claimed_utc = claimed_at.astimezone(timezone.utc)
        received_utc = received_at.astimezone(timezone.utc)
        window = timedelta(minutes=self.fiscal_offline_max_age_minutes)
        if claimed_utc < received_utc - window:
            raise BusinessRuleException("sale.offline_clock_out_of_window; reason=older_than_max_age")
        if claimed_utc > received_utc + window:
            raise BusinessRuleException("sale.offline_clock_out_of_window; reason=future_client_clock")

    async def _requires_fiscal_rule_enforcement(self, market_id: uuid.UUID) -> bool:
        return (
            await self._get_fiscal_rule_enforcement(market_id)
            is FiscalRuleEnforcement.BLOCK
        )

    async def _get_fiscal_rule_enforcement(
        self, market_id: uuid.UUID
    ) -> FiscalRuleEnforcement:
        if not self.fiscal_config_repo:
            return FiscalRuleEnforcement.OFF

        config = await self.fiscal_config_repo.get_by_market(market_id)
        if not config:
            return FiscalRuleEnforcement.OFF
        mode = getattr(config, "fiscal_rule_enforcement", FiscalRuleEnforcement.OFF)
        if isinstance(mode, FiscalRuleEnforcement):
            return effective_fiscal_rule_enforcement(
                mode, product_rules_enabled=self.fiscal_product_rules_enabled
            )
        try:
            return effective_fiscal_rule_enforcement(
                FiscalRuleEnforcement(mode),
                product_rules_enabled=self.fiscal_product_rules_enabled,
            )
        except (TypeError, ValueError):
            return FiscalRuleEnforcement.OFF

    async def list_sales(self, market_id: uuid.UUID, limit: int, offset: int) -> List[SaleResponseDTO]:
        sales = await self.sale_repo.list_by_market(market_id, limit, offset)
        return [self._map_sale_to_response(s) for s in sales]

    async def get_sale_by_id(self, market_id: uuid.UUID, sale_id: uuid.UUID) -> Optional[SaleResponseDTO]:
        sale = await self.sale_repo.get_by_id(sale_id)
        if not sale or sale.market_id != market_id:
            return None
        return self._map_sale_to_response(sale)

    async def cancel_sale(self, market_id: uuid.UUID, sale_id: uuid.UUID) -> SaleResponseDTO:
        """
        Cancela uma venda, estorna o estoque e registra o cancelamento.
        """
        sale = await self.sale_repo.get_by_id(sale_id)
        if not sale:
            raise BusinessRuleException("Venda não encontrada.")
        
        if sale.market_id != market_id:
            raise BusinessRuleException("Venda não pertence a este mercado.")

        if sale.status == SaleStatus.CANCELED:
            raise BusinessRuleException("Venda já está cancelada.")

        # Estorna Estoque
        for item in sale.items:
            product = await self.product_repo.get_by_id(item.product_id)
            if product:
                product.add_movement(
                    movement_type=StockMovementType.RETURN,
                    quantity=item.quantity,
                    reason=f"Cancelamento Venda {sale.id}"
                )
                await self.product_repo.save(product, commit=False)

        # Atualiza Status da Venda
        sale.status = SaleStatus.CANCELED
        saved_sale = await self.sale_repo.save(sale, commit=True)
        
        return self._map_sale_to_response(saved_sale)

    def _map_sale_to_response(self, sale: Sale) -> SaleResponseDTO:
        return SaleResponseDTO(
            id=sale.id,
            market_id=sale.market_id,
            box_id=sale.box_id,
            operator_id=sale.operator_id,
            status=sale.status.value,
            total_amount=sale.total_amount,
            discount=sale.discount,
            acrescimo=sale.acrescimo,
            created_at=sale.created_at,
            synced_at=sale.synced_at,
            customer_cpf=sale.customer_cpf,
            fiscal_rule_pendencies=sale.fiscal_rule_pendencies,
            items=[
                SaleItemDTO(
                    product_id=i.product_id,
                    product_name=i.product_name,
                    quantity=i.quantity,
                    unit_price=i.unit_price,
                    total=i.total,
                    ncm_snapshot=i.ncm_snapshot,
                    fiscal_tax_snapshot=i.fiscal_tax_snapshot,
                    tax_rule_id_snapshot=i.tax_rule_id_snapshot,
                    tax_rule_version_snapshot=i.tax_rule_version_snapshot,
                    snapshot_sha256=i.snapshot_sha256,
                    fiscal_calculation_version=i.fiscal_calculation_version,
                ) for i in sale.items
            ],
            payments=[
                PaymentDTO(
                    method=p.method.value,
                    amount=p.amount,
                    installments=p.installments
                ) for p in sale.payments
            ],
            invoice=sale.invoice 
        )

    # ==================================================================================
    # DASHBOARD
    # ==================================================================================

    async def get_dashboard_stats(self, market_id: uuid.UUID) -> MarketDashboardStatsDTO:
        # Vendas Hoje
        today = datetime.utcnow().date()
        stats = await self.sale_repo.get_daily_stats(market_id, today)
        
        # Caixas Abertos
        open_boxes = await self.box_repo.count_open(market_id)
        
        # Pega produtos com estoque baixo (ex: < 10)
        low_stock = await self.product_repo.count_low_stock(market_id, threshold=10)
        
        # Recebíveis (Total de Dívidas de Clientes)
        total_receivables = Decimal("0.00")
        if self.customer_repo:
            total_receivables = await self.customer_repo.get_total_debt(market_id)

        return MarketDashboardStatsDTO(
            today_sales_total=stats['total'],
            today_sales_count=stats['count'],
            open_boxes=open_boxes,
            low_stock_products=low_stock,
            total_receivables=total_receivables
        )

    async def get_sales_summary(self, market_id: uuid.UUID, start_date_str: str, end_date_str: str) -> List[DailySalesDTO]:
        """Retorna dados para gráfico de vendas."""
        try:
            # Tenta parsear formato YYYY-MM-DD
            if "T" in start_date_str:
                start_date = datetime.fromisoformat(start_date_str).date()
            else:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                
            if "T" in end_date_str:
                end_date = datetime.fromisoformat(end_date_str).date()
            else:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                
        except ValueError:
            raise BusinessRuleException("Formato de data inválido. Use YYYY-MM-DD.")
            
        stats_list = await self.sale_repo.get_sales_summary_by_period(market_id, start_date, end_date)
        return [DailySalesDTO(**item) for item in stats_list]
