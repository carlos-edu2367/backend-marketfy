"""Jobs ARQ de billing/assinatura: geração e reconciliação de faturas."""

from __future__ import annotations

from datetime import datetime, timedelta

from infra.config.logger import get_logger

logger = get_logger("billing_jobs")

INVOICE_LEAD_DAYS = 5  # gera a próxima fatura ~5 dias antes do vencimento


async def generate_due_invoices(
    ctx: dict,
    *,
    sub_repo=None,
    invoice_service=None,
    invoice_repo=None,
    email_gateway=None,
    user_repo=None,
) -> dict:
    """Gera a próxima fatura para assinaturas invoice próximas do vencimento.

    Injetável para teste. Em produção (sub_repo=None), monta as dependências
    a partir de uma sessão nova.
    """
    if sub_repo is None:
        return await _run_generate_due_invoices_with_session(ctx)

    cutoff = datetime.utcnow() + timedelta(days=INVOICE_LEAD_DAYS)
    subs = await sub_repo.list_invoice_subs_expiring_within(cutoff)
    generated = 0
    emailed = 0
    for sub in subs:
        try:
            invoice = await invoice_service.generate_next_invoice(sub)
            if invoice is None:
                continue
            generated += 1
            await invoice_repo.mark_notified(invoice.id, datetime.utcnow())
            if email_gateway is not None and user_repo is not None:
                try:
                    user = await user_repo.get_by_id(sub.owner_id)
                    if user is not None:
                        email = user.email.value if hasattr(user.email, "value") else user.email
                        await email_gateway.send_invoice_available(
                            to_email=email,
                            to_name=getattr(user, "full_name", getattr(user, "name", "")),
                            amount=str(invoice.amount),
                            due_date=invoice.due_date.strftime("%d/%m/%Y") if invoice.due_date else "",
                            checkout_url=invoice.checkout_url or "",
                        )
                        emailed += 1
                except Exception as exc:  # e-mail nunca bloqueia a geração
                    logger.warning("invoice_email_failed", extra={"extra_data": {"error": str(exc)}})
        except Exception as exc:
            logger.error("generate_invoice_error", extra={"extra_data": {"sub_id": str(sub.id), "error": str(exc)}})
    logger.info("generate_due_invoices_done", extra={"extra_data": {"generated": generated, "emailed": emailed}})
    return {"generated": generated, "emailed": emailed}


async def _run_generate_due_invoices_with_session(ctx: dict) -> dict:
    from infra.database.setup import async_session_factory
    from infra.config.settings import get_settings
    from infra.clients.billing_core_client import BillingCoreClient
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository, SQLAlchemyUserRepository
    from application.services.invoice_service import InvoiceService

    settings = get_settings()
    async with async_session_factory() as session:
        sub_repo = SQLAlchemyBillingSubscriptionRepository(session)
        invoice_repo = SQLAlchemyBillingInvoiceRepository(session)
        invoice_service = InvoiceService(
            invoice_repo=invoice_repo,
            subscription_repo=sub_repo,
            plan_repo=SQLAlchemyPlanRepository(session),
            billing_client=BillingCoreClient(),
            settings=settings,
        )
        email_gateway = _build_email_gateway(settings)  # ver Task 14 (retorna None se sem config)
        result = await generate_due_invoices(
            ctx, sub_repo=sub_repo, invoice_service=invoice_service,
            invoice_repo=invoice_repo, email_gateway=email_gateway,
            user_repo=SQLAlchemyUserRepository(session),
        )
        await session.commit()
        return result


def _build_email_gateway(settings):
    """Placeholder até a Task 14; retorna None (sem e-mail) por ora."""
    return None
