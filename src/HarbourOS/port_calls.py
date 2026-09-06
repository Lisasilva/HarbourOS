"""Group state periods into port-call events -- one row per visit.

A state period says "this ship was berthed from A to B". A port call is the
business event around it: when it arrived, how long it stayed, when it left,
and whether both ends were actually observed.

A visit ends only when the vessel returns to sea. A ship drifting on its
mooring lines for twenty minutes has not left port and come back -- without
this rule, one real stop is miscounted as several short port calls.

Durations are stored as whole minutes: exact, and readable without mental
arithmetic. Formatting into "2h 15m" belongs in the dashboard, not the data.

Merging visits to the same port across separate sea passages needs port
identity, which arrives with the UN/LOCODE dimension in Day 6.
"""

from dataclasses import dataclass
from datetime import datetime

from HarbourOS.state_machine import MAX_GAP, StatePeriod

STOP_STATES = ("berthed", "anchored")
# A visit backed by a handful of isolated pings is not evidence of a port
# call -- it is evidence that we were not watching.
MIN_VISIT_READINGS = 3


@dataclass
class PortCall:
    """One visit: a ship stopping, with whatever context surrounds it."""

    mmsi: int
    stop_type: str
    arrival_time: datetime
    berth_start: datetime
    berth_end: datetime
    departure_time: datetime
    minutes_alongside: int
    n_readings: int
    confidence: float
    completeness: str


def _completeness(has_before: bool, has_after: bool) -> str:
    """Did we witness the whole visit, or only part of it?"""
    if has_before and has_after:
        return "complete"
    if has_after:
        return "arrival_unobserved"
    if has_before:
        return "departure_unobserved"
    return "both_unobserved"


def _visit_spans(periods: list[StatePeriod]) -> list[tuple[int, int]]:
    """Index ranges covering each visit: first stop to last stop.

    A visit ends when the vessel returns to sea -- or when we lose sight of
    it. Slow shuffling between stops stays inside the same visit, but a long
    observation gap does not: a blind spot is not proof the ship stayed put.
    """
    spans: list[tuple[int, int]] = []
    first_stop: int | None = None
    last_stop = 0
    previous_end = None

    for index, period in enumerate(periods):
        lost_sight = previous_end is not None and (period.start_time - previous_end) > MAX_GAP
        if lost_sight and first_stop is not None:
            spans.append((first_stop, last_stop))
            first_stop = None
            last_stop = 0

        if period.state in STOP_STATES:
            if first_stop is None:
                first_stop = index
            last_stop = index
        elif period.state == "at_sea" and first_stop is not None:
            spans.append((first_stop, last_stop))
            first_stop = None
            last_stop = 0

        previous_end = period.end_time

    if first_stop is not None:
        spans.append((first_stop, last_stop))

    return spans


def derive_port_calls(periods: list[StatePeriod]) -> list[PortCall]:
    """Turn one ship's ordered state periods into port-call events."""
    calls: list[PortCall] = []

    for first_stop, last_stop in _visit_spans(periods):
        span = periods[first_stop : last_stop + 1]

        if sum(period.n_readings for period in span) < MIN_VISIT_READINGS:
            continue

        before = periods[first_stop - 1] if first_stop > 0 else None
        after = periods[last_stop + 1] if last_stop + 1 < len(periods) else None

        if before is not None and before.state == "approach":
            arrival = before.start_time
        else:
            arrival = span[0].start_time

        if after is not None and after.state == "departed":
            departure = after.end_time
        else:
            departure = span[-1].end_time

        berth_start = span[0].start_time
        berth_end = span[-1].end_time
        minutes = int((berth_end - berth_start).total_seconds() // 60)

        readings = sum(period.n_readings for period in span)
        weighted = sum(period.confidence * period.n_readings for period in span)

        stopped_as = "berthed" if any(p.state == "berthed" for p in span) else "anchored"

        calls.append(
            PortCall(
                mmsi=span[0].mmsi,
                stop_type=stopped_as,
                arrival_time=arrival,
                berth_start=berth_start,
                berth_end=berth_end,
                departure_time=departure,
                minutes_alongside=minutes,
                n_readings=readings,
                confidence=round(weighted / readings, 2) if readings else 0.0,
                completeness=_completeness(before is not None, after is not None),
            )
        )

    return calls
