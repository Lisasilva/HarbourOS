"""Throwaway probe: is the area + track combination dense enough to use?

Writes nothing to the database. Step 1 asks which ships were near Oslo
harbour recently. Step 2 pulls the full track for a few of them and measures
how far apart consecutive readings are -- the number that decides whether
MAX_GAP = 30 minutes survives.
"""

import statistics
from datetime import datetime, timedelta, timezone

import requests

from HarbourOS.ingestion import fetch_historic_track, get_access_token

AREA_URL = "https://historic.ais.barentswatch.no/v1/historic/mmsiinarea"

# A small box around Oslo harbour.
OSLO_BOX = {
    "type": "Polygon",
    "coordinates": [
        [
            [10.55, 59.80],
            [10.80, 59.80],
            [10.80, 59.95],
            [10.55, 59.95],
            [10.55, 59.80],
        ]
    ],
}

HOURS_BACK = 1
SHIPS_TO_SAMPLE = 3


def parse_time(value: str) -> datetime:
    """Turn the API's timestamp text into something we can subtract."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ships_in_area() -> list[int]:
    """Which ships were inside the box during the last hour."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=HOURS_BACK)

    body = {
        "msgtimefrom": start.isoformat().replace("+00:00", "Z"),
        "msgtimeto": end.isoformat().replace("+00:00", "Z"),
        "polygon": OSLO_BOX,
    }
    headers = {"Authorization": f"Bearer {get_access_token()}"}

    response = requests.post(AREA_URL, json=body, headers=headers, timeout=120)
    response.raise_for_status()
    return response.json()


def describe_track(mmsi: int) -> int:
    """Fetch one ship's 24-hour track and report how dense it is."""
    track = fetch_historic_track(mmsi)
    print(f"\n--- ship {mmsi} ---")
    print(f"readings: {len(track)}")

    if not track:
        return 0

    print(f"fields: {sorted(track[0].keys())}")

    moments = sorted(parse_time(row["msgtime"]) for row in track)
    span_hours = (moments[-1] - moments[0]).total_seconds() / 3600
    print(f"covers: {span_hours:.1f} hours")

    gaps = [(later - earlier).total_seconds() / 60 for earlier, later in zip(moments, moments[1:])]
    if gaps:
        print(
            f"gap minutes -- typical {statistics.median(gaps):.1f}, "
            f"smallest {min(gaps):.1f}, largest {max(gaps):.1f}"
        )
    return len(track)


def main() -> None:
    mmsis = ships_in_area()
    print(f"Ships near Oslo harbour in the last hour: {len(mmsis)}")
    print(f"First few: {mmsis[:10]}")

    sampled = mmsis[:SHIPS_TO_SAMPLE]
    total = 0
    for mmsi in sampled:
        total += describe_track(mmsi)

    if sampled:
        per_ship = total / len(sampled)
        print(f"\nAverage readings per ship over 24 hours: {per_ship:.0f}")
        print(f"All {len(mmsis)} ships would be roughly {per_ship * len(mmsis):,.0f} readings")
        print("\nMAX_GAP is currently 30 minutes.")


if __name__ == "__main__":
    main()
