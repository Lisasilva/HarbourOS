{{ config(materialized='view') }}

-- Where did each visit happen?
--
-- The state machine deliberately doesn't carry position. Anything recoverable
-- by joining and aggregating belongs in SQL, not in Python -- Python is only
-- there for the sequential logic SQL can't express. So a stop's location is
-- simply the average of the cleaned readings that fall inside it.
--
-- built_from_received_at is carried through from the Python layer so the Gold
-- model downstream can tell which vessels have actually changed.

with calls as (
    select * from {{ source('harbouros', 'port_call_events') }}
),

stop_positions as (
    select
        calls.mmsi,
        calls.berth_start,
        avg(silver.latitude) as stop_latitude,
        avg(silver.longitude) as stop_longitude,
        count(*) as position_readings
    from calls
    inner join {{ source('harbouros', 'ais_messages_silver') }} as silver
        on silver.mmsi = calls.mmsi
        and silver.message_time between calls.berth_start and calls.berth_end
    group by calls.mmsi, calls.berth_start
),

built as (
    select mmsi, built_from_received_at
    from {{ source('harbouros', 'derived_progress') }}
    where layer = 'port_calls'
)

select
    calls.*,
    stop_positions.stop_latitude,
    stop_positions.stop_longitude,
    stop_positions.position_readings,
    built.built_from_received_at
from calls
left join stop_positions
    on stop_positions.mmsi = calls.mmsi
    and stop_positions.berth_start = calls.berth_start
left join built
    on built.mmsi = calls.mmsi
