WITH raw_circuits AS (
    SELECT * FROM {{ source('formula1', 'circuits') }}
)
SELECT
    TRY_CAST(circuitId AS INT) AS circuit_id,
    NULLIF(circuitRef, '\N') AS circuit_ref,
    NULLIF(name, '\N') AS circuit_name,
    NULLIF(location, '\N') AS city,
    NULLIF(country, '\N') AS country,
    TRY_CAST(lat AS DOUBLE) AS latitude,
    TRY_CAST(lng AS DOUBLE) AS longitude,
    TRY_CAST(NULLIF(CAST(alt AS VARCHAR), '\N') AS DOUBLE) AS altitude_meters,
    NULLIF(url, '\N') AS wikipedia_url
FROM raw_circuits
