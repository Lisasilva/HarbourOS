"""Download UN/LOCODE and write a dbt seed of Norwegian seaports.

UN/LOCODE is the UN's official code list for trade and transport locations.
The worldwide list is ~120k entries; we keep only Norwegian entries flagged as
seaports that carry coordinates -- a few hundred rows, small enough to
version-control as a dbt seed so the project builds from a fresh clone.

Function is an 8-position flag string like "1-3-----": position 1 marks a
seaport, 2 rail, 3 road, 4 airport. Coordinates are left raw ("6326N 01023E")
and parsed in the staging model, so that conversion is SQL and can be tested.
"""
import csv
import io
from pathlib import Path

import requests

SOURCE_URL = "https://raw.githubusercontent.com/datasets/un-locode/main/data/code-list.csv"
SEED_PATH = Path("dbt/seeds/un_locode_ports.csv")
COUNTRY = "NO"


def is_seaport(function_code: str) -> bool:
    """Position 1 of the function flags marks a seaport."""
    return bool(function_code) and function_code[0] == "1"


def fetch_ports() -> list[dict]:
    response = requests.get(SOURCE_URL, timeout=120)
    response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))
    return [
        {
            "locode": f"{row['Country']}{row['Location']}",
            "country": row["Country"],
            "location": row["Location"],
            "port_name": row["NameWoDiacritics"] or row["Name"],
            "subdivision": row["Subdivision"],
            "status": row["Status"],
            "function": row["Function"],
            "coordinates": row["Coordinates"],
        }
        for row in reader
        if row["Country"] == COUNTRY and is_seaport(row["Function"]) and row["Coordinates"]
    ]


def main() -> None:
    ports = fetch_ports()
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)

    with SEED_PATH.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ports[0].keys()))
        writer.writeheader()
        writer.writerows(ports)

    print(f"Wrote {len(ports)} Norwegian seaports to {SEED_PATH}")


if __name__ == "__main__":
    main()
