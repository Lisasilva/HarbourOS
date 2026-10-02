"""Destination, ETA, IMO number and call sign: stored, carried to Silver, and
added to warehouses whose tables were created before these fields existed."""

from datetime import datetime
from pathlib import Path

import duckdb

from HarbourOS import ingestion
from HarbourOS.collect import voyage_field_coverage
from HarbourOS.storage import insert_ais_snapshots
from HarbourOS.transform import run_silver_transform

OLD_BRONZE = """
CREATE TABLE ais_messages_bronze (
    mmsi INTEGER, name VARCHAR, latitude DECIMAL(10, 6), longitude DECIMAL(10, 6),
    speedOverGround DECIMAL(10, 2), courseOverGround DECIMAL(10, 2),
    trueHeading DECIMAL(10, 2), rateOfTurn DECIMAL(10, 2), shipType INTEGER,
    navigationalStatus INTEGER, stream VARCHAR, msgtime TIMESTAMP,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def message(mmsi: int, msgtime: str, **extra) -> dict:
    return {
        "mmsi": mmsi,
        "latitude": 60.0,
        "longitude": 5.0,
        "speedOverGround": 0.1,
        "msgtime": msgtime,
        **extra,
    }


def rows(db_path: Path, sql: str) -> list:
    conn = duckdb.connect(str(db_path))
    try:
        return conn.sql(sql).fetchall()
    finally:
        conn.close()


def old_warehouse(tmp_path: Path) -> Path:
    """A warehouse whose layers were all built before the voyage fields."""
    db_path = tmp_path / "old.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(OLD_BRONZE)
    conn.close()
    insert_ais_snapshots(
        [(datetime(2026, 9, 30, 8, 0), [message(257000001, "2026-09-30 07:59:00")])],
        db_path=db_path,
    )
    run_silver_transform(db_path=db_path)
    return db_path


def test_an_existing_warehouse_gains_the_fields_and_keeps_its_rows(tmp_path: Path):
    db_path = old_warehouse(tmp_path)

    insert_ais_snapshots(
        [
            (
                datetime(2026, 10, 1, 8, 0),
                [
                    message(
                        257000002,
                        "2026-10-01 07:59:00",
                        destination="BERGEN",
                        eta="2026-10-01T18:00:00",
                        imoNumber=9123456,
                        callSign="LABC",
                    ),
                    message(
                        257000002,
                        "2026-10-01 07:59:00",
                        destination="BERGEN",
                    ),
                ],
            )
        ],
        db_path=db_path,
    )
    run_silver_transform(db_path=db_path)

    silver = rows(
        db_path,
        "SELECT mmsi, destination, eta, imo_number, call_sign "
        "FROM ais_messages_silver ORDER BY mmsi",
    )
    assert silver == [
        (257000001, None, None, None, None),
        (257000002, "BERGEN", "2026-10-01T18:00:00", 9123456, "LABC"),
    ]
    quarantined = rows(db_path, "SELECT destination, rejection_reason FROM ais_messages_quarantine")
    assert quarantined == [("BERGEN", "duplicate")]


def test_coverage_counts_only_fields_that_carry_a_value():
    snapshots = [
        (
            datetime(2026, 10, 1, 8, 0),
            [
                message(1, "2026-10-01 07:59:00", destination="OSLO", callSign=""),
                message(2, "2026-10-01 07:59:00", imoNumber=9123456),
            ],
        )
    ]

    assert voyage_field_coverage(snapshots) == (
        "Crew-typed fields present: navigationalStatus 0/2, "
        "destination 1/2, eta 0/2, imoNumber 1/2, callSign 0/2"
    )


def test_the_live_api_is_asked_for_the_full_model(monkeypatch):
    """The default "Simple" model leaves out status and every voyage field."""
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return [message(1, "2026-10-02 07:59:00", destination="OSLO")]

    def fake_get(url, **kwargs):
        calls.append(kwargs.get("params"))
        return Response()

    monkeypatch.setattr(ingestion, "get_access_token", lambda: "token")
    monkeypatch.setattr(ingestion.requests, "get", fake_get)

    assert ingestion.fetch_ais_data()[0]["destination"] == "OSLO"
    assert calls == [{"modelType": "Full", "modelFormat": "Json"}]
