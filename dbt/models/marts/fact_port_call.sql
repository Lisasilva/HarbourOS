-- The fact table. Grain: one vessel stopping once.
--
-- Each visit is matched to the nearest known seaport by great-circle distance.
-- UN/LOCODE positions are accurate to the nearest minute of arc (~1.8 km) and
-- mark the locality, not the quay -- so this is a proximity match, not a
-- certainty. The distance is kept as a column so it can be judged: a match at
-- 0.4 km is solid, one at 9 km deserves suspicion. Beyond the cutoff we record
-- no port at all rather than inventing one.
--
-- Haversine is computed in plain SQL rather than pulling in DuckDB's spatial
-- extension: one formula, no runtime dependency, and exact enough for source
-- data that is itself only accurate to about 2 km.

with calls as (
    select * from {{ ref('stg_port_calls') }}
),

ports as (
    select * from {{ ref('dim_port') }}
),

distances as (
    select
        calls.mmsi,
        calls.berth_start,
        ports.port_locode,
        6371 * 2 * asin(sqrt(
            pow(sin(radians(ports.latitude - calls.stop_latitude) / 2), 2)
            + cos(radians(calls.stop_latitude))
            * cos(radians(ports.latitude))
            * pow(sin(radians(ports.longitude - calls.stop_longitude) / 2), 2)
        )) as distance_km
    from calls
    cross join ports
    where calls.stop_latitude is not null
),

nearest as (
    select
        mmsi,
        berth_start,
        port_locode,
        distance_km
    from distances
    qualify row_number() over (
        partition by mmsi, berth_start order by distance_km
    ) = 1
)

select
    md5(cast(calls.mmsi as varchar) || '|' || cast(calls.berth_start as varchar))
        as port_call_key,
    calls.mmsi,
    case
        when nearest.distance_km <= {{ var('port_match_km', 10) }}
        then nearest.port_locode
    end as port_locode,
    round(nearest.distance_km, 2) as nearest_port_km,
    cast(strftime(calls.arrival_time, '%Y%m%d') as integer) as arrival_date_key,
    calls.stop_type,
    calls.completeness,
    calls.arrival_time,
    calls.berth_start,
    calls.berth_end,
    calls.departure_time,
    calls.minutes_alongside,
    calls.n_readings,
    calls.confidence,
    calls.stop_latitude,
    calls.stop_longitude
from calls
left join nearest
    on nearest.mmsi = calls.mmsi
    and nearest.berth_start = calls.berth_start
