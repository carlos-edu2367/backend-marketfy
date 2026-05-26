from __future__ import annotations

import sys
from pathlib import Path

app_dir = Path(__file__).resolve().parent
if str(app_dir) not in sys.path:
    sys.path.insert(0, str(app_dir))
