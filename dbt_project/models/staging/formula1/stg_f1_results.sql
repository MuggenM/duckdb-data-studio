WITH raw_results AS (
    SELECT * FROM {{ source('formula1', 'results') }}
)
SELECT
    TRY_CAST(resultId AS INT) AS result_id,
    TRY_CAST(raceId AS INT) AS race_id,
    TRY_CAST(driverId AS INT) AS driver_id,
    TRY_CAST(constructorId AS INT) AS constructor_id,
    TRY_CAST(NULLIF(CAST(number AS VARCHAR), '\N') AS INT) AS car_number,
    TRY_CAST(grid AS INT) AS starting_grid_position,
    TRY_CAST(NULLIF(CAST(position AS VARCHAR), '\N') AS INT) AS finish_position,
    NULLIF(positionText, '\N') AS finish_position_text,
    TRY_CAST(positionOrder AS INT) AS finish_position_order,
    TRY_CAST(NULLIF(CAST(points AS VARCHAR), '\N') AS DOUBLE) AS points_awarded,
    TRY_CAST(laps AS INT) AS laps_completed,
    NULLIF(time, '\N') AS race_time_str,
    TRY_CAST(NULLIF(CAST(milliseconds AS VARCHAR), '\N') AS BIGINT) AS race_time_milliseconds,
    TRY_CAST(NULLIF(CAST(fastestLap AS VARCHAR), '\N') AS INT) AS fastest_lap_number,
    TRY_CAST(NULLIF(CAST(rank AS VARCHAR), '\N') AS INT) AS fastest_lap_rank,
    NULLIF(fastestLapTime, '\N') AS fastest_lap_time_str,
    TRY_CAST(NULLIF(CAST(fastestLapSpeed AS VARCHAR), '\N') AS DOUBLE) AS fastest_lap_speed_kph,
    TRY_CAST(statusId AS INT) AS status_id
FROM raw_results
