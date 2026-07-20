import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

from infra.observability.metrics import MetricsRegistry  # noqa: E402


def test_fiscal_rule_metric_hashes_market_and_exposes_only_safe_labels():
    registry = MetricsRegistry()

    registry.record_fiscal_rule_event(
        market_id="5e224e27-0b84-4bcd-ae7c-83fd8e09d40c",
        mode="warn",
        event="fiscal_rule_missing",
    )

    output = registry.to_prometheus_text()

    assert "marketfy_fiscal_rule_events_total" in output
    assert 'mode="warn"' in output
    assert 'event="fiscal_rule_missing"' in output
    assert "5e224e27" not in output
