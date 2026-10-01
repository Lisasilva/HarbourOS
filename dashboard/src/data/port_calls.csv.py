"""Observable data loader: export the Gold port calls as CSV for the dashboard.

Observable Framework runs this at build time and serves whatever it prints as
data/port_calls.csv. Reading the warehouse here, rather than committing an
exported file, is what lets the dashboard rebuild itself after every pipeline
run instead of freezing at whenever someone last exported by hand.

in_port_now flags a visit whose departure hasn't been observed (completeness
is 'departure_unobserved' or 'both_unobserved') AND that is recent relative
to the freshest data the pipeline has -- not just "no later period exists".
Without the recency half, a ship that stopped transmitting weeks ago would
say "in port now" forever, since nothing ever arrives to contradict it. The
threshold is computed here, at build time against the live warehouse, rather
than baked into the incremental fact_port_call table -- that table only
reprocesses rows for ships with new data, so a static column would go stale
in exactly the same way.
"""

import sys

from HarbourOS.storage import connect

# How stale a visit's last sighting can be and still count as "in port now".
# Generous on purpose: it has to cover both the old once-a-day cadence and
# the current 10-minute one (see CLAUDE.md), and a false "still there" is a
# smaller error than flip-flopping a real ongoing visit to "unknown" between
# runs.
IN_PORT_RECENCY_HOURS = 6

QUERY = f"""
    with freshness as (
        select max(message_time) as latest_reading
        from ais_messages_silver
    )
    select
        f.mmsi,
        v.vessel_name,
        v.ship_category,
        f.port_locode,
        p.port_name,
        p.latitude as port_latitude,
        p.longitude as port_longitude,
        f.stop_type,
        f.visit_type,
        f.berth_name,
        f.anchorage_name,
        f.completeness,
        strftime(f.berth_start, '%Y-%m-%dT%H:%M:%SZ') as berth_start,
        strftime(f.berth_end, '%Y-%m-%dT%H:%M:%SZ') as berth_end,
        f.minutes_alongside,
        f.confidence,
        f.nearest_port_km,
        f.stop_latitude,
        f.stop_longitude,
        f.completeness in ('departure_unobserved', 'both_unobserved')
            and f.berth_end >= freshness.latest_reading
                - interval '{IN_PORT_RECENCY_HOURS} hours'
            as in_port_now
    from fact_port_call f
    left join dim_vessel v on v.mmsi = f.mmsi
    left join dim_port p on p.port_locode = f.port_locode
    cross join freshness
    order by f.berth_start
"""

with connect() as conn:
    conn.execute(QUERY).df().to_csv(sys.stdout, index=False)
