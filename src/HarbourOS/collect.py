"""Collect AIS snapshots on a steady beat, then store them in one upload.

The state machine only joins two sightings of a ship when they are at most
MAX_GAP (30 minutes) apart. One snapshot per pipeline run -- with GitHub
starting "hourly" runs three to six hours apart -- left nearly every sighting
stranded, so visits could not be stitched together. A run now stays open for
hours and polls every few minutes.

The snapshots are held on the runner and written to Bronze together at the
end. One upload per run instead of one per poll keeps the warehouse's work
flat however often we poll, which is what keeps it inside MotherDuck's free
compute allowance.
"""

import argparse
import time
from collections.abc import Callable
from datetime import datetime

from HarbourOS.ingestion import fetch_ais_data
from HarbourOS.storage import insert_ais_snapshots

Snapshot = tuple[datetime, list[dict]]


def collect(
    minutes: float,
    every_minutes: float,
    fetch: Callable[[], list[dict]] = fetch_ais_data,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[Snapshot]:
    """Poll the live API every `every_minutes` for `minutes`, keeping new messages.

    The live endpoint returns each ship's latest message, so a ship that has
    not transmitted since the last poll comes back unchanged. Those repeats
    are dropped here: they carry no new information, and storing them would
    only fill Quarantine with 'duplicate' rows.
    """
    deadline = clock() + minutes * 60
    seen: set[tuple] = set()
    snapshots: list[Snapshot] = []

    while True:
        started = clock()
        try:
            positions = fetch()
        except Exception as error:  # one bad poll must not lose the ones already held
            print(f"Poll failed, skipping it: {error}")
            positions = []

        fresh = []
        for position in positions:
            key = (position.get("mmsi"), position.get("msgtime"))
            if key not in seen:
                seen.add(key)
                fresh.append(position)
        if fresh:
            snapshots.append((datetime.now(), fresh))
        print(f"Poll {len(snapshots)}: {len(positions)} positions, {len(fresh)} new")

        next_poll = started + every_minutes * 60
        if next_poll >= deadline:
            return snapshots
        sleep(max(0.0, next_poll - clock()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, required=True, help="how long to collect")
    parser.add_argument("--every", type=float, default=10, help="minutes between polls")
    args = parser.parse_args()

    snapshots = collect(args.minutes, args.every)
    stored = insert_ais_snapshots(snapshots)
    print(f"✅ Stored {stored} messages from {len(snapshots)} polls in the Bronze layer")


if __name__ == "__main__":
    main()
