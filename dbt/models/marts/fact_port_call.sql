{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='mmsi',
    on_schema_change='fail'
) }}

-- The fact table. Grain: one vessel stopping once.
--
-- Each visit is matched to the nearest known seaport by great-circle distance.
-- UN/LOCODE positions are accurate to the nearest minute of arc (~1.8 km) and
-- mark the locality, not the quay -- so this is a proximity match, not a
-- certainty. The distance is kept as a column so it can be judged: a match at
-- 0.4 km is solid, one at 9 km deserves suspicion. Beyond the cutoff we record
-- no port at all rather than inventing one.
--
-- visit_type says what kind of stop this was: 'port_call' when a seaport was
-- matched and the ship was at a berth (below), 'at_sea' when no seaport is
-- near. An audit of the first weeks of data found 14%
-- of stops over 10 km from any seaport -- mostly oil rigs and supply ships in
-- the North Sea fields. Those are real stops, just not port calls, so they
-- get their own label rather than silently counting toward port traffic.
--
-- 'fish_farm' is a stop within 300 m of a fish farm. The reliability check
-- (2026-10-01) found service boats parked at farms for hours, often within
-- 10 km of a listed port, so they had been counted as port calls. A farm
-- takes precedence over a nearby port: a boat moored at a farm is working
-- there, not calling at the town. The farm's name is kept in fish_farm_name.
--
-- A port call must also be at a berth: within berth_match_m (500 m) of a
-- quay, ferry quay, port facility or harbour in Kystverket's official
-- location register (seed kystverket_locations, the list ships use when they
-- report port calls to the authorities). The reliability check (2026-10-01)
-- found that many "port calls" were ships waiting off the coast, a few
-- kilometres from town. A stop within port_match_km of a port but not at any
-- berth, or near one of Kystverket's official anchorages, is 'anchorage':
-- anchored or waiting, not alongside. berth_name / berth_m and
-- anchorage_name keep the nearest official places so this can be judged,
-- and port_locode is set for port calls only.
-- The 500 m was chosen on 2026-10-01 data: of stops within 500 m of an
-- official berth, 77% were also beside a quay on OpenStreetMap (an
-- independent map), against 20% of the stops further away.
--
-- confidence combines two independent kinds of evidence that a stop was real.
-- status_confidence is the state machine's score: does the status the crew
-- typed agree with the speed the GPS measured? That says more about how
-- carefully crews keep their status than about whether the stop happened.
-- The second is location: a ship stopped within a couple of kilometres of a
-- listed port is very likely at that port whatever its status says. The
-- final confidence is the stronger of the two, and status_confidence is kept
-- so the original score can still be inspected.
--
-- Haversine is computed in plain SQL rather than pulling in DuckDB's spatial
-- extension: one formula, no runtime dependency, and exact enough for source
-- data that is itself only accurate to about 2 km.
--
-- Incremental, and the reason is the cross join below: every visit is measured
-- against all 610 seaports. Rebuilding all visits every run means that cost
-- grows with total history rather than with new data. Deletion is keyed on
-- mmsi, not on port_call_key, because a vessel's visits are recomputed as a
-- set -- if two visits merge into one, keying on the visit would strand the
-- stale row.
--
-- on_schema_change='fail': an incremental run never adds a new column to the
-- existing table, so a column added here would be missing in the warehouse
-- until someone ran a full refresh. Failing loudly lets the pipeline notice
-- and rebuild the table from scratch (see .github/workflows/pipeline.yml).

with calls as (
    select * from {{ ref('stg_port_calls') }}

    {% if is_incremental() %}
    where built_from_received_at > (
        select coalesce(max(built_from_received_at), timestamp '1970-01-01')
        from {{ this }}
    )
    {% endif %}
),

ports as (
    select * from {{ ref('dim_port') }}
),

-- Bounding boxes, so "within 300 m" is a box test widened by 300 m (longitude
-- degrees shrink towards the pole, hence the cos()). A farm is a few hundred
-- metres across, so the box is close to its real outline.
at_fish_farm as (
    select
        calls.mmsi,
        calls.berth_start,
        min(farms.name) as fish_farm_name
    from calls
    inner join {{ source('harbouros', 'fish_farms') }} as farms
        on calls.stop_latitude
            between farms.min_lat - {{ var('fish_farm_m', 300) }} / 111320.0
            and farms.max_lat + {{ var('fish_farm_m', 300) }} / 111320.0
        and calls.stop_longitude
            between farms.min_lon
                - {{ var('fish_farm_m', 300) }} / 111320.0 / cos(radians(calls.stop_latitude))
            and farms.max_lon
                + {{ var('fish_farm_m', 300) }} / 111320.0 / cos(radians(calls.stop_latitude))
    group by calls.mmsi, calls.berth_start
),

-- Kystverket's places are points, so the nearest of each kind is found by
-- distance, after a box test (about 3 km) keeps the comparison small.
official as (
    select
        calls.mmsi,
        calls.berth_start,
        places.kind,
        places.location_name,
        6371000 * 2 * asin(sqrt(
            pow(sin(radians(places.latitude - calls.stop_latitude) / 2), 2)
            + cos(radians(calls.stop_latitude))
            * cos(radians(places.latitude))
            * pow(sin(radians(places.longitude - calls.stop_longitude) / 2), 2)
        )) as distance_m
    from calls
    inner join {{ ref('kystverket_locations') }} as places
        on places.latitude between calls.stop_latitude - 0.03 and calls.stop_latitude + 0.03
        and places.longitude
            between calls.stop_longitude - 0.03 / cos(radians(calls.stop_latitude))
            and calls.stop_longitude + 0.03 / cos(radians(calls.stop_latitude))
    where places.kind in ('quay', 'ferry_quay', 'port_facility', 'harbour', 'anchorage')
),

nearest_berth as (
    select mmsi, berth_start, location_name as berth_name, distance_m as berth_m
    from official
    where kind <> 'anchorage'
    qualify row_number() over (partition by mmsi, berth_start order by distance_m) = 1
),

nearest_anchorage as (
    select mmsi, berth_start, location_name as anchorage_name
    from official
    where kind = 'anchorage' and distance_m <= {{ var('anchorage_m', 1500) }}
    qualify row_number() over (partition by mmsi, berth_start order by distance_m) = 1
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
        when at_fish_farm.mmsi is null
            and nearest.distance_km <= {{ var('port_match_km', 10) }}
            and nearest_berth.berth_m <= {{ var('berth_match_m', 500) }}
            and nearest_anchorage.mmsi is null
        then nearest.port_locode
    end as port_locode,
    case
        when at_fish_farm.mmsi is not null then 'fish_farm'
        when nearest.distance_km <= {{ var('port_match_km', 10) }}
            and nearest_berth.berth_m <= {{ var('berth_match_m', 500) }}
            and nearest_anchorage.mmsi is null
        then 'port_call'
        when nearest.distance_km <= {{ var('port_match_km', 10) }}
            or nearest_anchorage.mmsi is not null
        then 'anchorage'
        else 'at_sea'
    end as visit_type,
    at_fish_farm.fish_farm_name,
    nearest_berth.berth_name,
    round(nearest_berth.berth_m) as berth_m,
    nearest_anchorage.anchorage_name,
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
    calls.confidence as status_confidence,
    round(greatest(
        calls.confidence,
        case
            when nearest.distance_km <= {{ var('port_close_km', 2) }} then 0.8
            when nearest.distance_km <= {{ var('port_near_km', 5) }} then 0.6
            else 0
        end
    ), 2) as confidence,
    calls.stop_latitude,
    calls.stop_longitude,
    calls.built_from_received_at
from calls
left join nearest
    on nearest.mmsi = calls.mmsi
    and nearest.berth_start = calls.berth_start
left join at_fish_farm
    on at_fish_farm.mmsi = calls.mmsi
    and at_fish_farm.berth_start = calls.berth_start
left join nearest_berth
    on nearest_berth.mmsi = calls.mmsi
    and nearest_berth.berth_start = calls.berth_start
left join nearest_anchorage
    on nearest_anchorage.mmsi = calls.mmsi
    and nearest_anchorage.berth_start = calls.berth_start
