"""Observable data loader: when the dashboard's data was last refreshed.

Served as data/freshness.json. latest_reading is the newest AIS position in
Silver, which is what "last updated" means to a visitor: if the pipeline
stops, this stops moving even though nothing else on the page says so.
built_at is when this build ran, which tells a stalled pipeline (old
latest_reading, old built_at) apart from a stalled data feed (old
latest_reading, recent built_at).
"""

import json
import sys
from datetime import UTC, datetime

from HarbourOS.storage import connect

QUERY = "select max(message_time) as latest_reading from ais_messages_silver"

with connect() as conn:
    row = conn.execute(QUERY).fetchone()

latest = row[0] if row else None
json.dump(
    {
        "latest_reading": latest.replace(tzinfo=UTC).isoformat() if latest else None,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
    },
    sys.stdout,
)
