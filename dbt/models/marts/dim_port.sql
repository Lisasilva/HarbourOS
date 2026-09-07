-- Port dimension: Norwegian seaports from UN/LOCODE, the UN's official code
-- list for trade and transport locations. LOCODE (e.g. NOBGO for Bergen) is a
-- real-world business key, so no surrogate key is needed here.

select
    locode as port_locode,
    port_name,
    subdivision,
    status as locode_status,
    latitude,
    longitude
from {{ ref('stg_ports') }}
