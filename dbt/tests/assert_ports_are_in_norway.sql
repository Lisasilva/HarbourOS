-- Geographic sanity: mainland Norway spans roughly 57-72N, 4-32E. A parsed
-- port outside that box means something is wrong with the source or the
-- conversion.

select locode, port_name, raw_coordinates, latitude, longitude
from {{ ref('stg_ports') }}
where latitude not between 57 and 72
   or longitude not between 4 and 32
