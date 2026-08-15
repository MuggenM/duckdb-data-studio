WITH raw_constructors AS (
    SELECT * FROM {{ source('formula1', 'constructors') }}
)
SELECT
    TRY_CAST(constructorId AS INT) AS constructor_id,
    NULLIF(constructorRef, '\N') AS constructor_ref,
    NULLIF(name, '\N') AS constructor_name,
    NULLIF(nationality, '\N') AS nationality,
    NULLIF(url, '\N') AS wikipedia_url
FROM raw_constructors
