-- Vessel dimension, one row per MMSI observed.
--
-- A ship re-broadcasts its name and type periodically, and crews do correct
-- them, so we take the most recent value rather than the first. arg_max picks
-- the value of one column at the row where another column is highest.

select
    mmsi,
    arg_max(name, message_time) as vessel_name,
    arg_max(ship_type, message_time) as ship_type,
    min(message_time) as first_seen,
    max(message_time) as last_seen,
    count(*) as n_readings
from {{ source('harbouros', 'ais_messages_silver') }}
group by mmsi
