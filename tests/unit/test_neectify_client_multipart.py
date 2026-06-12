"""
Regressão (auditoria E2E 2026-06-12).

O NeectifyFiscalClient NÃO pode fixar Content-Type: application/json como header
default do AsyncClient. Sendo default, o httpx o re-injeta em toda requisição e,
por já estar presente, NÃO é substituído pelo boundary multipart gerado por
`files=` — fazendo o upload de certificado ir como corpo multipart rotulado
"application/json" → o FastAPI do Fiscal não acha os campos → 422.
"""
import httpx
import pytest

from infra.clients.neectify_fiscal_client import NeectifyFiscalClient


def test_client_has_no_default_content_type_header():
    client = NeectifyFiscalClient(api_key="nf_test_a_b", base_url="http://fiscal")
    http = client._get_client()
    assert "content-type" not in {k.lower() for k in http.headers}


@pytest.mark.asyncio
async def test_multipart_upload_sends_multipart_content_type():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(201, json={"id": "cert_1"})

    client = NeectifyFiscalClient(api_key="nf_test_a_b", base_url="http://fiscal")
    # injeta transporte mock preservando os headers default do client
    http = client._get_client()
    client._client = httpx.AsyncClient(
        base_url="http://fiscal", headers=http.headers,
        transport=httpx.MockTransport(handler),
    )

    files = {
        "file": ("certificate.pfx", b"PFX", "application/octet-stream"),
        "password": (None, "secret"),
        "environment": (None, "homologation"),
    }
    await client.request("POST", "/v1/issuers/iss_1/certificates", files=files)
    assert captured["content_type"].startswith("multipart/form-data; boundary=")


@pytest.mark.asyncio
async def test_json_request_sends_application_json_content_type():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"ok": True})

    client = NeectifyFiscalClient(api_key="nf_test_a_b", base_url="http://fiscal")
    http = client._get_client()
    client._client = httpx.AsyncClient(
        base_url="http://fiscal", headers=http.headers,
        transport=httpx.MockTransport(handler),
    )
    await client.request("POST", "/v1/issuers", json={"a": 1})
    assert captured["content_type"] == "application/json"
