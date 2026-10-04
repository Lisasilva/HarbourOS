"""Observable data loader: unusual ship behaviour this week.

Served as data/anomalies.json. Each pipeline run looks for unusual stays,
stops in open sea, unusual days and impossible position jumps
(src/HarbourOS/anomalies.py) and this reads the results. The dashboard
preview runs that check without saving and points ANOMALIES_JSON at its
output, so a preview shows fresh results without writing to the warehouse.
"""

import json
import os
import sys
from pathlib import Path

from HarbourOS.anomalies import dashboard_summary
from HarbourOS.storage import connect

override = os.environ.get("ANOMALIES_JSON")
if override and Path(override).is_file():
    sys.stdout.write(Path(override).read_text())
else:
    with connect() as conn:
        json.dump(dashboard_summary(conn), sys.stdout)
