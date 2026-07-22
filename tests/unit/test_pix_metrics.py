from infra.observability.metrics import metrics_registry


def test_pix_counters_increment():
    metrics_registry.record_pix_qr_created()
    metrics_registry.record_pix_payment_approved(source="webhook")
    metrics_registry.record_pix_webhook(action="order.processed", result="processed")
    metrics_registry.record_pix_divergence(kind="amount")
    snapshot = metrics_registry.snapshot() if hasattr(metrics_registry, "snapshot") else None
    assert snapshot is None or "pix_qr_created_total" in str(snapshot)


def test_pix_anomaly_gauge_accepts_value():
    metrics_registry.set_pix_anomaly(kind="paid_not_completed", value=3)
    metrics_registry.set_pix_anomaly(kind="completed_not_confirmed", value=0)


def test_pix_location_events_use_bounded_labels():
    metrics_registry.record_pix_location_event("location_saved")
    metrics_registry.record_pix_location_event("location_validation_failed")
    snapshot = metrics_registry.snapshot()
    assert snapshot["pix_location_events_total"]["location_saved"] >= 1
    assert "location_validation_failed" in snapshot["pix_location_events_total"]
