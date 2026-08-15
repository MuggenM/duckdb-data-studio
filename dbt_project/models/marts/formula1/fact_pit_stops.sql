WITH raw_pit_stops AS (
    SELECT * FROM {{ source('formula1', 'pit_stops') }}
),
dim_races AS (
    SELECT race_sk, race_id FROM {{ ref('dim_races') }}
),
dim_drivers AS (
    SELECT driver_sk, driver_id FROM {{ ref('dim_drivers') }}
)
SELECT
    {{ dbt_utils.generate_surrogate_key(['p.raceId', 'p.driverId', 'p.stop']) }} AS pit_stop_sk,
    r.race_sk AS race_fk,
    d.driver_sk AS driver_fk,
    TRY_CAST(p.stop AS INT) AS pit_stop_number,
    TRY_CAST(p.lap AS INT) AS pit_stop_lap,
    NULLIF(CAST(p.time AS VARCHAR), '\N') AS pit_stop_time,
    TRY_CAST(NULLIF(CAST(p.duration AS VARCHAR), '\N') AS DOUBLE) AS duration_seconds,
    TRY_CAST(NULLIF(CAST(p.milliseconds AS VARCHAR), '\N') AS BIGINT) AS duration_milliseconds
FROM raw_pit_stops p
LEFT JOIN dim_races r ON TRY_CAST(p.raceId AS INT) = r.race_id
LEFT JOIN dim_drivers d ON TRY_CAST(p.driverId AS INT) = d.driver_id
