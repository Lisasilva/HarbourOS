"""Tests for grouping state periods into port-call events."""
from datetime import datetime, timedelta

from HarbourOS.port_calls import derive_port_calls
from HarbourOS.state_machine import StatePeriod

START = datetime(2026, 9, 4, 8, 0)
SHIP = 257000000


def period(state: str, start_minute: int, end_minute: int) -> StatePeriod:
    return StatePeriod(
        mmsi=SHIP,
        state=state,
        start_time=START + timedelta(minutes=start_minute),
        end_time=START + timedelta(minutes=end_minute),
        n_readings=10,
        confidence=1.0,
        note="test",
    )


def test_complete_visit_becomes_one_port_call():
    periods = [
        period("at_sea", 0, 60),
        period("approach", 60, 70),
        period("berthed", 70, 190),
        period("departed", 190, 200),
        period("at_sea", 200, 260),
    ]
    calls = derive_port_calls(periods)

    assert len(calls) == 1
    call = calls[0]
    assert call.stop_type == "berthed"
    assert call.completeness == "complete"
    assert call.minutes_alongside == 120
    assert call.arrival_time == START + timedelta(minutes=60)
    assert call.departure_time == START + timedelta(minutes=200)


def test_wobble_at_the_berth_does_not_end_the_visit():
    """Drifting on the mooring lines is not leaving port and coming back."""
    periods = [
        period("at_sea", 0, 60),
        period("approach", 60, 70),
        period("berthed", 70, 190),
        period("departed", 190, 210),
        period("berthed", 210, 330),
        period("departed", 330, 340),
        period("at_sea", 340, 400),
    ]
    calls = derive_port_calls(periods)

    assert len(calls) == 1
    assert calls[0].berth_start == START + timedelta(minutes=70)
    assert calls[0].berth_end == START + timedelta(minutes=330)
    assert calls[0].minutes_alongside == 260


def test_returning_to_sea_ends_the_visit():
    periods = [
        period("berthed", 0, 60),
        period("departed", 60, 70),
        period("at_sea", 70, 200),
        period("approach", 200, 210),
        period("berthed", 210, 300),
    ]
    assert len(derive_port_calls(periods)) == 2


def test_stop_already_underway_when_our_data_starts_is_flagged():
    calls = derive_port_calls([period("berthed", 0, 120), period("departed", 120, 130)])
    assert calls[0].completeness == "arrival_unobserved"


def test_stop_still_ongoing_when_our_data_ends_is_flagged():
    calls = derive_port_calls([period("at_sea", 0, 60), period("berthed", 60, 180)])
    assert calls[0].completeness == "departure_unobserved"


def test_anchoring_is_recorded_as_its_own_stop_type():
    periods = [
        period("at_sea", 0, 60),
        period("anchored", 60, 120),
        period("departed", 120, 130),
    ]
    assert derive_port_calls(periods)[0].stop_type == "anchored"


def test_a_ship_that_never_stops_has_no_port_calls():
    assert derive_port_calls([period("at_sea", 0, 600)]) == []


def test_a_visit_backed_by_a_single_stray_ping_is_not_reported():
    """One lonely ping is not proof a ship visited a port."""
    lonely = StatePeriod(
        mmsi=SHIP,
        state="berthed",
        start_time=START,
        end_time=START,
        n_readings=1,
        confidence=0.5,
        note="test",
    )
    assert derive_port_calls([lonely]) == []


def test_losing_sight_of_a_ship_ends_the_visit():
    """A two-day observation gap is not proof the ship sat at the berth."""
    periods = [period("berthed", 0, 60), period("berthed", 2940, 3000)]
    assert len(derive_port_calls(periods)) == 2
