-- Date dimension covering exactly the days we observed.
--
-- A date dimension exists so questions like "how do weekend turnarounds
-- compare to weekdays" are a join, not a pile of date functions repeated in
-- every query.

with bounds as (
    select
        min(cast(message_time as date)) as first_date,
        max(cast(message_time as date)) as last_date
    from {{ source('harbouros', 'ais_messages_silver') }}
),

calendar as (
    select cast(
        unnest(generate_series(first_date, last_date, interval 1 day)) as date
    ) as date_day
    from bounds
)

select
    cast(strftime(date_day, '%Y%m%d') as integer) as date_key,
    date_day,
    year(date_day) as calendar_year,
    month(date_day) as calendar_month,
    day(date_day) as calendar_day,
    strftime(date_day, '%A') as day_name,
    dayofweek(date_day) in (0, 6) as is_weekend
from calendar
