"""Adapter Mailgun (API v3) via httpx — portado do padrão Neectify Food."""

from __future__ import annotations

import httpx

from infra.config.logger import get_logger

logger = get_logger("mailgun")


class EmailDeliveryError(Exception):
    pass


class MailgunEmailGateway:
    def __init__(self, api_key: str, domain: str, from_email: str, from_name: str,
                 api_base_url: str = "https://api.mailgun.net") -> None:
        if not api_key:
            raise ValueError("MAILGUN_API_KEY não configurado.")
        if not domain:
            raise ValueError("MAILGUN_DOMAIN não configurado.")
        self._api_key = api_key
        self._domain = domain
        self._from_email = from_email
        self._from_name = from_name
        self._send_url = f"{api_base_url.rstrip('/')}/v3/{domain}/messages"

    async def send_invoice_available(self, *, to_email: str, to_name: str, amount: str,
                                     due_date: str, checkout_url: str) -> None:
        data = {
            "from": f"{self._from_name} <{self._from_email}>",
            "to": f"{to_name} <{to_email}>",
            "subject": "Você tem uma fatura disponível para pagamento — Marketfy",
            "html": _build_invoice_html(to_name, amount, due_date, checkout_url),
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self._send_url, data=data, auth=("api", self._api_key))
        if resp.status_code not in (200, 202):
            logger.error("mailgun_delivery_failed status=%s body=%s", resp.status_code, resp.text[:300])
            raise EmailDeliveryError(f"Mailgun retornou status {resp.status_code}.")


def _build_invoice_html(name: str, amount: str, due_date: str, checkout_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:system-ui,-apple-system,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:40px 16px;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">
        <tr><td style="background:#18181b;padding:24px 32px;">
          <span style="color:#f97316;font-size:22px;font-weight:800;">Marketfy</span>
        </td></tr>
        <tr><td style="padding:36px 32px 28px;">
          <h1 style="margin:0 0 8px;font-size:20px;font-weight:700;color:#18181b;">Fatura disponível</h1>
          <p style="margin:0 0 24px;font-size:15px;color:#52525b;line-height:1.65;">
            Olá, {name}.<br>
            Sua fatura de assinatura no valor de <strong>R$ {amount}</strong> está disponível.
            Vencimento em <strong>{due_date}</strong>. Pague para manter seu acesso ativo.
          </p>
          <a href="{checkout_url}" style="display:inline-block;background:#f97316;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;padding:14px 32px;border-radius:10px;">
            Pagar fatura
          </a>
        </td></tr>
        <tr><td style="border-top:1px solid #f4f4f5;padding:16px 32px;">
          <p style="margin:0;font-size:11px;color:#a1a1aa;text-align:center;">© Marketfy · E-mail automático, não responda.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
