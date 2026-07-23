"""
Regression: async reconciliation resolves a document's SEFAZ status but never
finalized its fiscal quota reservation. Every NFC-e resolved via
reconcile_nfce_job (the default path for the Neectify provider) stayed
permanently counted as 'reserved' — never 'used', never released — until the
monthly counter reset wiped reserved_count back to zero and masked it again.

_finalize_reconciled_quota() is the fix: called after reconcile_document()
returns a terminal document, it closes the reservation the same way the
synchronous emit_nfce_job path already does inline.
"""
import os
import sys
import uuid
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.jobs.fiscal_jobs import _finalize_reconciled_quota
from domain.fiscal import FiscalDocument, FiscalDocumentStatus, FiscalEnvironment


def _make_doc(status: FiscalDocumentStatus) -> FiscalDocument:
    return FiscalDocument(
        market_id=uuid.uuid4(),
        sale_id=uuid.uuid4(),
        status=status,
        environment=FiscalEnvironment.HOMOLOGATION,
        provider="neectify_fiscal",
    )


@pytest.mark.asyncio
async def test_authorized_document_consumes_quota():
    doc = _make_doc(FiscalDocumentStatus.AUTHORIZED)
    quota_service = AsyncMock()
    owner_id, market_id = uuid.uuid4(), uuid.uuid4()

    await _finalize_reconciled_quota(
        doc, quota_service,
        owner_id=owner_id, market_id=market_id, period="202607", consuming_addon=True,
    )

    quota_service.consume.assert_awaited_once_with(
        owner_id=owner_id, market_id=market_id, period="202607",
        consuming_addon=True, sale_id=doc.sale_id, fiscal_document_id=doc.id,
    )
    quota_service.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_document_releases_quota():
    doc = _make_doc(FiscalDocumentStatus.REJECTED)
    quota_service = AsyncMock()
    owner_id, market_id = uuid.uuid4(), uuid.uuid4()

    await _finalize_reconciled_quota(
        doc, quota_service,
        owner_id=owner_id, market_id=market_id, period="202607", consuming_addon=False,
    )

    quota_service.release.assert_awaited_once_with(
        owner_id=owner_id, period="202607", market_id=market_id,
        reason="rejected", fiscal_document_id=doc.id,
    )
    quota_service.consume.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_action_required_releases_quota():
    """Circuit breaker exhausted: reprocess_document() will reserve a fresh
    unit if support retries later, so this reservation must not stay held."""
    doc = _make_doc(FiscalDocumentStatus.MANUAL_ACTION_REQUIRED)
    quota_service = AsyncMock()

    await _finalize_reconciled_quota(
        doc, quota_service,
        owner_id=uuid.uuid4(), market_id=uuid.uuid4(), period="202607", consuming_addon=False,
    )

    quota_service.release.assert_awaited_once()
    quota_service.consume.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_terminal_document_does_not_touch_quota():
    doc = _make_doc(FiscalDocumentStatus.PROCESSING)
    quota_service = AsyncMock()

    await _finalize_reconciled_quota(
        doc, quota_service,
        owner_id=uuid.uuid4(), market_id=uuid.uuid4(), period="202607", consuming_addon=False,
    )

    quota_service.consume.assert_not_awaited()
    quota_service.release.assert_not_awaited()
