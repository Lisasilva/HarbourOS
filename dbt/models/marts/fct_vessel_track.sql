-- Where each ship was, for drawing routes on the dashboard map.
-- Grain: one ship in one 10-minute slot, over the last 7 days of data.
--
-- The collector takes a snapshot every 10 minutes, so a slot usually holds
-- one reading; when it holds more, the latest one is kept. Seven days is
-- enough to follow a ship between ports while keeping the table small, and
-- "last 7 days" is measured from the newest reading rather than the clock,
-- so a paused pipeline still shows its last week instead of an empty map.
--
-- Rebuilt in full each run. It only reads the last 7 days of Silver, so the
-- work per run stays the same however long the history grows.

with latest as (
    select max(message_time) as latest_reading
    from {{ source('harbouros', 'ais_messages_silver') }}
),

recent as (
    select s.*
    from {{ source('harbouros', 'ais_messages_silver') }} as s, latest
    where s.message_time > latest.latest_reading - interval 7 days
)

select
    mmsi,
    time_bucket(interval 10 minutes, message_time) as slot_start,
    max(message_time) as message_time,
    arg_max(latitude, message_time) as latitude,
    arg_max(longitude, message_time) as longitude,
    arg_max(speed_over_ground, message_time) as speed_over_ground,
    arg_max(course_over_ground, message_time) as course_over_ground,
    arg_max(true_heading, message_time) as true_heading,
    arg_max(navigational_status, message_time) as navigational_status
from recent
group by mmsi, slot_start
