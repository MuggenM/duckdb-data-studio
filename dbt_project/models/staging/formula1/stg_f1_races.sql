WITH raw_races AS (
    SELECT * FROM {{ source('formula1', 'races') }}
)
SELECT
    TRY_CAST(raceId AS INT) AS race_id,
    TRY_CAST(year AS INT) AS race_year,
    TRY_CAST(round AS INT) AS race_round,
    TRY_CAST(circuitId AS INT) AS circuit_id,
    NULLIF(name, '\N') AS grand_prix_name,
    TRY_CAST(NULLIF(CAST(date AS VARCHAR), '\N') AS DATE) AS race_date,
    TRY_CAST(NULLIF(CAST(time AS VARCHAR), '\N') AS TIME) AS race_start_time,
    NULLIF(url, '\N') AS wikipedia_url
FROM raw_races
