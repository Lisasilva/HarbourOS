"""Observable data loader: the latest independent reliability check.

Served as data/reliability.json. Each pipeline run checks 100 random recent
port calls against OpenStreetMap's map of quays (src/HarbourOS/reliability.py)
and this reads the results. The dashboard preview runs that check without
saving and points RELIABILITY_JSON at its output, so a preview shows a fresh
result without writing to the warehouse.
"""

import json
import os
import sys
from pathlib import Path

from HarbourOS.reliability import dashboard_summary
from HarbourOS.storage import connect

override = os.environ.get("RELIABILITY_JSON")
if override and Path(override).is_file():
    sys.stdout.write(Path(override).read_text())
else:
    with connect() as conn:
        json.dump(dashboard_summary(conn), sys.stdout)
