-- Geographic sanity: mainland Norway spans roughly 57-72N, 4-32E, and
-- Svalbard roughly 74-81N, 10-35E. A parsed port outside both boxes means
-- something is wrong with the source or the conversion.

select locode, port_name, raw_coordinates, latitude, longitude
from {{ ref('stg_ports') }}
where not (latitude between 57 and 72 and longitude between 4 and 32)
  and not (latitude between 74 and 81 and longitude between 10 and 35)
