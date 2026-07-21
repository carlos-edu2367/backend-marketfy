"""Pix (Mercado Pago): adiciona colunas de webhook MP a provider_webhook_events.

Revision ID: 20260721_0017
Revises: 20260721_0016
Create Date: 2026-07-21 12:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0017"
down_revision: Union[str, Sequence[str], None] = "20260721_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("provider_webhook_events", sa.Column("request_id", sa.String(), nullable=True))
    op.add_column("provider_webhook_events", sa.Column("signature_valid", sa.Boolean(), nullable=True))
    op.add_column("provider_webhook_events", sa.Column("received_ts", sa.String(), nullable=True))
    op.add_column("provider_webhook_events", sa.Column("action", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("provider_webhook_events", "action")
    op.drop_column("provider_webhook_events", "received_ts")
    op.drop_column("provider_webhook_events", "signature_valid")
    op.drop_column("provider_webhook_events", "request_id")
