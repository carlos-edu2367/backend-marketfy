import re
import sys
from pathlib import Path

app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))


def test_no_token_logging_in_pix_sources():
    root = app_dir
    suspicious = re.compile(
        r"logger\.(info|warning|error|debug)\([^)]*(access_token|refresh_token|client_secret|qr_data|MP_WEBHOOK_SECRET)"
    )
    offenders = []
    for path in list(root.rglob("*pix*.py")) + list(root.rglob("*mercadopago*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if suspicious.search(text):
            offenders.append(str(path))
    assert offenders == [], f"Possível log de segredo em: {offenders}"
