-- The sharpest guard on the riskiest line in the project.
--
-- If "6023N" were misread as 60.23 degrees instead of 60 degrees 23 minutes,
-- the decimal part would not land on a whole arc-minute. Every correctly
-- parsed position must sit exactly on a minute boundary.

select locode, raw_coordinates, latitude, longitude
from {{ ref('stg_ports') }}
where abs(latitude * 60 - round(latitude * 60)) > 0.000001
   or abs(longitude * 60 - round(longitude * 60)) > 0.000001
