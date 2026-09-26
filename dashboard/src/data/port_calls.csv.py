"""Observable data loader: export the Gold port calls as CSV for the dashboard.

Observable Framework runs this at build time and serves whatever it prints as
data/port_calls.csv. Reading the warehouse here, rather than committing an
exported file, is what lets the dashboard rebuild itself after every pipeline
run instead of freezing at whenever someone last exported by hand.
"""

import sys

from HarbourOS.storage import connect

QUERY = """
    select
        f.mmsi,
        v.vessel_name,
        p.port_name,
        f.stop_type,
        f.completeness,
        f.berth_start,
        f.minutes_alongside,
        f.confidence,
        f.nearest_port_km,
        f.stop_latitude,
        f.stop_longitude
    from fact_port_call f
    left join dim_vessel v on v.mmsi = f.mmsi
    left join dim_port p on p.port_locode = f.port_locode
    order by f.berth_start
"""

with connect() as conn:
    conn.execute(QUERY).df().to_csv(sys.stdout, index=False)
