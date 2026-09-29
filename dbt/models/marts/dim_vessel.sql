-- Vessel dimension, one row per MMSI observed.
--
-- A ship re-broadcasts its name and type periodically, and crews do correct
-- them, so we take the most recent value rather than the first. arg_max picks
-- the value of one column at the row where another column is highest.
--
-- ship_category turns the numeric AIS ship type (ITU-R M.1371) into the few
-- groups the dashboard filters and colours by. Same grouping as the audit
-- (src/HarbourOS/audit.py), so their numbers line up. Passenger covers ferries,
-- which is what lets the dashboard keep ferry quays out of the cargo view.

with vessels as (
    select
        mmsi,
        arg_max(name, message_time) as vessel_name,
        arg_max(ship_type, message_time) as ship_type,
        min(message_time) as first_seen,
        max(message_time) as last_seen,
        count(*) as n_readings
    from {{ source('harbouros', 'ais_messages_silver') }}
    group by mmsi
)

select
    *,
    case
        when ship_type = 30 then 'fishing'
        when ship_type in (31, 32, 52) then 'towing / tug'
        when ship_type between 50 and 59 then 'pilot, rescue, service'
        when ship_type between 60 and 69 then 'passenger'
        when ship_type between 70 and 79 then 'cargo'
        when ship_type between 80 and 89 then 'tanker'
        when ship_type between 36 and 37 then 'leisure'
        when ship_type is null or ship_type = 0 then 'unknown'
        else 'other'
    end as ship_category
from vessels
