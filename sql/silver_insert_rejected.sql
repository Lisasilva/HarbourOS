-- Rows that failed a rule, kept as-delivered with the reason attached.

INSERT INTO ais_messages_quarantine
SELECT *
FROM new_bronze_batch
WHERE rejection_reason IS NOT NULL;
