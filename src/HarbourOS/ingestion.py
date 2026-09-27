"""Ingestion: collect dense vessel tracks for the port areas we monitor.

Two steps per run. First ask BarentsWatch which vessels were inside each
monitored area during the lookback window -- that endpoint returns MMSI
numbers only, not positions. Then fetch the full recent track for each.

The area decides WHICH vessels are interesting, not WHAT data we keep. Once
a vessel is on the list we take its whole track, so a journey is never cut
off at the edge of the box.

This replaced a live snapshot of every vessel in Norway. Measured against
real data, that snapshot left 317 minutes between a vessel's readings -- ten
times MAX_GAP, so the state machine could never join two readings into a
journey, and live polling produced zero port calls in two weeks. Track
readings arrive about 1.2 minutes apart, which is what it was built for.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from HarbourOS.storage import initialize_bronze_table, insert_ais_messages

load_dotenv()

TOKEN_URL = "https://id.barentswatch.no/connect/token"
AREA_URL = "https://historic.ais.barentswatch.no/v1/historic/mmsiinarea"
TRACK_URL = "https://historic.ais.barentswatch.no/v1/historic/trackslast24hours"

AREAS_FILE = Path(__file__).resolve().parents[2] / "config" / "monitored_areas.json"

REQUEST_TIMEOUT_SECONDS = 120


def get_access_token() -> str:
    """Get a fresh access token from BarentsWatch."""
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": os.getenv("BARENTSWATCH_CLIENT_ID"),
            "client_secret": os.getenv("BARENTSWATCH_CLIENT_SECRET"),
            "grant_type": "client_credentials",
            "scope": "ais",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get token: {response.text}")
    return response.json()["access_token"]


def load_areas(path: Path = AREAS_FILE) -> dict:
    """Read the monitored areas. Adding a port means editing that file only."""
    with path.open() as handle:
        return json.load(handle)


def fetch_mmsis_in_area(polygon: dict, hours: int, token: str) -> list[int]:
    """Which vessels were inside this area during the last `hours` hours."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    body = {
        "msgtimefrom": start.isoformat().replace("+00:00", "Z"),
        "msgtimeto": end.isoformat().replace("+00:00", "Z"),
        "polygon": polygon,
    }
    response = requests.post(
        AREA_URL,
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"Area lookup failed: {response.status_code} {response.text[:200]}")
        return []
    found = response.json()
    return found if isinstance(found, list) else []


def fetch_historic_track(mmsi: int, token: str | None = None) -> list[dict]:
    """Fetch the last 24 hours of positions for one vessel.

    The token is passed in because one run fetches dozens of tracks, and
    asking for a new token each time would be dozens of extra round trips
    for a credential that is still perfectly valid.
    """
    if token is None:
        token = get_access_token()
    response = requests.get(
        f"{TRACK_URL}/{mmsi}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"Track failed for {mmsi}: {response.status_code}")
        return []
    data = response.json()
    return data if isinstance(data, list) else []


def collect_tracks(config: dict | None = None) -> list[dict]:
    """Every position reading for every vessel seen in any monitored area."""
    config = config or load_areas()
    hours = int(config.get("lookback_hours", 24))
    token = get_access_token()

    mmsis: set[int] = set()
    for area in config["areas"]:
        found = fetch_mmsis_in_area(area["polygon"], hours, token)
        print(f"{area['name']}: {len(found)} vessels in the last {hours}h")
        mmsis.update(found)

    print(f"Distinct vessels to fetch: {len(mmsis)}")

    readings: list[dict] = []
    failed = 0
    for index, mmsi in enumerate(sorted(mmsis), start=1):
        track = fetch_historic_track(mmsi, token=token)
        if not track:
            failed += 1
        readings.extend(track)
        if index % 25 == 0:
            print(f"  {index}/{len(mmsis)} tracks, {len(readings)} readings so far")

    print(f"Tracks fetched: {len(mmsis) - failed} ok, {failed} empty or failed")
    print(f"Total readings: {len(readings)}")
    return readings


def ingest_batch() -> int:
    """Fetch tracks for the monitored areas and store them in Bronze.

    The table is created if it is missing so a fresh database -- a local test
    file, or a new warehouse -- works without a separate setup step.
    """
    readings = collect_tracks()
    initialize_bronze_table()
    stored = insert_ais_messages(readings)
    print(f"Stored {stored} messages in Bronze layer")
    return stored


if __name__ == "__main__":
    ingest_batch()
