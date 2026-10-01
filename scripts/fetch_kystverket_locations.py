"""Download Kystverket's official list of Norwegian quays, harbours and
anchorages and write it as a dbt seed.

Kystverket (the Norwegian Coastal Administration) keeps the location register
that ships use when they report port calls to the authorities (SafeSeaNet).
Its open API publishes it with no login. About 4,100 places, each one point:
quays, ferry quays, port facilities, harbours, anchorages, pilot boarding
points and named spots at sea.

The list is small and changes rarely, so it is version-controlled as a seed:
the pipeline never depends on Kystverket's server being up. Run this script by
hand to refresh it, then start the Pipeline with "full_refresh" ticked so older
stops are labelled again.

    uv run python scripts/fetch_kystverket_locations.py
"""

import csv
from pathlib import Path
from typing import Any

import requests

SOURCE_URL = "https://kystdatahuset.no/ws/api/location/norway/all/geojson"
SEED_PATH = Path("dbt/seeds/kystverket_locations.csv")
# Kystverket's location types, as kept in the seed.
KINDS = {
    "QUAY": "quay",
    "FERRY_QUAY": "ferry_quay",
    "PORT_FACILITY": "port_facility",
    "HARBOUR": "harbour",
    "ANCHORAGE": "anchorage",
    "LOCATION_AT_SEA": "location_at_sea",
    "PILOT_BOARDING": "pilot_boarding",
}


def parse_locations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per place with a real position (some sit at 0, 0)."""
    rows = []
    for feature in payload.get("features", []):
        props = feature.get("properties", {})
        kind = KINDS.get(props.get("systemname", ""))
        lon, lat = feature.get("geometry", {}).get("coordinates", (0, 0))
        if kind is None or (lat == 0 and lon == 0):
            continue
        rows.append(
            {
                "location_id": props["locationid"],
                "locode": props.get("locationcode") or "",
                "location_name": (props.get("locationnamenor") or "").strip(),
                "kind": kind,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
            }
        )
    return sorted(rows, key=lambda row: row["location_id"])


def main() -> None:
    response = requests.get(SOURCE_URL, timeout=120)
    response.raise_for_status()
    rows = parse_locations(response.json())
    with SEED_PATH.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} locations to {SEED_PATH}")


if __name__ == "__main__":
    main()
