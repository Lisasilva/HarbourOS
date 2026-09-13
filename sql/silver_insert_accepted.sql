-- Rows that passed every rule, renamed to snake_case.

INSERT INTO ais_messages_silver
SELECT
    mmsi,
    name,
    latitude,
    longitude,
    speedOverGround,
    courseOverGround,
    trueHeading,
    rateOfTurn,
    shipType,
    navigationalStatus,
    stream,
    msgtime,
    received_at
FROM new_bronze_batch
WHERE rejection_reason IS NULL;
