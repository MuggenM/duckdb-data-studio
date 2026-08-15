WITH raw_status AS (
    SELECT * FROM {{ source('formula1', 'status') }}
)
SELECT
    TRY_CAST(statusId AS INT) AS status_id,
    NULLIF(status, '\N') AS finishing_status
FROM raw_status
