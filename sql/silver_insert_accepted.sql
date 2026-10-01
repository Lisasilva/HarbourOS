-- Rows that passed every rule, renamed to snake_case. BY NAME, because
-- columns added later sit at the end of the existing table, not in the order
-- written here.

INSERT INTO ais_messages_silver BY NAME
SELECT
    mmsi,
    name,
    latitude,
    longitude,
    speedOverGround     AS speed_over_ground,
    courseOverGround    AS course_over_ground,
    trueHeading         AS true_heading,
    rateOfTurn          AS rate_of_turn,
    shipType            AS ship_type,
    navigationalStatus  AS navigational_status,
    stream,
    msgtime             AS message_time,
    received_at,
    destination,
    eta,
    imoNumber           AS imo_number,
    callSign            AS call_sign
FROM new_bronze_batch
WHERE rejection_reason IS NULL;
