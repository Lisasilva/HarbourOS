-- Rows that failed a rule, kept as-delivered with the reason attached. BY
-- NAME, because columns added later sit at the end of the existing table.

INSERT INTO ais_messages_quarantine BY NAME
SELECT *
FROM new_bronze_batch
WHERE rejection_reason IS NOT NULL;
