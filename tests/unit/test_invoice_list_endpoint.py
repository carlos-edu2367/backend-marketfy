from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.web.routers import billing as billing_router


def test_invoice_endpoints_registered():
    paths = {r.path for r in billing_router.router.routes}
    assert "/invoices" in paths
    assert "/invoices/{invoice_id}" in paths
    assert "/invoices/{invoice_id}/checkout" in paths
