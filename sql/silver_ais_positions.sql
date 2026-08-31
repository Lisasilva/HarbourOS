-- Silver layer: deduplicated, validated, standardized AIS positions.
-- Reads from Bronze (raw, as-delivered by the API), keeps only clean rows.

CREATE OR REPLACE TABLE ais_messages_silver AS
WITH deduplicated AS (
    SELECT
        *,
        ROW_NUMBER() OVER (             -- window function : Gives each row a number once the partition and order by clause are executed
            PARTITION BY mmsi, msgtime  -- Groups rows that have the same ship (MMSI) and same message time.
            ORDER BY received_at DESC   -- Within each group, sorts the rows from newest received to oldest received.
        ) AS row_num
    FROM ais_messages_bronze
)
SELECT
    mmsi,
    name,
    latitude,
    longitude,
    speedOverGround     AS speed_over_ground,  -- standardizing to snake_case
    courseOverGround    AS course_over_ground,
    trueHeading         AS true_heading,
    rateOfTurn          AS rate_of_turn,
    shipType            AS ship_type,
    navigationalStatus  AS navigational_status,
    stream,
    msgtime             AS message_time,
    received_at
FROM deduplicated
WHERE row_num = 1   -- the latest copy of each duplicate message
  AND mmsi IS NOT NULL
  AND mmsi BETWEEN 100000000 AND 999999999
  AND latitude BETWEEN -90 AND 90
  AND longitude BETWEEN -180 AND 180
  AND speedOverGround BETWEEN 0 AND 102.2
  AND msgtime IS NOT NULL
  AND msgtime <= CURRENT_TIMESTAMP;
