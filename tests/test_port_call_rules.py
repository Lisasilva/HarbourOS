"""Changing the port-call rules re-derives every ship once, then goes back to
re-deriving only ships with new data."""

from pathlib import Path

import pytest

from HarbourOS import transform
from HarbourOS.storage import rules_version


def test_every_ship_is_rebuilt_once_after_the_rules_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warehouse.duckdb"

    # First run: nothing recorded yet, so everything is (re)built.
    assert transform.run_port_calls_transform(db_path=db) is True
    assert rules_version("port_calls", db_path=db) == transform.PORT_CALL_RULES_VERSION
    assert transform.run_port_calls_transform(db_path=db) is False

    monkeypatch.setattr(transform, "PORT_CALL_RULES_VERSION", transform.PORT_CALL_RULES_VERSION + 1)
    assert transform.run_port_calls_transform(db_path=db) is True
    assert transform.run_port_calls_transform(db_path=db) is False


def test_rebuild_all_can_be_asked_for(tmp_path: Path) -> None:
    db = tmp_path / "warehouse.duckdb"
    transform.run_port_calls_transform(db_path=db)

    assert transform.run_port_calls_transform(db_path=db, rebuild_all=True) is True
