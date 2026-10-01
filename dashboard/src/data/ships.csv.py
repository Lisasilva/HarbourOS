"""Observable data loader: every ship's latest position, for the live map.

Served as data/ships.csv, one row per ship seen in the last 7 days (the span
of fct_vessel_track). The map shows the ships seen recently as arrows (moving)
or dots (stopped); the rest are still listed so a ship can be looked up after
it has gone quiet.

last_port is the port of the ship's most recent port call, and in_port_now
uses the same recency rule as port_calls.csv.py, so the map and the port-call
numbers agree on which ships are in port. It also needs that latest stop to be
a port call: a boat working at a fish farm or stopped at sea isn't in port.
"""

import sys

from HarbourOS.storage import connect

# Must match port_calls.csv.py.
IN_PORT_RECENCY_HOURS = 6

QUERY = f"""
    with freshness as (
        select max(message_time) as latest_reading
        from ais_messages_silver
    ),
    latest_position as (
        select *
        from fct_vessel_track
        qualify row_number() over (partition by mmsi order by slot_start desc) = 1
    ),
    latest_call as (
        select *
        from fact_port_call
        where mmsi in (select mmsi from latest_position)
        qualify row_number() over (partition by mmsi order by berth_start desc) = 1
    )
    select
        t.mmsi,
        v.vessel_name,
        v.ship_category,
        strftime(t.message_time, '%Y-%m-%dT%H:%M:%SZ') as last_seen,
        round(t.latitude, 5) as latitude,
        round(t.longitude, 5) as longitude,
        t.speed_over_ground,
        t.course_over_ground,
        t.true_heading,
        t.navigational_status,
        p.port_name as last_port,
        c.visit_type = 'port_call'
            and c.completeness in ('departure_unobserved', 'both_unobserved')
            and c.berth_end >= freshness.latest_reading
                - interval '{IN_PORT_RECENCY_HOURS} hours'
            as in_port_now
    from latest_position t
    left join dim_vessel v on v.mmsi = t.mmsi
    left join latest_call c on c.mmsi = t.mmsi
    left join dim_port p on p.port_locode = c.port_locode
    cross join freshness
    order by t.mmsi
"""

with connect() as conn:
    conn.execute(QUERY).df().to_csv(sys.stdout, index=False)
