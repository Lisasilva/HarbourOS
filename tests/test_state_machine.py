"""Tests for the port-call state machine."""
from datetime import datetime, timedelta

from HarbourOS.state_machine import (
    CONF_AGREES,
    CONF_CONTRADICTS,
    classify_message,
    classify_movement,
    derive_state_periods,
)

START = datetime(2026, 9, 4, 8, 0)
SHIP = 257000000


def journey(*blocks) -> list[dict]:
    """Build time-ordered readings from (count, speed, status) blocks."""
    messages = []
    minute = 0
    for count, speed, status in blocks:
        for _ in range(count):
            messages.append(
                {
                    "message_time": START + timedelta(minutes=minute),
                    "speed_over_ground": speed,
                    "navigational_status": status,
                }
            )
            minute += 1
    return messages


def test_classify_movement_thresholds():
    assert classify_movement(0.0) == "stopped"
    assert classify_movement(0.4) == "stopped"
    assert classify_movement(1.5) == "maneuvering"
    assert classify_movement(10.0) == "underway"
    assert classify_movement(None) == "unknown"


def test_moving_ship_claiming_moored_is_flagged_low_confidence():
    """Seen in real data: a vessel reporting 'moored' while doing 11 knots."""
    state, confidence, note = classify_message(speed=11.0, status=5)
    assert state == "at_sea"
    assert confidence == CONF_CONTRADICTS
    assert "claims_stopped" in note


def test_stopped_ship_with_matching_status_is_high_confidence():
    state, confidence, _ = classify_message(speed=0.0, status=5)
    assert state == "berthed"
    assert confidence == CONF_AGREES


def test_full_journey_produces_expected_state_sequence():
    messages = journey(
        (10, 12.0, 0),
        (5, 1.5, 0),
        (10, 0.0, 5),
        (5, 1.5, 0),
        (10, 12.0, 0),
    )
    periods = derive_state_periods(messages, mmsi=SHIP)
    assert [p.state for p in periods] == [
        "at_sea",
        "approach",
        "berthed",
        "departed",
        "at_sea",
    ]


def test_anchoring_is_distinguished_from_berthing():
    periods = derive_state_periods(journey((10, 0.0, 1)), mmsi=SHIP)
    assert [p.state for p in periods] == ["anchored"]


def test_single_noisy_reading_does_not_create_a_state():
    messages = journey((10, 12.0, 0), (1, 0.0, 0), (10, 12.0, 0))
    periods = derive_state_periods(messages, mmsi=SHIP)
    assert [p.state for p in periods] == ["at_sea"]


def test_long_silence_splits_into_separate_periods():
    """A 12-hour gap doesn't mean the ship sat still -- it means we weren't watching."""
    messages = journey((10, 0.0, 5))
    later = START + timedelta(hours=12)
    for index in range(10):
        messages.append(
            {
                "message_time": later + timedelta(minutes=index),
                "speed_over_ground": 0.0,
                "navigational_status": 5,
            }
        )
    periods = derive_state_periods(messages, mmsi=SHIP)
    assert len(periods) == 2
    assert all(p.state == "berthed" for p in periods)


def test_every_reading_is_accounted_for():
    """Same guarantee as the Silver layer: nothing is silently dropped."""
    messages = journey((1, 0.0, 5), (10, 12.0, 0), (1, 0.0, 0), (10, 0.0, 5))
    periods = derive_state_periods(messages, mmsi=SHIP)
    assert sum(p.n_readings for p in periods) == len(messages)


def test_isolated_readings_days_apart_do_not_merge_into_one_period():
    """Two lonely pings two days apart are two observations, not one long stop."""
    messages = [
        {"message_time": START, "speed_over_ground": 0.0, "navigational_status": 5},
        {
            "message_time": START + timedelta(days=2),
            "speed_over_ground": 0.0,
            "navigational_status": 5,
        },
    ]
    assert len(derive_state_periods(messages, mmsi=SHIP)) == 2


def test_a_stray_ping_days_earlier_does_not_stretch_a_later_period():
    """A ping from Monday must not back-date a stop that really began Wednesday."""
    messages = [{"message_time": START, "speed_over_ground": 0.0, "navigational_status": 5}]
    dense_start = START + timedelta(days=2)
    for index in range(10):
        messages.append(
            {
                "message_time": dense_start + timedelta(minutes=index),
                "speed_over_ground": 0.0,
                "navigational_status": 5,
            }
        )

    periods = derive_state_periods(messages, mmsi=SHIP)
    assert len(periods) == 2
    assert periods[1].start_time == dense_start
