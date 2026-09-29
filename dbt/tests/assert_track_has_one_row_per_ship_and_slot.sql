-- fct_vessel_track has one row per ship per 10-minute slot.
select mmsi, slot_start, count(*) as n
from {{ ref('fct_vessel_track') }}
group by mmsi, slot_start
having count(*) > 1
