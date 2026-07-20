from __future__ import annotations

import os
import sys
from dataclasses import dataclass

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.jobs.billing_jobs import _build_email_gateway


@dataclass
class S:
    MAILGUN_API_KEY: str = ""
    MAILGUN_DOMAIN: str = ""
    MAILGUN_FROM_EMAIL: str = "n@x.com"
    MAILGUN_FROM_NAME: str = "Marketfy"
    MAILGUN_API_BASE_URL: str = "https://api.mailgun.net"


def test_returns_none_without_config():
    assert _build_email_gateway(S()) is None


def test_returns_gateway_with_config():
    gw = _build_email_gateway(S(MAILGUN_API_KEY="k", MAILGUN_DOMAIN="mg.x.com"))
    assert gw is not None
    assert hasattr(gw, "send_invoice_available")
