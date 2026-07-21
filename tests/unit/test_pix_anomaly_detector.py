import pytest
from unittest.mock import patch


class FakeRepo:
    async def count_paid_not_completed(self): return 2
    async def count_completed_not_confirmed(self): return 0


@pytest.mark.asyncio
async def test_scan_sets_gauges_and_returns_counts():
    from application.jobs.pix_jobs import PixAnomalyDetector
    det = PixAnomalyDetector(FakeRepo())
    with patch("application.jobs.pix_jobs.metrics_registry") as mreg:
        result = await det.scan()
        mreg.set_pix_anomaly.assert_any_call(kind="paid_not_completed", value=2)
        mreg.set_pix_anomaly.assert_any_call(kind="completed_not_confirmed", value=0)
    assert result == {"paid_not_completed": 2, "completed_not_confirmed": 0}
