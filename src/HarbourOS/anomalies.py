"""Find unusual ship behaviour in the last week of data.

    uv run python -m HarbourOS.anomalies                              # the pipeline
    uv run python -m HarbourOS.anomalies --no-save --json out.json    # a preview

Four kinds of thing are flagged, each with a plain-language reason:

1. long_stay / short_stay: a port call far longer or shorter than usual. Each
   stay is compared with the stays of the same kind of ship (cargo, tanker,
   passenger, ...) at the same port, or at all ports when that port has too
   few. Stays vary by orders of magnitude (a ferry calls for 20 minutes, a
   tanker for a day), so they are compared on a log scale, using the median
   and the median absolute deviation rather than the mean and standard
   deviation: a handful of very long stays would otherwise drag the "usual"
   up and hide exactly what we look for. A stay still going on counts too,
   for "longer than usual" only, since it can only get longer. A stay the
   ship itself often makes (three times or more at that port, within a factor
   of two) is its routine, not news: a ferry that always turns round in 50
   minutes where others stay overnight is left alone.

2. odd_stop: a stop in open sea (visit_type 'at_sea': no port, anchorage or
   fish farm nearby) where no other ship has stopped this month. Ships do stop
   at sea for good reasons, at oil fields or in waiting areas, but those
   places are shared by many ships. Only stops 20 km or more from a port
   count, and fishing boats, tugs and pilot, rescue and service boats are left
   out, because stopping at sea is part of their job.

3. unusual_day: a ship's day that doesn't look like the days of other ships
   of its kind. Each ship-day is described by a few numbers (how far it
   sailed, its top speed, how much of the day it was moving, its longest
   silence and how many positions it sent) and an Isolation Forest, a
   standard machine-learning method for spotting outliers, scores how easy
   that day is to tell apart from the rest. It is trained per kind of ship,
   every run, on the week being checked, so it needs no labelled examples and
   adapts as traffic changes. The reason names the number that is rarest
   for that kind of ship (the furthest into the top or bottom of its range).

4. impossible_jump: two positions from one ship, minutes apart, that would
   need a speed no ship can do (over 60 knots). That is a faulty GPS, two
   ships sharing an identity, or a false position.

Only ships are checked: MMSI numbers from 200000000 to 799999999. The others
belong to search-and-rescue aircraft, buoys, beacons and the like.

Each ship gets at most one flag of each kind, its most unusual one, so one
ship can't fill the list.

Each run rebuilds the table vessel_anomalies from scratch (it holds a week and
is small, a few hundred rows), after dbt build. Nothing here feeds back into
port calls or the scores. With --no-save nothing is written to the warehouse;
the dashboard preview uses that.
"""

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from sklearn.ensemble import IsolationForest  # type: ignore[import-untyped]
from sklearn.neighbors import BallTree  # type: ignore[import-untyped]

from HarbourOS.storage import DB_PATH, connect

# Raise when the rules below change, so old and new flags can be told apart.
MODEL_VERSION = 1

WEEK_DAYS = 7
# A port-and-ship-type group needs this many finished stays to have its own
# "usual"; smaller ones fall back to that ship type at every port.
MIN_GROUP = 10
# How unusual a stay must be, in robust standard deviations on a log scale.
# 3.5 is the usual cut-off for the modified z-score (Iglewicz and Hoaglin).
STAY_Z = 3.5
# ... and how long, so a 40-minute stay where 10 is normal isn't news.
LONG_STAY_MIN_HOURS = 24
# A short stay must still be a real stop, not a pause.
SHORT_STAY_MIN_MINUTES = 15
# "No other ship stopped here this month": within this distance.
ODD_STOP_KM = 3.0
ODD_STOP_MIN_MINUTES = 60
ODD_STOP_MIN_PORT_KM = 20.0
# Ships whose work includes stopping at sea.
STOPS_AT_SEA_FOR_WORK = ("fishing", "towing / tug", "pilot, rescue, service")
# "The ship's own routine": this many similar stays at the port, where
# similar means within a factor of two.
HABIT_STAYS = 3
EARTH_KM = 6371.0
# Ship-days with fewer positions than this (two hours) are too thin to judge.
MIN_DAY_SLOTS = 12
# An Isolation Forest needs enough days to learn what is normal.
MIN_DAYS_PER_TYPE = 200
# Share of each ship type's days to flag, most unusual first.
DAY_SHARE = 0.003
JUMP_KNOTS = 60.0
JUMP_MIN_KM = 2.0
# AIS speed 102.3 means "not available" (102.2 is "102.2 or more"): not a speed.
MAX_REAL_SOG = 102.0

# Ship stations only, not aircraft (111...), coast stations or beacons (97...).
SHIPS_ONLY = "mmsi BETWEEN 200000000 AND 799999999"

KINDS = ("long_stay", "short_stay", "odd_stop", "unusual_day", "impossible_jump")
COLUMNS = [
    "mmsi",
    "kind",
    "started_at",
    "ended_at",
    "latitude",
    "longitude",
    "port_locode",
    "score",
    "reason",
    "model_version",
    "detected_at",
]

STAYS = f"""
SELECT f.mmsi, f.port_locode, p.port_name, v.ship_category, f.completeness,
       f.minutes_alongside, f.berth_start, f.berth_end,
       f.stop_latitude AS latitude, f.stop_longitude AS longitude
FROM fact_port_call f
JOIN dim_vessel v USING (mmsi)
LEFT JOIN dim_port p USING (port_locode)
WHERE f.visit_type = 'port_call'
  AND f.completeness IN ('complete', 'departure_unobserved')
  AND f.minutes_alongside > 0
  AND f.{SHIPS_ONLY}
"""

AT_SEA = f"""
SELECT f.mmsi, v.ship_category, f.minutes_alongside, f.berth_start, f.berth_end,
       f.stop_latitude AS latitude, f.stop_longitude AS longitude, f.nearest_port_km
FROM fact_port_call f
JOIN dim_vessel v USING (mmsi)
WHERE f.visit_type = 'at_sea' AND f.stop_latitude IS NOT NULL AND f.{SHIPS_ONLY}
"""

# One row per ship per day, from the 10-minute track. Only whole days: the
# first and last day of the week are cut off part-way.
SHIP_DAYS = f"""
WITH t AS (
    SELECT mmsi, message_time, latitude, longitude,
           CASE WHEN speed_over_ground < {MAX_REAL_SOG} THEN speed_over_ground END AS sog,
           date_trunc('day', message_time) AS day,
           lag(message_time) OVER w AS prev_time,
           lag(latitude) OVER w AS prev_lat,
           lag(longitude) OVER w AS prev_lon
    FROM fct_vessel_track
    WHERE {SHIPS_ONLY}
    WINDOW w AS (PARTITION BY mmsi ORDER BY message_time)
),
steps AS (
    SELECT *,
        {EARTH_KM} * 2 * asin(sqrt(
            pow(sin(radians(latitude - prev_lat) / 2), 2)
            + cos(radians(prev_lat)) * cos(radians(latitude))
            * pow(sin(radians(longitude - prev_lon) / 2), 2)
        )) AS step_km,
        epoch(message_time - prev_time) / 60 AS gap_min
    FROM t
),
bounds AS (SELECT min(day) AS first_day, max(day) AS last_day FROM t)
SELECT mmsi, day,
       count(*) AS slots,
       coalesce(sum(step_km) FILTER (WHERE gap_min <= 60), 0) AS distance_km,
       coalesce(max(sog), 0) AS top_speed,
       coalesce(avg(CASE WHEN sog > 2 THEN 1 ELSE 0 END), 0) AS moving_share,
       coalesce(max(gap_min), 0) AS longest_gap_min,
       arg_max(latitude, message_time) AS latitude,
       arg_max(longitude, message_time) AS longitude
FROM steps, bounds
WHERE day > bounds.first_day AND day < bounds.last_day
GROUP BY mmsi, day
"""

JUMPS = f"""
WITH t AS (
    SELECT mmsi, message_time, latitude, longitude,
           lag(message_time) OVER w AS prev_time,
           lag(latitude) OVER w AS prev_lat,
           lag(longitude) OVER w AS prev_lon
    FROM fct_vessel_track
    WHERE {SHIPS_ONLY}
    WINDOW w AS (PARTITION BY mmsi ORDER BY message_time)
),
steps AS (
    SELECT mmsi, prev_time, message_time, latitude, longitude,
        {EARTH_KM} * 2 * asin(sqrt(
            pow(sin(radians(latitude - prev_lat) / 2), 2)
            + cos(radians(prev_lat)) * cos(radians(latitude))
            * pow(sin(radians(longitude - prev_lon) / 2), 2)
        )) AS km,
        epoch(message_time - prev_time) / 60 AS minutes
    FROM t
    WHERE prev_time IS NOT NULL
)
SELECT mmsi, prev_time, message_time, latitude, longitude, km, minutes,
       km / 1.852 / (minutes / 60) AS knots
FROM steps
WHERE minutes BETWEEN 5 AND 60
  AND km >= {JUMP_MIN_KM}
  AND km / 1.852 / (minutes / 60) > {JUMP_KNOTS}
QUALIFY row_number() OVER (
    PARTITION BY mmsi, date_trunc('day', message_time) ORDER BY knots DESC
) = 1
"""

CATEGORIES = """SELECT mmsi, ship_category FROM dim_vessel"""

LATEST = """SELECT max(message_time) FROM fct_vessel_track"""


def hours_text(minutes: float) -> str:
    """'25 minutes', '7 hours', '3.5 days'."""
    if minutes < 90:
        return f"{round(minutes)} minutes"
    hours = minutes / 60
    if hours < 48:
        return f"{round(hours)} hours"
    return f"{hours / 24:.1f} days"


def plural(category: str) -> str:
    """How a ship category reads in a sentence: 'cargo ships', 'tugs'."""
    return {
        "fishing": "fishing boats",
        "towing / tug": "tugs",
        "pilot, rescue, service": "pilot, rescue and service boats",
        "passenger": "passenger ships",
        "cargo": "cargo ships",
        "tanker": "tankers",
        "leisure": "leisure boats",
    }.get(category, "ships of the same type")


def robust_spread(values: pd.Series) -> tuple[float, float]:
    """Median and robust standard deviation (1.4826 x median absolute deviation)."""
    median = float(values.median())
    mad = float((values - median).abs().median()) * 1.4826
    return median, mad


def stay_anomalies(stays: pd.DataFrame, since: datetime) -> list[dict[str, Any]]:
    """Port calls far longer or shorter than usual for that ship type and port."""
    if stays.empty:
        return []
    stays = stays.copy()
    stays["log_minutes"] = np.log(stays["minutes_alongside"].astype(float))
    finished = stays[stays["completeness"] == "complete"]

    by_port = {
        key: (len(group), *robust_spread(group["log_minutes"]))
        for key, group in finished.groupby(["port_locode", "ship_category"])
    }
    by_type = {
        key: (len(group), *robust_spread(group["log_minutes"]))
        for key, group in finished.groupby("ship_category")
    }

    own = {
        key: group["log_minutes"].to_numpy()
        for key, group in stays.groupby(["mmsi", "port_locode"])
    }

    found = []
    recent = stays[stays["berth_end"] >= since]
    for row in recent.itertuples(index=False):
        habit = own[(row.mmsi, row.port_locode)]
        if (np.abs(habit - row.log_minutes) < math.log(2)).sum() - 1 >= HABIT_STAYS:
            continue
        port_stats = by_port.get((row.port_locode, row.ship_category))
        at_port = port_stats is not None and port_stats[0] >= MIN_GROUP
        stats = port_stats if at_port else by_type.get(row.ship_category)
        if stats is None or stats[0] < MIN_GROUP or stats[2] <= 0:
            continue
        _, median, spread = stats
        z = (row.log_minutes - median) / spread
        usual = hours_text(math.exp(median))
        port = row.port_name or row.port_locode
        where = f"at {port}" if at_port else "at any port"
        still = row.completeness == "departure_unobserved"
        if z >= STAY_Z and row.minutes_alongside >= LONG_STAY_MIN_HOURS * 60:
            verb = "Has been" if still else "Stayed"
            kind = "long_stay"
            reason = (
                f"{verb} {hours_text(row.minutes_alongside)} in {port}; "
                f"{plural(row.ship_category)} {where} usually stay {usual}."
            )
        elif (
            z <= -STAY_Z
            and at_port
            and not still
            and row.minutes_alongside >= SHORT_STAY_MIN_MINUTES
        ):
            kind = "short_stay"
            reason = (
                f"Stayed only {hours_text(row.minutes_alongside)} in {port}; "
                f"{plural(row.ship_category)} there usually stay {usual}."
            )
        else:
            continue
        found.append(
            {
                "mmsi": row.mmsi,
                "kind": kind,
                "started_at": row.berth_start,
                "ended_at": None if still else row.berth_end,
                "latitude": row.latitude,
                "longitude": row.longitude,
                "port_locode": row.port_locode,
                "score": round(abs(z), 2),
                "reason": reason,
            }
        )
    return found


def odd_stops(at_sea: pd.DataFrame, since: datetime) -> list[dict[str, Any]]:
    """Stops in open sea where no other ship has stopped this month."""
    if at_sea.empty:
        return []
    points = np.radians(at_sea[["latitude", "longitude"]].to_numpy(dtype=float))
    tree = BallTree(points, metric="haversine")
    neighbours = tree.query_radius(points, r=ODD_STOP_KM / EARTH_KM)
    mmsis = at_sea["mmsi"].to_numpy()

    found = []
    for i, row in enumerate(at_sea.itertuples(index=False)):
        if (
            row.berth_end < since
            or row.ship_category in STOPS_AT_SEA_FOR_WORK
            or row.minutes_alongside < ODD_STOP_MIN_MINUTES
            or row.nearest_port_km < ODD_STOP_MIN_PORT_KM
        ):
            continue
        others = {int(mmsis[j]) for j in neighbours[i]} - {int(row.mmsi)}
        if others:
            continue
        found.append(
            {
                "mmsi": row.mmsi,
                "kind": "odd_stop",
                "started_at": row.berth_start,
                "ended_at": row.berth_end,
                "latitude": row.latitude,
                "longitude": row.longitude,
                "port_locode": None,
                "score": round(float(row.nearest_port_km), 1),
                "reason": (
                    f"Stopped {hours_text(row.minutes_alongside)} in open sea, "
                    f"{round(row.nearest_port_km)} km from the nearest port, "
                    f"where no other ship stopped this month."
                ),
            }
        )
    return found


# The numbers each ship-day is described by, and how to say each in a
# sentence: what this ship did, and what its kind of ship usually does.
DAY_FEATURES = {
    "distance_km": ("Sailed {mine:.0f} km in a day", "usually sail {usual:.0f} km"),
    "top_speed": ("Reached {mine:.0f} knots", "usually top out at {usual:.0f} knots"),
    "moving_share": ("Was moving {mine:.0%} of the day", "usually move {usual:.0%} of it"),
    "longest_gap_min": ("Went silent for {mine}", "usually go {usual} at most between positions"),
    "slots": (
        "Sent positions in {mine:.0f} of 144 ten-minute slots",
        "usually send them in {usual:.0f}",
    ),
}


def _day_values(days: pd.DataFrame) -> np.ndarray:
    """The numbers the forest sees. Long-tailed ones on a log scale."""
    return np.column_stack(
        [
            np.log1p(days["distance_km"].astype(float)),
            days["top_speed"].astype(float),
            days["moving_share"].astype(float),
            np.log1p(days["longest_gap_min"].astype(float)),
            days["slots"].astype(float),
        ]
    )


def _day_reason(day: pd.Series, group: pd.DataFrame, category: str) -> str:
    """Name the number that is rarest for this kind of ship.

    Rarest means furthest into the top or bottom of the group's range: the
    share of the group's days with a smaller value (ties count half), or with
    a larger one, whichever is smaller.
    """
    rarity = {}
    for column in DAY_FEATURES:
        values = group[column].astype(float)
        mine = float(day[column])
        below = ((values < mine).sum() + 0.5 * (values == mine).sum()) / len(values)
        # Ties (both the highest of the week, say) go to the one further from
        # the middle, in robust standard deviations.
        median, spread = robust_spread(values)
        rarity[column] = (min(below, 1 - below), -abs(mine - median) / (spread or 1e-9))
    column = min(rarity, key=lambda name: rarity[name])
    mine_raw, usual_raw = float(day[column]), float(group[column].median())
    if column == "longest_gap_min":
        mine_text, usual_text = hours_text(mine_raw), hours_text(usual_raw)
    else:
        mine_text, usual_text = mine_raw, usual_raw  # type: ignore[assignment]
    ship_phrase, usual_phrase = DAY_FEATURES[column]
    return (
        f"{ship_phrase.format(mine=mine_text)}; "
        f"{plural(category)} {usual_phrase.format(usual=usual_text)}."
    )


def unusual_days(days: pd.DataFrame, categories: dict[int, str]) -> list[dict[str, Any]]:
    """Ship-days an Isolation Forest finds easiest to tell apart from the rest."""
    if days.empty:
        return []
    days = days[days["slots"] >= MIN_DAY_SLOTS].copy()
    days["ship_category"] = days["mmsi"].map(categories).fillna("unknown")
    found = []
    for category, group in days.groupby("ship_category"):
        if len(group) < MIN_DAYS_PER_TYPE or category == "unknown":
            continue
        forest = IsolationForest(n_estimators=200, random_state=0)
        forest.fit(_day_values(group))
        # score_samples: lower is more unusual. Flip it so higher is odder.
        oddness = -forest.score_samples(_day_values(group))
        count = max(1, round(len(group) * DAY_SHARE))
        for i in np.argsort(-oddness)[:count]:
            day = group.iloc[i]
            found.append(
                {
                    "mmsi": day["mmsi"],
                    "kind": "unusual_day",
                    "started_at": day["day"],
                    "ended_at": day["day"] + pd.Timedelta(days=1),
                    "latitude": day["latitude"],
                    "longitude": day["longitude"],
                    "port_locode": None,
                    "score": round(float(oddness[i]), 3),
                    "reason": _day_reason(day, group, str(category)),
                }
            )
    return found


def impossible_jumps(jumps: pd.DataFrame) -> list[dict[str, Any]]:
    """Positions minutes apart that no ship could travel between."""
    if jumps.empty:
        return []
    days_with_jumps = jumps.groupby("mmsi").size()
    jumps = jumps.sort_values("knots", ascending=False).drop_duplicates("mmsi")
    return [
        {
            "mmsi": row.mmsi,
            "kind": "impossible_jump",
            "started_at": row.prev_time,
            "ended_at": row.message_time,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "port_locode": None,
            "score": round(float(row.knots)),
            "reason": (
                f"Jumped {row.km:.0f} km in {round(row.minutes)} minutes "
                f"(about {row.knots:.0f} knots): a faulty or false position"
                + (
                    f", on {days_with_jumps[row.mmsi]} days this week."
                    if days_with_jumps[row.mmsi] > 1
                    else "."
                )
            ),
        }
        for row in jumps.itertuples(index=False)
    ]


def find_anomalies(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Every flag for the last week of data, one row each."""
    latest = conn.execute(LATEST).fetchone()
    if latest is None or latest[0] is None:
        return pd.DataFrame(columns=COLUMNS)
    since = latest[0] - pd.Timedelta(days=WEEK_DAYS)
    categories = dict(conn.execute(CATEGORIES).fetchall())

    found = (
        stay_anomalies(conn.execute(STAYS).df(), since)
        + odd_stops(conn.execute(AT_SEA).df(), since)
        + unusual_days(conn.execute(SHIP_DAYS).df(), categories)
        + impossible_jumps(conn.execute(JUMPS).df())
    )
    frame = pd.DataFrame(found, columns=COLUMNS)
    frame = frame.sort_values("score", ascending=False).drop_duplicates(["mmsi", "kind"])
    frame["model_version"] = MODEL_VERSION
    frame["detected_at"] = datetime.now(UTC).replace(tzinfo=None)
    return frame


def save(
    conn: duckdb.DuckDBPyConnection, frame: pd.DataFrame, table: str, temporary: bool = False
) -> None:
    """Replace the table with this run's flags (a temporary one vanishes on disconnect)."""
    conn.register("anomaly_frame", frame)
    conn.execute(
        f"""
        CREATE OR REPLACE {"TEMP " if temporary else ""}TABLE {table} AS
        SELECT CAST(mmsi AS INTEGER) AS mmsi, CAST(kind AS VARCHAR) AS kind,
               CAST(started_at AS TIMESTAMP) AS started_at,
               CAST(ended_at AS TIMESTAMP) AS ended_at,
               CAST(latitude AS DOUBLE) AS latitude, CAST(longitude AS DOUBLE) AS longitude,
               CAST(port_locode AS VARCHAR) AS port_locode, CAST(score AS DOUBLE) AS score,
               CAST(reason AS VARCHAR) AS reason, CAST(model_version AS INTEGER) AS model_version,
               CAST(detected_at AS TIMESTAMP) AS detected_at
        FROM anomaly_frame
        """
    )
    conn.unregister("anomaly_frame")


def _iso(value: Any) -> str | None:
    return None if value is None or pd.isna(value) else pd.Timestamp(value).isoformat()


def dashboard_summary(
    conn: duckdb.DuckDBPyConnection, table: str = "vessel_anomalies", limit: int = 60
) -> dict[str, Any]:
    """What the website shows: counts per kind and the most unusual flags."""
    exists = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [table]
    ).fetchone()
    if not exists or not exists[0]:
        return {"counts": {}, "flags": []}
    counts = dict(conn.execute(f"SELECT kind, count(*) FROM {table} GROUP BY kind").fetchall())
    # Within each kind, most unusual first; then interleave the kinds so one
    # kind can't fill the whole list.
    rows = conn.execute(
        f"""
        SELECT a.mmsi, v.vessel_name, v.ship_category, a.kind, a.started_at, a.ended_at,
               a.latitude, a.longitude, a.reason
        FROM (
            SELECT *, row_number() OVER (PARTITION BY kind ORDER BY score DESC) AS place
            FROM {table}
        ) a
        LEFT JOIN dim_vessel v USING (mmsi)
        WHERE a.place <= ?
        ORDER BY a.place, a.kind
        """,
        [math.ceil(limit / len(KINDS))],
    ).fetchall()
    return {
        "counts": counts,
        "flags": [
            {
                "mmsi": mmsi,
                "ship": name or f"MMSI {mmsi}",
                "group": category,
                "kind": kind,
                "started_at": _iso(start),
                "ended_at": _iso(end),
                "lat": lat,
                "lon": lon,
                "reason": reason,
            }
            for mmsi, name, category, kind, start, end, lat, lon, reason in rows
        ][:limit],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-save", action="store_true", help="write nothing to the warehouse")
    parser.add_argument("--json", type=Path, help="also write the dashboard summary here")
    args = parser.parse_args()

    conn = connect(DB_PATH)
    try:
        frame = find_anomalies(conn)
        table = "vessel_anomalies_preview" if args.no_save else "vessel_anomalies"
        save(conn, frame, table, temporary=args.no_save)
        counts = frame["kind"].value_counts().to_dict()
        print(
            "Unusual this week: "
            + ", ".join(f"{kind} {counts.get(kind, 0)}" for kind in KINDS)
            + "."
        )
        for row in frame.sort_values("score", ascending=False).groupby("kind").head(3).itertuples():
            print(f"  {row.kind} {row.mmsi}: {row.reason}")
        if args.json:
            args.json.write_text(json.dumps(dashboard_summary(conn, table)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
