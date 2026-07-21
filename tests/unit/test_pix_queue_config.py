# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.queues.arq_config import ALL_QUEUES, QUEUE_PIX_HIGH, QUEUE_PIX_RECONCILE


def test_pix_queue_registered():
    assert QUEUE_PIX_HIGH == "pix:high"
    assert QUEUE_PIX_HIGH in ALL_QUEUES


def test_pix_reconcile_remapped_to_pix_high():
    assert QUEUE_PIX_RECONCILE == "pix:high"
