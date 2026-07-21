from datetime import datetime, timezone
from domain.inventory import StockMovementType
from domain.sales import SaleStatus
from domain.finance import FinancialTransaction, TransactionType


class SaleCompleter:
    """Materializa a venda após confirmação Pix, de forma idempotente."""
    def __init__(self, *, sale_repo, product_repo, box_repo, financial_repo, payment_repo):
        self.sale_repo = sale_repo
        self.product_repo = product_repo
        self.box_repo = box_repo
        self.financial_repo = financial_repo
        self.payment_repo = payment_repo

    async def complete_sale(self, attempt):
        sale = await self.sale_repo.get_by_id(attempt.sale_id)
        if sale is None or sale.status == SaleStatus.COMPLETED:
            return  # idempotente
        # baixa estoque dos itens da venda (persistidos em AWAITING_PAYMENT)
        for item in sale.items:
            product = await self.product_repo.get_by_id(item.product_id)
            if product:
                product.add_movement(StockMovementType.SALE, item.quantity, reason=f"Venda Pix {sale.id}")
                await self.product_repo.save(product, commit=False)
        # registro financeiro
        fin = FinancialTransaction(market_id=sale.market_id, description="Venda Pix (QR)",
            amount=sale.total_amount, type=TransactionType.CREDIT,
            due_date=datetime.now(), paid_at=datetime.now())
        await self.financial_repo.save(fin, commit=False)
        # pagamento confirmado
        await self.payment_repo.add_pix_payment(sale_id=sale.id, amount=sale.total_amount,
            pix_attempt_id=attempt.id, external_reference=attempt.external_reference,
            confirmed_at=datetime.now(timezone.utc), commit=False)
        sale.status = SaleStatus.COMPLETED
        attempt.approved_at = datetime.now(timezone.utc)
        attempt.qr_data = None
        await self.sale_repo.save(sale, commit=True)
