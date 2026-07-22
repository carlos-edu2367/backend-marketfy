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
        self._fiscal_rule_events: Dict[Tuple[str, str], int] = defaultdict(int)
        self._fiscal_contracts: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
        self._pix_qr_created: int = 0
        self._pix_payment_approved: Dict[str, int] = defaultdict(int)
        self._pix_payment_expired: int = 0
        self._pix_attempt_canceled: int = 0
        self._pix_webhook: Dict[Tuple[str, str], int] = defaultdict(int)
        self._pix_divergence: Dict[str, int] = defaultdict(int)
        self._pix_token_refresh: Dict[str, int] = defaultdict(int)
        self._pix_verify: Dict[str, int] = defaultdict(int)
        self._pix_provider_call: Dict[Tuple[str, str], int] = defaultdict(int)
        self._pix_provider_latency: Dict[str, dict] = defaultdict(
            lambda: {"count": 0, "sum": 0.0, "max": 0.0}
        )
        self._pix_reconciliation: Dict[str, int] = defaultdict(int)
        self._pix_anomaly: Dict[str, int] = defaultdict(int)
        self._pix_location_events: Dict[str, int] = defaultdict(int)

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

    def record_fiscal_rule_event(self, *, market_id: str, mode: str, event: str) -> None:
        allowed_events = {
            "fiscal_rule_missing",
            "fiscal_rule_ambiguous",
            "snapshot_invalid",
            "contract_rejected",
            "unsupported_tax_group",
        }
        if event not in allowed_events:
            return
        with self._lock:
            # market_id remains available to structured logs/audit only. A hash is
            # still an identifier and must not become a Prometheus label.
            self._fiscal_rule_events[(_bounded_mode(mode), event)] += 1

    def record_fiscal_contract(
        self,
        *,
        market_id: str,
        contract_version: str,
        enforcement_mode: str,
        result_code: str,
        path: str,
    ) -> None:
        """Record v2 rollout outcomes with a small, tenant-free label set."""
        del market_id  # Deliberately not retained in metrics; use structured audit logs.
        with self._lock:
            self._fiscal_contracts[
                (
                    _bounded_contract_version(contract_version),
                    _bounded_mode(enforcement_mode),
                    _bounded_fiscal_result(result_code),
                    _bounded_path(path),
                )
            ] += 1

    # --- Pix (Mercado Pago) ---
    def record_pix_qr_created(self) -> None:
        with self._lock:
            self._pix_qr_created += 1

    def record_pix_payment_approved(self, source: str) -> None:
        with self._lock:
            self._pix_payment_approved[_safe_label(source)] += 1

    def record_pix_payment_expired(self) -> None:
        with self._lock:
            self._pix_payment_expired += 1

    def record_pix_attempt_canceled(self) -> None:
        with self._lock:
            self._pix_attempt_canceled += 1

    def record_pix_webhook(self, action: str, result: str) -> None:
        with self._lock:
            self._pix_webhook[(_safe_label(action), _safe_label(result))] += 1

    def record_pix_divergence(self, kind: str) -> None:
        with self._lock:
            self._pix_divergence[_safe_label(kind)] += 1

    def record_pix_token_refresh(self, result: str) -> None:
        with self._lock:
            self._pix_token_refresh[_safe_label(result)] += 1

    def record_pix_verify(self, result: str) -> None:
        with self._lock:
            self._pix_verify[_safe_label(result)] += 1

    def record_pix_provider_call(self, op: str, status: str, latency_ms: int) -> None:
        with self._lock:
            op_label = _safe_label(op)
            self._pix_provider_call[(op_label, _safe_label(status))] += 1
            bucket = self._pix_provider_latency[op_label]
            bucket["count"] += 1
            bucket["sum"] += latency_ms
            bucket["max"] = max(bucket["max"], latency_ms)

    def record_pix_reconciliation(self, result: str) -> None:
        with self._lock:
            self._pix_reconciliation[_safe_label(result)] += 1

    def set_pix_anomaly(self, kind: str, value: int) -> None:
        with self._lock:
            self._pix_anomaly[_safe_label(kind)] = value

    def record_pix_location_event(self, event: str) -> None:
        allowed = {"location_saved", "location_validation_failed", "location_missing"}
        if event not in allowed:
            return
        with self._lock:
            self._pix_location_events[event] += 1

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
                "fiscal_rule_events_total": dict(self._fiscal_rule_events),
                "fiscal_contract_total": dict(self._fiscal_contracts),
                "pix_qr_created_total": self._pix_qr_created,
                "pix_payment_approved_total": dict(self._pix_payment_approved),
                "pix_payment_expired_total": self._pix_payment_expired,
                "pix_attempt_canceled_total": self._pix_attempt_canceled,
                "pix_webhook_total": dict(self._pix_webhook),
                "pix_divergence_total": dict(self._pix_divergence),
                "pix_token_refresh_total": dict(self._pix_token_refresh),
                "pix_verify_manual_total": dict(self._pix_verify),
                "pix_provider_call_total": dict(self._pix_provider_call),
                "pix_provider_call_ms": {key: value.copy() for key, value in self._pix_provider_latency.items()},
                "pix_reconciliation_runs_total": dict(self._pix_reconciliation),
                "pix_anomaly": dict(self._pix_anomaly),
                "pix_location_events_total": dict(self._pix_location_events),
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
        lines.append("# TYPE marketfy_fiscal_rule_events_total counter")
        for (mode, event), count in snapshot["fiscal_rule_events_total"].items():
            lines.append(
                "marketfy_fiscal_rule_events_total"
                f'{{mode="{mode}",event="{event}"}} {count}'
            )
        lines.append("# TYPE marketfy_fiscal_contract_total counter")
        for (contract_version, enforcement_mode, result_code, path), count in snapshot[
            "fiscal_contract_total"
        ].items():
            lines.append(
                "marketfy_fiscal_contract_total"
                f'{{contract_version="{contract_version}",enforcement_mode="{enforcement_mode}",'
                f'result_code="{result_code}",path="{path}"}} {count}'
            )
        lines.append("# TYPE marketfy_pix_qr_created_total counter")
        lines.append(f"marketfy_pix_qr_created_total {snapshot['pix_qr_created_total']}")
        lines.append("# TYPE marketfy_pix_payment_approved_total counter")
        for source, count in snapshot["pix_payment_approved_total"].items():
            lines.append(f'marketfy_pix_payment_approved_total{{source="{source}"}} {count}')
        lines.append("# TYPE marketfy_pix_payment_expired_total counter")
        lines.append(f"marketfy_pix_payment_expired_total {snapshot['pix_payment_expired_total']}")
        lines.append("# TYPE marketfy_pix_attempt_canceled_total counter")
        lines.append(f"marketfy_pix_attempt_canceled_total {snapshot['pix_attempt_canceled_total']}")
        lines.append("# TYPE marketfy_pix_webhook_total counter")
        for (action, result), count in snapshot["pix_webhook_total"].items():
            lines.append(f'marketfy_pix_webhook_total{{action="{action}",result="{result}"}} {count}')
        lines.append("# TYPE marketfy_pix_divergence_total counter")
        for kind, count in snapshot["pix_divergence_total"].items():
            lines.append(f'marketfy_pix_divergence_total{{kind="{kind}"}} {count}')
        lines.append("# TYPE marketfy_pix_token_refresh_total counter")
        for result, count in snapshot["pix_token_refresh_total"].items():
            lines.append(f'marketfy_pix_token_refresh_total{{result="{result}"}} {count}')
        lines.append("# TYPE marketfy_pix_verify_manual_total counter")
        for result, count in snapshot["pix_verify_manual_total"].items():
            lines.append(f'marketfy_pix_verify_manual_total{{result="{result}"}} {count}')
        lines.append("# TYPE marketfy_pix_provider_call_total counter")
        for (op, status), count in snapshot["pix_provider_call_total"].items():
            lines.append(f'marketfy_pix_provider_call_total{{op="{op}",status="{status}"}} {count}')
        lines.append("# TYPE marketfy_pix_provider_call_ms_sum counter")
        for op, values in snapshot["pix_provider_call_ms"].items():
            labels = f'op="{op}"'
            lines.append(f"marketfy_pix_provider_call_ms_sum{{{labels}}} {values['sum']:.6f}")
            lines.append(f"marketfy_pix_provider_call_ms_count{{{labels}}} {values['count']}")
            lines.append(f"marketfy_pix_provider_call_ms_max{{{labels}}} {values['max']:.6f}")
        lines.append("# TYPE marketfy_pix_reconciliation_runs_total counter")
        for result, count in snapshot["pix_reconciliation_runs_total"].items():
            lines.append(f'marketfy_pix_reconciliation_runs_total{{result="{result}"}} {count}')
        lines.append("# TYPE marketfy_pix_anomaly gauge")
        for kind, value in snapshot["pix_anomaly"].items():
            lines.append(f'marketfy_pix_anomaly{{kind="{kind}"}} {value}')
        lines.append("# TYPE marketfy_pix_location_events_total counter")
        for event, count in snapshot["pix_location_events_total"].items():
            lines.append(f'marketfy_pix_location_events_total{{event="{event}"}} {count}')
        return "\n".join(lines) + "\n"


def _safe_label(value: str | None) -> str:
    value = (value or "unknown").lower()
    allowed = []
    for char in value[:64]:
        allowed.append(char if char.isalnum() or char in {"_", "-", "."} else "_")
    return "".join(allowed) or "unknown"


def _bounded_mode(value: str | None) -> str:
    return value if value in {"off", "warn", "block"} else "other"


def _bounded_contract_version(value: str | None) -> str:
    return (
        value
        if value
        in {
            "legacy",
            "marketfy.fiscal-tax-snapshot.v1",
            "marketfy.fiscal-tax-snapshot.v2",
        }
        else "other"
    )


def _bounded_fiscal_result(value: str | None) -> str:
    allowed = {
        "success",
        "queued",
        "payload_invalid",
        "payload_missing",
        "sale.fiscal_rule_missing",
        "sale.fiscal_rule_invalid",
        "sale.fiscal_connection_required",
    }
    return value if value in allowed else "other"


def _bounded_path(value: str | None) -> str:
    return value if value in {"legacy", "v2"} else "other"


def _safe_route(route: str) -> str:
    return route[:160] if route else "unknown"


metrics_registry = MetricsRegistry()

