import sys
import os

# --- CONFIGURAÇÃO DE PATH (Para evitar erros de import) ---
# Adiciona a pasta 'app' ao Python Path para que 'from domain...' funcione
# Assume estrutura: backend/tests/unit/test_domain_pure.py -> precisa de backend/app
current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Imports do Domínio
from app.domain.shared import BusinessRuleException, ValidationException, CPF, CNPJ, Email
from app.domain.identity import Plan, PlanType, PlanDuration, User, UserRole
from app.domain.inventory import Product, StockMovementType
from app.domain.sales import Box, BoxStatus, Sale, SaleStatus, PaymentMethod
from app.domain.finance import Customer, CustomerStatus
from app.domain.support import Ticket, TicketStatus, TicketPriority

# =============================================================================
# 1. TESTES DE SHARED (Value Objects & Validations)
# =============================================================================

class TestSharedDomain:
    def test_cpf_validation(self):
        # CPF válido (apenas formato/tamanho no MVP)
        cpf = CPF("123.456.789-00")
        assert cpf.value == "12345678900"
        
        # CPF inválido (tamanho incorreto)
        with pytest.raises(ValidationException):
            CPF("123")

    def test_email_validation(self):
        # Email válido
        email = Email("teste@marketfy.com")
        assert email.value == "teste@marketfy.com"

        # Email inválido
        with pytest.raises(ValidationException):
            Email("teste-sem-arroba.com")

# =============================================================================
# 2. TESTES DE IDENTITY (Planos e Limites)
# =============================================================================

class TestIdentityDomain:
    def test_plan_limits_check(self):
        plan = Plan(
            id=uuid.uuid4(),
            name="Plano Básico",
            type=PlanType.PAID,
            max_markets=2,
            max_boxes_per_market=3,
            price=99.90
        )

        # Teste Limite de Mercados
        assert plan.is_limit_reached(1, 'markets') is False
        assert plan.is_limit_reached(2, 'markets') is True # Atingiu
        assert plan.is_limit_reached(3, 'markets') is True # Excedeu

        # Teste Limite de Caixas
        assert plan.is_limit_reached(2, 'boxes') is False
        assert plan.is_limit_reached(3, 'boxes') is True

    def test_user_plan_expiration(self):
        # Usuário com plano expirado
        expired_date = datetime.now(timezone.utc) - timedelta(days=1)
        user = User(
            id=uuid.uuid4(),
            name="Dono",
            email=Email("dono@email.com"),
            cpf=CPF("11111111111"),
            password_hash="hash",
            role=UserRole.OWNER,
            plan_id=uuid.uuid4(),
            plan_expiration=expired_date
        )

        with pytest.raises(BusinessRuleException) as exc:
            user.check_plan_validity()
        assert "Plano expirado" in str(exc.value)

    def test_user_activate_plan(self):
        user = User(
            id=uuid.uuid4(),
            name="Dono",
            email=Email("dono@email.com"),
            cpf=CPF("11111111111"),
            password_hash="hash",
            role=UserRole.OWNER
        )
        plan = Plan(id=uuid.uuid4(), name="Pro", type=PlanType.PAID, max_markets=5, max_boxes_per_market=5, price=100)
        
        # Ativa plano anual
        user.activate_plan(plan, PlanDuration.ANNUAL)
        
        assert user.plan_id == plan.id
        assert user.plan_expiration > datetime.now(timezone.utc) + timedelta(days=360)

# =============================================================================
# 3. TESTES DE INVENTORY (Auditoria e Estoque)
# =============================================================================

class TestInventoryDomain:
    def test_product_stock_audit_trace(self):
        """Testa se toda alteração de estoque gera rastro auditável e cálculo correto."""
        product = Product(
            id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            name="Arroz 5kg",
            code="1001",
            barcode="78900001", # Corrigido: Campo obrigatório adicionado
            price=Decimal("20.00"),
            ncm="12345678",
            origin=0
        )
        
        # 1. Entrada de Mercadoria (+10)
        movement_in = product.add_movement(StockMovementType.ENTRY, Decimal("10"), "Nota Fiscal 001")
        
        assert product.current_stock == Decimal("10.00")
        assert movement_in.quantity == Decimal("10")
        assert movement_in.movement_type == StockMovementType.ENTRY
        assert movement_in.reason == "Nota Fiscal 001"

        # 2. Venda (-2)
        movement_sale = product.add_movement(StockMovementType.SALE, Decimal("2"), "Venda PDV")
        
        assert product.current_stock == Decimal("8.00")
        assert movement_sale.quantity == Decimal("2")
        
        # 3. Sangria/Perda (-1)
        product.add_movement(StockMovementType.LOSS, Decimal("1"), "Produto vencido")
        assert product.current_stock == Decimal("7.00")

    def test_negative_quantity_validation(self):
        product = Product(
            id=uuid.uuid4(), 
            market_id=uuid.uuid4(), 
            name="Teste", 
            code="1", 
            barcode="78900002", # Corrigido: Campo obrigatório adicionado
            price=Decimal("10")
        )
        with pytest.raises(BusinessRuleException):
            product.add_movement(StockMovementType.ENTRY, Decimal("-5"))

# =============================================================================
# 4. TESTES DE SALES (Caixa e Venda)
# =============================================================================

class TestSalesDomain:
    # --- CAIXA (BOX) ---
    def test_box_lifecycle(self):
        operator_id = uuid.uuid4()
        box = Box(id=uuid.uuid4(), market_id=uuid.uuid4(), name="Caixa 01")
        
        # Abertura
        initial_balance = Decimal("100.00")
        box.open_box(operator_id, initial_balance)
        
        assert box.status == BoxStatus.OPEN
        assert box.current_balance == initial_balance
        assert box.opened_at is not None

        # Movimentação (Venda em dinheiro)
        box.add_cash(Decimal("50.00"))
        assert box.current_balance == Decimal("150.00")

        # Sangria
        box.remove_cash(Decimal("20.00"))
        assert box.current_balance == Decimal("130.00")

        # Fechamento com Quebra (Operador contou menos do que o sistema diz)
        # Sistema diz 130.00, Operador contou 125.00 -> Quebra de -5.00
        diff = box.close_box(Decimal("125.00"))
        
        assert diff == Decimal("-5.00")
        assert box.status == BoxStatus.CLOSED
        assert box.current_operator_id is None

    def test_cannot_move_cash_on_closed_box(self):
        box = Box(id=uuid.uuid4(), market_id=uuid.uuid4(), name="Caixa Fechado")
        with pytest.raises(BusinessRuleException):
            box.add_cash(Decimal("10.00"))

    # --- VENDA (SALE) ---
    def test_sale_flow_and_totals(self):
        sale = Sale(
            id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            box_id=uuid.uuid4(),
            operator_id=uuid.uuid4(),
            status=SaleStatus.PENDING_SYNC,
            total_amount=Decimal("0")
        )

        product_a = Product(
            id=uuid.uuid4(), 
            market_id=sale.market_id, 
            name="Coca Cola", 
            code="1", 
            barcode="78900003", # Corrigido
            price=Decimal("10.00"), 
            ncm="2202", 
            origin=0
        )
        product_b = Product(
            id=uuid.uuid4(), 
            market_id=sale.market_id, 
            name="Biscoito", 
            code="2", 
            barcode="78900004", # Corrigido
            price=Decimal("5.00"), 
            ncm="1905", 
            origin=0
        )

        # 1. Adicionar Itens
        # 2x Coca (20.00)
        item1 = sale.add_item(product_a, Decimal("2"))
        # 1x Biscoito (5.00)
        item2 = sale.add_item(product_b, Decimal("1"))

        assert len(sale.items) == 2
        assert sale.total_amount == Decimal("25.00")
        
        # Validação do Snapshot Fiscal no Item
        assert item1.ncm_snapshot == "2202"
        assert item1.product_name == "Coca Cola" # Snapshot do nome

        # 2. Desconto
        sale.discount = Decimal("5.00")
        sale.calculate_total()
        assert sale.total_amount == Decimal("20.00")

        # 3. Pagamento Parcial (Erro)
        sale.add_payment(PaymentMethod.CASH, Decimal("10.00"))
        with pytest.raises(BusinessRuleException) as exc:
            sale.finalize()
        assert "Pagamento insuficiente" in str(exc.value)

        # 4. Pagamento Restante
        sale.add_payment(PaymentMethod.CREDIT_CARD, Decimal("10.00"))
        sale.finalize()
        
        assert sale.status == SaleStatus.COMPLETED

# =============================================================================
# 5. TESTES DE FINANCE (Fiado/Crédito)
# =============================================================================

class TestFinanceDomain:
    def test_customer_credit_limit(self):
        customer = Customer(
            id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            name="José da Silva",
            credit_limit=Decimal("100.00"),
            current_debt=Decimal("0.00")
        )

        # Compra aceita
        customer.add_debt(Decimal("80.00"))
        assert customer.current_debt == Decimal("80.00")

        # Compra rejeitada (Estoura limite 100)
        with pytest.raises(BusinessRuleException) as exc:
            customer.add_debt(Decimal("25.00")) # 80 + 25 = 105
        assert "Limite de crédito excedido" in str(exc.value)

    def test_blocked_customer(self):
        customer = Customer(
            id=uuid.uuid4(), market_id=uuid.uuid4(), name="Caloteiro",
            credit_limit=Decimal("1000"), status=CustomerStatus.BLOCKED
        )
        with pytest.raises(BusinessRuleException) as exc:
            customer.add_debt(Decimal("10.00"))
        assert "Cliente bloqueado" in str(exc.value)

    def test_pay_debt(self):
        customer = Customer(
            id=uuid.uuid4(), market_id=uuid.uuid4(), name="José",
            credit_limit=Decimal("100"), current_debt=Decimal("50.00")
        )
        
        customer.pay_debt(Decimal("50.00"))
        assert customer.current_debt == Decimal("0.00")

# =============================================================================
# 6. TESTES DE SUPPORT (Tickets)
# =============================================================================

class TestSupportDomain:
    def test_ticket_flow(self):
        ticket = Ticket(
            id=uuid.uuid4(),
            requester_id=uuid.uuid4(),
            market_id=uuid.uuid4(), # Corrigido: Campo obrigatório adicionado
            subject="Erro no Login",
            status=TicketStatus.OPEN
        )

        # Adicionar mensagem
        ticket.add_message(uuid.uuid4(), "Não consigo logar")
        assert len(ticket.messages) == 1

        # Atribuir agente
        agent_id = uuid.uuid4()
        ticket.assign_agent(agent_id)
        assert ticket.status == TicketStatus.IN_PROGRESS
        assert ticket.assigned_to_id == agent_id

        # Fechar ticket
        ticket.change_status(TicketStatus.CLOSED, agent_id)
        
        # Tentar adicionar msg em ticket fechado
        with pytest.raises(BusinessRuleException):
            ticket.add_message(uuid.uuid4(), "Ainda com erro")

if __name__ == "__main__":
    # Permite rodar este arquivo diretamente se necessário
    sys_args = ["-v", __file__]
    pytest.main(sys_args)