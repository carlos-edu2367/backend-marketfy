"""fiscal system phase5 - tabelas robustas

Revision ID: b9f2e1a4c5d8
Revises: f6d4c0a9b812
Create Date: 2026-05-21 12:00:00.000000
"""

from typing import Sequence, Union
import uuid
import sqlalchemy as sa
from alembic import op

revision: str = "b9f2e1a4c5d8"
down_revision: Union[str, Sequence[str], None] = "f6d4c0a9b812"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── fiscal_tenant_configs ──────────────────────────────────────────────────
    op.create_table(
        "fiscal_tenant_configs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id"), nullable=False, unique=True),
        sa.Column("provider", sa.String, nullable=False, server_default="focus_nfe"),
        sa.Column("environment", sa.String, nullable=False, server_default="homologacao"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("legal_name", sa.String, nullable=True),
        sa.Column("trade_name", sa.String, nullable=True),
        sa.Column("cnpj", sa.String, nullable=True),
        sa.Column("state_registration", sa.String, nullable=True),
        sa.Column("municipal_registration", sa.String, nullable=True),
        sa.Column("tax_regime", sa.String, nullable=True),
        sa.Column("crt", sa.String, nullable=True),
        sa.Column("cnae_primary", sa.String, nullable=True),
        sa.Column("address_json", sa.Text, nullable=True),
        sa.Column("certificate_storage_key", sa.String, nullable=True),
        sa.Column("certificate_password_ciphertext", sa.String, nullable=True),
        sa.Column("certificate_fingerprint", sa.String, nullable=True),
        sa.Column("certificate_valid_until", sa.DateTime, nullable=True),
        sa.Column("csc_id_ciphertext", sa.String, nullable=True),
        sa.Column("csc_token_ciphertext", sa.String, nullable=True),
        sa.Column("nfce_series", sa.Integer, nullable=False, server_default="1"),
        sa.Column("nfce_next_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("numbering_mode", sa.String, nullable=False, server_default="provider_auto"),
        sa.Column("contingency_series", sa.Integer, nullable=False, server_default="900"),
        sa.Column("contingency_next_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("default_cfop", sa.String, nullable=False, server_default="5102"),
        sa.Column("default_ncm", sa.String, nullable=True),
        sa.Column("default_csosn", sa.String, nullable=True, server_default="102"),
        sa.Column("default_cst", sa.String, nullable=True),
        sa.Column("responsavel_tecnico", sa.Text, nullable=True),
        sa.Column("validation_status", sa.String, nullable=False, server_default="not_validated"),
        sa.Column("last_validated_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ftc_market", "fiscal_tenant_configs", ["market_id"])
    op.create_index("ix_ftc_validation", "fiscal_tenant_configs", ["validation_status"])

    # ── product_tax_profiles ──────────────────────────────────────────────────
    op.create_table(
        "product_tax_profiles",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("ncm", sa.String, nullable=True),
        sa.Column("cest", sa.String, nullable=True),
        sa.Column("cfop", sa.String, nullable=False, server_default="5102"),
        sa.Column("origin", sa.String, nullable=False, server_default="0"),
        sa.Column("icms_cst", sa.String, nullable=True),
        sa.Column("icms_csosn", sa.String, nullable=True, server_default="102"),
        sa.Column("pis_cst", sa.String, nullable=False, server_default="07"),
        sa.Column("cofins_cst", sa.String, nullable=False, server_default="07"),
        sa.Column("aliquotas_json", sa.Text, nullable=True),
        sa.Column("effective_from", sa.DateTime, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ptp_market", "product_tax_profiles", ["market_id"])
    op.create_index("ix_ptp_active", "product_tax_profiles", ["market_id", "active"])

    # ── fiscal_documents ──────────────────────────────────────────────────────
    op.create_table(
        "fiscal_documents",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("sale_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("document_type", sa.String, nullable=False, server_default="nfce"),
        sa.Column("provider", sa.String, nullable=False, server_default="focus_nfe"),
        sa.Column("provider_ref", sa.String, nullable=True),
        sa.Column("environment", sa.String, nullable=False, server_default="homologacao"),
        sa.Column("status", sa.String, nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String, nullable=True),
        sa.Column("series", sa.Integer, nullable=True),
        sa.Column("number", sa.Integer, nullable=True),
        sa.Column("access_key", sa.String, nullable=True),
        sa.Column("protocol", sa.String, nullable=True),
        sa.Column("sefaz_status_code", sa.String, nullable=True),
        sa.Column("sefaz_message", sa.Text, nullable=True),
        sa.Column("provider_status", sa.String, nullable=True),
        sa.Column("issued_at", sa.DateTime, nullable=True),
        sa.Column("authorized_at", sa.DateTime, nullable=True),
        sa.Column("canceled_at", sa.DateTime, nullable=True),
        sa.Column("contingency_mode", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("offline_receipt_id", sa.String, nullable=True),
        sa.Column("last_attempt_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("market_id", "sale_id", "document_type", name="uq_fd_market_sale_type"),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_fd_provider_ref"),
    )
    op.create_index("ix_fd_market_status", "fiscal_documents", ["market_id", "status", "created_at"])
    op.create_index("ix_fd_access_key", "fiscal_documents", ["access_key"])
    op.create_index("ix_fd_market_created", "fiscal_documents", ["market_id", "created_at"])

    # ── fiscal_attempts ───────────────────────────────────────────────────────
    op.create_table(
        "fiscal_attempts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("fiscal_document_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("fiscal_documents.id"), nullable=False),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("operation", sa.String, nullable=False, server_default="emit"),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("request_hash", sa.String, nullable=True),
        sa.Column("provider_request_id", sa.String, nullable=True),
        sa.Column("http_status", sa.Integer, nullable=True),
        sa.Column("error_code", sa.String, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("next_retry_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_fa_doc", "fiscal_attempts", ["fiscal_document_id"])
    op.create_index("ix_fa_market_created", "fiscal_attempts", ["market_id", "started_at"])
    op.create_index("ix_fa_next_retry", "fiscal_attempts", ["next_retry_at"])

    # ── fiscal_artifacts ──────────────────────────────────────────────────────
    op.create_table(
        "fiscal_artifacts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("fiscal_document_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("fiscal_documents.id"), nullable=False),
        sa.Column("artifact_type", sa.String, nullable=False),
        sa.Column("storage_key", sa.String, nullable=False),
        sa.Column("sha256", sa.String, nullable=True),
        sa.Column("content_type", sa.String, nullable=False, server_default="application/xml"),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fart_doc", "fiscal_artifacts", ["fiscal_document_id"])

    # ── fiscal_events ─────────────────────────────────────────────────────────
    op.create_table(
        "fiscal_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("fiscal_document_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("fiscal_documents.id"), nullable=False),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=False, server_default="marketfy"),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("metadata_json_sanitized", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fevt_doc", "fiscal_events", ["fiscal_document_id"])
    op.create_index("ix_fevt_market", "fiscal_events", ["market_id"])
    op.create_index("ix_fevt_created", "fiscal_events", ["created_at"])

    # ── fiscal_usage_counters ─────────────────────────────────────────────────
    op.create_table(
        "fiscal_usage_counters",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("owner_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("period_yyyymm", sa.String(6), nullable=False),
        sa.Column("included_limit", sa.Integer, nullable=False, server_default="0"),
        sa.Column("addon_limit", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reserved_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("used_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_billable_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("released_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("blocked_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "market_id", "period_yyyymm", name="uq_fuc_owner_market_period"),
    )
    op.create_index("ix_fuc_owner_period", "fiscal_usage_counters", ["owner_id", "period_yyyymm"])

    # ── fiscal_usage_ledger ───────────────────────────────────────────────────
    op.create_table(
        "fiscal_usage_ledger",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("owner_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("sale_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("fiscal_document_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("fiscal_documents.id"), nullable=True),
        sa.Column("period_yyyymm", sa.String(6), nullable=False),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("reason", sa.String, nullable=True),
        sa.Column("idempotency_key", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_ful_idempotency"),
    )
    op.create_index("ix_ful_owner_period", "fiscal_usage_ledger", ["owner_id", "period_yyyymm"])
    op.create_index("ix_ful_doc", "fiscal_usage_ledger", ["fiscal_document_id"])

    # ── fiscal_emission_packages ──────────────────────────────────────────────
    op.create_table(
        "fiscal_emission_packages",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("owner_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("package_type", sa.String, nullable=False, server_default="nfce_addon"),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="0"),
        sa.Column("remaining", sa.Integer, nullable=False, server_default="0"),
        sa.Column("valid_from", sa.DateTime, nullable=True),
        sa.Column("valid_until", sa.DateTime, nullable=True),
        sa.Column("billing_subscription_id", sa.String, nullable=True),
        sa.Column("payment_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fep_owner", "fiscal_emission_packages", ["owner_id"])
    op.create_index("ix_fep_valid", "fiscal_emission_packages", ["owner_id", "valid_until"])

    # ── provider_webhook_events ───────────────────────────────────────────────
    op.create_table(
        "provider_webhook_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("event_id", sa.String, nullable=False),
        sa.Column("provider_ref", sa.String, nullable=True),
        sa.Column("raw_payload_hash", sa.String, nullable=True),
        sa.Column("processing_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("processed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "event_id", name="uq_pwe_provider_event"),
    )
    op.create_index("ix_pwe_provider_ref", "provider_webhook_events", ["provider_ref"])

    # ── fiscal_notifications ──────────────────────────────────────────────────
    op.create_table(
        "fiscal_notifications",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("owner_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("market_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("notification_type", sa.String, nullable=False),
        sa.Column("severity", sa.String, nullable=False, server_default="info"),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("dedupe_key", sa.String, nullable=True),
        sa.Column("read_at", sa.DateTime, nullable=True),
        sa.Column("sent_email_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("dedupe_key", name="uq_fn_dedupe"),
    )
    op.create_index("ix_fn_owner", "fiscal_notifications", ["owner_id"])
    op.create_index("ix_fn_market", "fiscal_notifications", ["market_id"])
    op.create_index("ix_fn_read", "fiscal_notifications", ["owner_id", "read_at"])

    # Adicionar monthly_nfce_limit ao plans (billing fiscal)
    op.add_column("plans", sa.Column("monthly_nfce_limit", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "monthly_nfce_limit")
    op.drop_table("fiscal_notifications")
    op.drop_table("provider_webhook_events")
    op.drop_table("fiscal_emission_packages")
    op.drop_table("fiscal_usage_ledger")
    op.drop_table("fiscal_usage_counters")
    op.drop_table("fiscal_events")
    op.drop_table("fiscal_artifacts")
    op.drop_table("fiscal_attempts")
    op.drop_table("fiscal_documents")
    op.drop_table("product_tax_profiles")
    op.drop_table("fiscal_tenant_configs")
