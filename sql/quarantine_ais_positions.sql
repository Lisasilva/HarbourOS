-- Quarantine: Bronze rows that FAILED Silver validation, tagged with why.
-- This is a data-quality audit trail so that nothing is silently dropped.

CREATE OR REPLACE TABLE ais_messages_quarantine AS
WITH deduplicated AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY mmsi, msgtime
            ORDER BY received_at DESC
        ) AS row_num
    FROM ais_messages_bronze
),
flagged AS (
    SELECT
        *,
        CASE
            WHEN row_num != 1 THEN 'duplicate'
            WHEN mmsi IS NULL THEN 'null_mmsi'
            WHEN mmsi NOT BETWEEN 100000000 AND 999999999 THEN 'invalid_mmsi_range'
            WHEN latitude IS NULL OR latitude NOT BETWEEN -90 AND 90 THEN 'invalid_latitude'
            WHEN longitude IS NULL OR longitude NOT BETWEEN -180 AND 180 THEN 'invalid_longitude'
            WHEN speedOverGround IS NULL OR speedOverGround NOT BETWEEN 0 AND 102.2 THEN 'invalid_speed'
            WHEN msgtime IS NULL THEN 'null_msgtime'
            WHEN msgtime > CURRENT_TIMESTAMP THEN 'future_timestamp'
            ELSE NULL
        END AS rejection_reason
    FROM deduplicated
)
SELECT * EXCLUDE (row_num) -- DuckDB-specific keyword  -it give every column except row_num col from the bronze table
FROM flagged
WHERE rejection_reason IS NOT NULL;
