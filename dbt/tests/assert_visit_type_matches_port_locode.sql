-- visit_type and port_locode must agree: a port_call always has a port, and
-- an at_sea stop never does. If these disagree, the visit_type logic in
-- fact_port_call.sql has drifted from the port_locode logic next to it.

select port_call_key, visit_type, port_locode
from {{ ref('fact_port_call') }}
where (visit_type = 'port_call') != (port_locode is not null)
