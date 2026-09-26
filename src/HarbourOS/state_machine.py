"""Derive confidence-scored port-call states from AIS positions.

Design note: navigational_status is typed in by the crew and is often stale
or wrong -- observed in real BarentsWatch data, a vessel reported "moored"
while averaging 7 knots for 11 hours. Speed over ground is measured by GPS,
not declared by a person, so this machine is speed-first: status is used
only to corroborate, raising or lowering a confidence score.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

# Speed thresholds, in knots.
STOPPED_MAX_KNOTS = 0.5
MANEUVERING_MAX_KNOTS = 3.0

# Consecutive readings required before committing to a new state (anti-jitter).
DWELL_READINGS = 3

# Longer than this between messages means we lost sight of the ship.
MAX_GAP = timedelta(minutes=30)

# AIS navigational status codes.
STATUS_AT_ANCHOR = 1
STATUS_MOORED = 5
STATUS_UNDERWAY = (0, 8)

CONF_AGREES = 1.0
CONF_NO_SIGNAL = 0.5
CONF_CONTRADICTS = 0.3

STOPPED_STATES = ("anchored", "berthed")


@dataclass
class StatePeriod:
    """One continuous stretch of time a ship spent in one state."""

    mmsi: int
    state: str
    start_time: datetime
    end_time: datetime
    n_readings: int
    confidence: float
    note: str


@dataclass
class _Run:
    """Internal: a stretch of consecutive readings sharing one state."""

    state: str
    start: datetime
    end: datetime
    n: int
    conf_sum: float
    min_conf: float
    note: str


def classify_movement(speed) -> str:
    """What the GPS says the ship is physically doing."""
    if speed is None:
        return "unknown"
    speed = float(speed)
    if speed < STOPPED_MAX_KNOTS:
        return "stopped"
    if speed < MANEUVERING_MAX_KNOTS:
        return "maneuvering"
    return "underway"


def classify_message(speed, status) -> tuple[str, float, str]:
    """Classify a single reading: (state, confidence, note)."""
    movement = classify_movement(speed)

    if movement == "unknown":
        return "unknown", 0.0, "no_speed_reported"

    if movement == "stopped":
        if status == STATUS_AT_ANCHOR:
            return "anchored", CONF_AGREES, "speed_and_status_agree"
        if status == STATUS_MOORED:
            return "berthed", CONF_AGREES, "speed_and_status_agree"
        if status in STATUS_UNDERWAY:
            return "berthed", CONF_CONTRADICTS, "status_claims_underway_but_stopped"
        return "berthed", CONF_NO_SIGNAL, "no_usable_status_needs_port_proximity"

    if movement == "underway":
        if status in STATUS_UNDERWAY:
            return "at_sea", CONF_AGREES, "speed_and_status_agree"
        if status in (STATUS_AT_ANCHOR, STATUS_MOORED):
            return "at_sea", CONF_CONTRADICTS, "status_claims_stopped_but_moving"
        return "at_sea", CONF_NO_SIGNAL, "no_usable_status"

    # Slow but moving -- renamed to approach/departed from context below.
    return "maneuvering", CONF_NO_SIGNAL, "slow_transitional_speed"


def _group_into_runs(messages: list[dict]) -> list[_Run]:
    """Collapse consecutive same-state readings into runs, splitting on gaps."""
    runs: list[_Run] = []

    for message in messages:
        time = message["message_time"]
        state, conf, note = classify_message(
            message.get("speed_over_ground"), message.get("navigational_status")
        )

        if runs and runs[-1].state == state and (time - runs[-1].end) <= MAX_GAP:
            run = runs[-1]
            run.end = time
            run.n += 1
            run.conf_sum += conf
            if conf < run.min_conf:
                run.min_conf = conf
                run.note = note
        else:
            runs.append(
                _Run(
                    state=state,
                    start=time,
                    end=time,
                    n=1,
                    conf_sum=conf,
                    min_conf=conf,
                    note=note,
                )
            )

    return runs


def _absorb(target: _Run, other: _Run) -> None:
    """Fold one run into another, keeping the worst confidence seen."""
    target.end = other.end
    target.n += other.n
    target.conf_sum += other.conf_sum
    if other.min_conf < target.min_conf:
        target.min_conf = other.min_conf
        target.note = other.note


def _prepend(target: _Run, earlier: list[_Run]) -> None:
    """Attach leading short runs to the first believable run that follows."""
    target.start = earlier[0].start
    for run in earlier:
        target.n += run.n
        target.conf_sum += run.conf_sum
        if run.min_conf < target.min_conf:
            target.min_conf = run.min_conf
            target.note = run.note


def _denoise(runs: list[_Run]) -> list[_Run]:
    """Absorb runs too short to believe (GPS jitter), without losing readings.

    Every reading must end up inside exactly one period -- the same
    "nothing is silently dropped" rule the Silver layer follows.
    """
    kept: list[_Run] = []
    pending: list[_Run] = []

    for run in runs:
        too_short = run.n < DWELL_READINGS
        joinable = bool(kept) and (run.start - kept[-1].end) <= MAX_GAP

        if too_short and not joinable:
            if pending and (run.start - pending[-1].end) > MAX_GAP:
                kept.extend(pending)
                pending = []
            pending.append(run)
            continue
        if too_short:
            _absorb(kept[-1], run)
            continue

        if pending:
            if (run.start - pending[-1].end) <= MAX_GAP:
                _prepend(run, pending)
            else:
                kept.extend(pending)
            pending = []

        same_state = bool(kept) and kept[-1].state == run.state
        if same_state and (run.start - kept[-1].end) <= MAX_GAP:
            _absorb(kept[-1], run)
            continue

        kept.append(run)

    kept.extend(pending)
    return kept


def _name_transitions(runs: list[_Run], previous_state: str | None = None) -> list[_Run]:
    """A slow-moving stretch is 'approach' coming in, 'departed' going out."""
    for index, run in enumerate(runs):
        if run.state != "maneuvering":
            continue
        before = runs[index - 1].state if index > 0 else previous_state
        run.state = "departed" if before in STOPPED_STATES else "approach"
    return runs


def derive_state_periods(
    messages: list[dict], mmsi: int, previous_state: str | None = None
) -> list[StatePeriod]:
    """Turn one ship's time-ordered AIS readings into confidence-scored states.

    `previous_state` is the state of the period just before `messages` begin,
    for when only the tail of a ship's history is being rebuilt. It matters
    only for naming a leading slow stretch 'approach' or 'departed'.
    """
    runs = _name_transitions(_denoise(_group_into_runs(messages)), previous_state)
    return [
        StatePeriod(
            mmsi=mmsi,
            state=run.state,
            start_time=run.start,
            end_time=run.end,
            n_readings=run.n,
            confidence=round(run.conf_sum / run.n, 2),
            note=run.note,
        )
        for run in runs
    ]
