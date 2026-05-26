from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Dict, Tuple


class MetricsRegistry:
    def __init__(self):
        self._lock = Lock()
        self._requests: Dict[Tuple[str, str, int], int] = defaultdict(int)
        self._errors: Dict[Tuple[str, str, int], int] = defaultdict(int)
        self._latency: Dict[Tuple[str, str], dict] = defaultdict(
            lambda: {"count": 0, "sum": 0.0, "max": 0.0}
        )
        self._auth_failures: Dict[str, int] = defaultdict(int)
        self._rate_limits: Dict[str, int] = defaultdict(int)
        self._sync: Dict[str, int] = defaultdict(int)
        self._billing_webhooks: Dict[str, int] = defaultdict(int)
        self._fiscal_invoices: Dict[str, int] = defaultdict(int)

    def record_request(self, method: str, route: str, status_code: int, duration_ms: float) -> None:
        method = method.upper()
        route = _safe_route(route)
        with self._lock:
            self._requests[(method, route, status_code)] += 1
            if status_code >= 400:
                self._errors[(method, route, status_code)] += 1
            bucket = self._latency[(method, route)]
            bucket["count"] += 1
            bucket["sum"] += duration_ms
            bucket["max"] = max(bucket["max"], duration_ms)

    def record_auth_failure(self, reason: str) -> None:
        with self._lock:
            self._auth_failures[_safe_label(reason)] += 1

    def record_rate_limit(self, bucket: str) -> None:
        with self._lock:
            self._rate_limits[_safe_label(bucket)] += 1

    def record_sync(self, result: str) -> None:
        with self._lock:
            self._sync[_safe_label(result)] += 1

    def record_billing_webhook(self, event_id: str, result: str) -> None:
        with self._lock:
            self._billing_webhooks[_safe_label(result)] += 1

    def record_fiscal_invoice(self, sale_id: str, result: str) -> None:
        with self._lock:
            self._fiscal_invoices[_safe_label(result)] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "api_requests_total": dict(self._requests),
                "api_errors_total": dict(self._errors),
                "api_latency_ms": {key: value.copy() for key, value in self._latency.items()},
                "auth_failures_total": dict(self._auth_failures),
                "rate_limits_total": dict(self._rate_limits),
                "sales_sync_total": dict(self._sync),
                "billing_webhooks_total": dict(self._billing_webhooks),
                "fiscal_invoices_total": dict(self._fiscal_invoices),
            }

    def to_prometheus_text(self) -> str:
        lines = [
            "# HELP marketfy_api_requests_total Total HTTP requests.",
            "# TYPE marketfy_api_requests_total counter",
        ]
        snapshot = self.snapshot()
        for (method, route, status), count in snapshot["api_requests_total"].items():
            lines.append(
                f'marketfy_api_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}'
            )
        lines.extend([
            "# HELP marketfy_api_errors_total Total HTTP error responses.",
            "# TYPE marketfy_api_errors_total counter",
        ])
        for (method, route, status), count in snapshot["api_errors_total"].items():
            lines.append(
                f'marketfy_api_errors_total{{method="{method}",route="{route}",status="{status}"}} {count}'
            )
        lines.extend([
            "# HELP marketfy_api_latency_ms_sum Sum of request latency in ms.",
            "# TYPE marketfy_api_latency_ms_sum counter",
        ])
        for (method, route), values in snapshot["api_latency_ms"].items():
            labels = f'method="{method}",route="{route}"'
            lines.append(f"marketfy_api_latency_ms_sum{{{labels}}} {values['sum']:.6f}")
            lines.append(f"marketfy_api_latency_ms_count{{{labels}}} {values['count']}")
            lines.append(f"marketfy_api_latency_ms_max{{{labels}}} {values['max']:.6f}")
        for metric_name, values in (
            ("marketfy_auth_failures_total", snapshot["auth_failures_total"]),
            ("marketfy_rate_limits_total", snapshot["rate_limits_total"]),
            ("marketfy_sales_sync_total", snapshot["sales_sync_total"]),
            ("marketfy_billing_webhooks_total", snapshot["billing_webhooks_total"]),
            ("marketfy_fiscal_invoices_total", snapshot["fiscal_invoices_total"]),
        ):
            lines.append(f"# TYPE {metric_name} counter")
            for result, count in values.items():
                lines.append(f'{metric_name}{{result="{result}"}} {count}')
        return "\n".join(lines) + "\n"


def _safe_label(value: str | None) -> str:
    value = (value or "unknown").lower()
    allowed = []
    for char in value[:64]:
        allowed.append(char if char.isalnum() or char in {"_", "-", "."} else "_")
    return "".join(allowed) or "unknown"


def _safe_route(route: str) -> str:
    return route[:160] if route else "unknown"


metrics_registry = MetricsRegistry()

