"""add billing models phase4

Revision ID: a4c9e1b7f230
Revises: f3a1c7d92b01
Create Date: 2026-05-21 00:00:00.000000

Cria as tabelas:
  - billing_subscriptions: assinatura local espelhada do Billing Core
  - billing_events: eventos recebidos via webhook, processados de forma idempotente

A tabela billing_subscriptions usa owner_id como tenant comercial
(system_sub_id = str(owner_user_id)). Os campos plan_id e plan_expiration
em UserModel continuam como cache temporário para leitura rápida.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'a4c9e1b7f230'
down_revision = 'f3a1c7d92b01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'billing_subscriptions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('owner_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_id', UUID(as_uuid=True), sa.ForeignKey('plans.id'), nullable=True),
        sa.Column('billing_system', sa.String(), nullable=False, server_default='marketfy'),
        sa.Column('billing_system_sub_id', sa.String(), nullable=True),
        sa.Column('billing_subscription_id', sa.String(), nullable=True),
        sa.Column('billing_job_id', sa.String(), nullable=True),
        sa.Column('customer_provider_id', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('subscription_type', sa.String(), nullable=True),
        sa.Column('value', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_event_at', sa.DateTime(), nullable=True),
        sa.Column('idempotency_key', sa.String(), nullable=True, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_billing_sub_owner', 'billing_subscriptions', ['owner_id'])
    op.create_index('ix_billing_sub_status', 'billing_subscriptions', ['status'])

    op.create_table(
        'billing_events',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('event_id', sa.String(), nullable=False, unique=True),
        sa.Column('subscription_id', UUID(as_uuid=True), sa.ForeignKey('billing_subscriptions.id'), nullable=True),
        sa.Column('owner_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('idempotency_key', sa.String(), nullable=True),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('processing_status', sa.String(), nullable=False, server_default='received'),
        sa.Column('processing_error', sa.Text(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_billing_event_sub', 'billing_events', ['subscription_id'])
    op.create_index('ix_billing_event_type', 'billing_events', ['event_type'])


def downgrade() -> None:
    op.drop_index('ix_billing_event_type', table_name='billing_events')
    op.drop_index('ix_billing_event_sub', table_name='billing_events')
    op.drop_table('billing_events')

    op.drop_index('ix_billing_sub_status', table_name='billing_subscriptions')
    op.drop_index('ix_billing_sub_owner', table_name='billing_subscriptions')
    op.drop_table('billing_subscriptions')
