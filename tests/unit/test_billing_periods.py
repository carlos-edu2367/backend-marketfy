from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.billing_periods import CYCLE_MAP, PERIOD_DAYS


def test_period_days_covers_all_cycles():
    assert PERIOD_DAYS == {"monthly": 30, "semiannual": 180, "annual": 365}


def test_cycle_map_matches_billing_core_enum_values():
    assert CYCLE_MAP == {"monthly": "MONTHLY", "semiannual": "SEMIANNUALLY", "annual": "YEARLY"}
