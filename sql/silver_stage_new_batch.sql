-- Stage the Bronze rows that have not been processed yet, and decide the fate
-- of each one. This is the ONLY place the accept/reject rules are written, so
-- Silver and Quarantine cannot drift apart: every staged row has either a NULL
-- rejection_reason (goes to Silver) or a non-NULL one (goes to Quarantine).
--
-- 'missing_' and 'invalid_' are deliberately separate reasons. A field the
-- source never sent is a different failure from a field the source sent wrong:
-- one points at the feed, the other at the vessel's equipment.

CREATE OR REPLACE TEMP TABLE new_bronze_batch AS
WITH already_processed AS (
    -- Every Bronze row ends up in Silver or Quarantine, so the newest row in
    -- either table marks exactly how far the pipeline has got.
    SELECT COALESCE(max(received_at), TIMESTAMP '1970-01-01 00:00:00') AS up_to
    FROM (
        SELECT received_at FROM ais_messages_silver
        UNION ALL
        SELECT received_at FROM ais_messages_quarantine
    )
),
new_rows AS (
    SELECT b.*
    FROM ais_messages_bronze AS b, already_processed AS p
    WHERE b.received_at > p.up_to
),
ranked AS (
    SELECT
        n.*,
        ROW_NUMBER() OVER (
            PARTITION BY mmsi, msgtime          -- same ship, same instant
            ORDER BY
                received_at,                    -- earliest copy wins
                concat_ws('|', latitude, longitude, speedOverGround,
                          courseOverGround, navigationalStatus, name)
                                                -- deterministic tie-break
        ) AS row_num
    FROM new_rows AS n
)
SELECT
    r.* EXCLUDE (row_num),
    CASE
        WHEN r.row_num != 1 THEN 'duplicate'
        WHEN EXISTS (
            SELECT 1 FROM ais_messages_silver AS s
            WHERE s.mmsi = r.mmsi AND s.message_time = r.msgtime
        ) THEN 'duplicate'                      -- duplicate of an earlier batch
        WHEN r.mmsi IS NULL THEN 'missing_mmsi'
        WHEN r.mmsi NOT BETWEEN 100000000 AND 999999999 THEN 'invalid_mmsi_range'
        WHEN r.latitude IS NULL THEN 'missing_latitude'
        WHEN r.latitude NOT BETWEEN -90 AND 90 THEN 'invalid_latitude'
        WHEN r.longitude IS NULL THEN 'missing_longitude'
        WHEN r.longitude NOT BETWEEN -180 AND 180 THEN 'invalid_longitude'
        WHEN r.speedOverGround IS NULL THEN 'missing_speed'
        WHEN r.speedOverGround NOT BETWEEN 0 AND 102.2 THEN 'invalid_speed'
        WHEN r.msgtime IS NULL THEN 'missing_msgtime'
        WHEN r.msgtime > CURRENT_TIMESTAMP THEN 'future_timestamp'
        ELSE NULL
    END AS rejection_reason
FROM ranked AS r;
