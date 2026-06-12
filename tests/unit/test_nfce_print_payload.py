from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.fiscal import (  # noqa: E402
    FiscalArtifact,
    FiscalArtifactType,
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalEnvironment,
)
from domain.shared import BusinessRuleException  # noqa: E402


AUTHORIZED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe52260612345678000195650010000000211000000210" versao="4.00">
      <ide>
        <serie>1</serie>
        <nNF>21</nNF>
        <dhEmi>2026-06-12T18:00:00-03:00</dhEmi>
      </ide>
      <emit>
        <CNPJ>12345678000195</CNPJ>
        <xNome>Mercado Teste Ltda</xNome>
        <xFant>Mercado Teste</xFant>
        <IE>109876543</IE>
        <enderEmit>
          <xLgr>Rua A</xLgr>
          <nro>123</nro>
          <xBairro>Centro</xBairro>
          <xMun>Goiania</xMun>
          <UF>GO</UF>
          <CEP>74000000</CEP>
        </enderEmit>
      </emit>
      <det nItem="1">
        <prod>
          <cProd>SKU-1</cProd>
          <xProd>Refrigerante Cola</xProd>
          <qCom>2.0000</qCom>
          <uCom>UN</uCom>
          <vUnCom>5.50</vUnCom>
          <vProd>11.00</vProd>
        </prod>
      </det>
      <total>
        <ICMSTot>
          <vNF>11.00</vNF>
        </ICMSTot>
      </total>
      <pag>
        <detPag>
          <tPag>03</tPag>
          <vPag>11.00</vPag>
        </detPag>
      </pag>
    </infNFe>
    <infNFeSupl>
      <qrCode><![CDATA[https://nfe.sefaz.go.gov.br/qrcode?p=52260612345678000195650010000000211000000210|2|1|1|HASH]]></qrCode>
      <urlChave>https://nfe.sefaz.go.gov.br/consulta</urlChave>
    </infNFeSupl>
  </NFe>
  <protNFe>
    <infProt>
      <chNFe>52260612345678000195650010000000211000000210</chNFe>
      <nProt>152260000000001</nProt>
      <dhRecbto>2026-06-12T18:00:05-03:00</dhRecbto>
      <cStat>100</cStat>
      <xMotivo>Autorizado o uso da NF-e</xMotivo>
    </infProt>
  </protNFe>
</nfeProc>
"""


def _authorized_doc(market_id: uuid.UUID, sale_id: uuid.UUID) -> FiscalDocument:
    return FiscalDocument(
        market_id=market_id,
        sale_id=sale_id,
        provider="neectify_fiscal",
        provider_ref="nfce_123",
        environment=FiscalEnvironment.PRODUCTION,
        status=FiscalDocumentStatus.AUTHORIZED,
        access_key="52260612345678000195650010000000211000000210",
        protocol="152260000000001",
        series=1,
        number=21,
        authorized_at=datetime(2026, 6, 12, 21, 0, 5),
    )


def test_parse_nfce_for_print_extracts_authorized_xml_fields():
    from application.services.fiscal.nfce_print_parser import parse_nfce_for_print

    payload = parse_nfce_for_print(AUTHORIZED_XML)

    assert payload["access_key"] == "52260612345678000195650010000000211000000210"
    assert payload["protocol"] == "152260000000001"
    assert payload["authorized_at"] == "2026-06-12T18:00:05-03:00"
    assert payload["sefaz_cstat"] == "100"
    assert payload["sefaz_reason"] == "Autorizado o uso da NF-e"
    assert payload["qr_code_url"].startswith("https://nfe.sefaz.go.gov.br/qrcode?p=")
    assert payload["url_chave"] == "https://nfe.sefaz.go.gov.br/consulta"
    assert payload["issuer"]["legal_name"] == "Mercado Teste Ltda"
    assert payload["issuer"]["cnpj"] == "12345678000195"
    assert payload["series"] == "1"
    assert payload["number"] == "21"
    assert payload["total_amount"] == "11.00"
    assert payload["items"] == [
        {
            "n_item": "1",
            "code": "SKU-1",
            "description": "Refrigerante Cola",
            "quantity": "2.0000",
            "unit": "UN",
            "unit_amount": "5.50",
            "total_amount": "11.00",
        }
    ]
    assert payload["payments"] == [{"method": "03", "amount": "11.00"}]


@pytest.mark.asyncio
async def test_nfce_print_service_rejects_non_authorized_document():
    from application.services.fiscal.nfce_print_service import NfcePrintService

    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    doc = _authorized_doc(market_id, sale_id)
    doc.status = FiscalDocumentStatus.PROCESSING

    doc_repo = AsyncMock()
    doc_repo.get_by_sale.return_value = doc

    service = NfcePrintService(
        doc_repo=doc_repo,
        artifact_repo=AsyncMock(),
        storage=AsyncMock(),
        provider=AsyncMock(),
    )

    with pytest.raises(BusinessRuleException, match="ainda nao autorizada"):
        await service.prepare_sale_print_payload(market_id, sale_id)


@pytest.mark.asyncio
async def test_nfce_print_service_uses_stored_authorized_xml():
    from application.services.fiscal.nfce_print_service import NfcePrintService

    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    doc = _authorized_doc(market_id, sale_id)
    artifact = FiscalArtifact(
        fiscal_document_id=doc.id,
        artifact_type=FiscalArtifactType.XML_AUTHORIZED,
        storage_key="fiscal/producao/market/doc/xml_authorized.xml",
        sha256=None,
        content_type="application/xml",
    )

    doc_repo = AsyncMock()
    doc_repo.get_by_sale.return_value = doc
    artifact_repo = AsyncMock()
    artifact_repo.get_by_doc_and_type.return_value = artifact
    storage = MagicMock()
    storage.load = AsyncMock(return_value=AUTHORIZED_XML)
    provider = AsyncMock()

    service = NfcePrintService(
        doc_repo=doc_repo,
        artifact_repo=artifact_repo,
        storage=storage,
        provider=provider,
    )

    payload = await service.prepare_sale_print_payload(market_id, sale_id)

    assert payload["document"]["id"] == str(doc.id)
    assert payload["access_key"] == "52260612345678000195650010000000211000000210"
    provider.download_xml.assert_not_called()


@pytest.mark.asyncio
async def test_nfce_print_service_downloads_and_stores_xml_when_missing():
    from application.services.fiscal.nfce_print_service import NfcePrintService

    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    doc = _authorized_doc(market_id, sale_id)

    doc_repo = AsyncMock()
    doc_repo.get_by_sale.return_value = doc
    artifact_repo = AsyncMock()
    artifact_repo.get_by_doc_and_type.return_value = None
    artifact_repo.save.return_value = None
    storage = MagicMock()
    storage.save = AsyncMock(return_value="abc123")
    storage.build_storage_key.return_value = "fiscal/producao/market/doc/xml_authorized.xml"
    provider = AsyncMock()
    provider.download_xml.return_value = AUTHORIZED_XML

    service = NfcePrintService(
        doc_repo=doc_repo,
        artifact_repo=artifact_repo,
        storage=storage,
        provider=provider,
    )

    payload = await service.prepare_sale_print_payload(market_id, sale_id)

    provider.download_xml.assert_called_once_with("nfce_123", "", "producao")
    artifact_repo.save.assert_called_once()
    assert payload["qr_code_url"].startswith("https://nfe.sefaz.go.gov.br/qrcode?p=")
