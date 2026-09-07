{{ config(materialized='view') }}

-- UN/LOCODE stores a position as "6753N 01259E": degrees and minutes jammed
-- together with a hemisphere letter. The last two digits before the letter are
-- MINUTES, not a decimal fraction -- 6753N is 67 degrees 53 minutes, which is
-- 67.88 degrees, NOT 67.53. Getting this wrong moves every port by up to
-- 35 km, quietly and plausibly. Convert once, here, so nothing downstream
-- ever touches the raw string.

with raw_ports as (
    select * from {{ ref('un_locode_ports') }}
),

parsed as (
    select
        locode,
        port_name,
        subdivision,
        status,
        coordinates,
        cast(substr(coordinates, 1, 2) as integer) as lat_degrees,
        cast(substr(coordinates, 3, 2) as integer) as lat_minutes,
        substr(coordinates, 5, 1) as lat_hemisphere,
        cast(substr(coordinates, 7, 3) as integer) as lon_degrees,
        cast(substr(coordinates, 10, 2) as integer) as lon_minutes,
        substr(coordinates, 12, 1) as lon_hemisphere
    from raw_ports
)

select
    locode,
    port_name,
    subdivision,
    status,
    coordinates as raw_coordinates,
    (lat_degrees + lat_minutes / 60.0)
        * case when lat_hemisphere = 'S' then -1 else 1 end as latitude,
    (lon_degrees + lon_minutes / 60.0)
        * case when lon_hemisphere = 'W' then -1 else 1 end as longitude
from parsed
