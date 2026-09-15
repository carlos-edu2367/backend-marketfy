"""billing_subscriptions: checkout_url.

Descoberto ao implementar RecurringService.ensure_checkout (Task 5 do plano
de 2026-09-15): sem esta coluna, o checkout de assinatura recorrente nao
tinha onde persistir o link de pagamento, e a confirmacao idempotente
(reabrir o mesmo checkout) nunca conseguia devolver a URL ja emitida.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260915_0025"
down_revision: Union[str, Sequence[str], None] = "20260915_0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("billing_subscriptions", sa.Column("checkout_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("billing_subscriptions", "checkout_url")
