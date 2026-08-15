WITH stg_results AS (
    SELECT * FROM {{ ref('stg_f1_results') }}
),
dim_races AS (
    SELECT race_sk, race_id FROM {{ ref('dim_races') }}
),
dim_drivers AS (
    SELECT driver_sk, driver_id FROM {{ ref('dim_drivers') }}
),
dim_constructors AS (
    SELECT constructor_sk, constructor_id FROM {{ ref('dim_constructors') }}
),
dim_status AS (
    SELECT status_sk, status_id FROM {{ ref('dim_status') }}
)
SELECT
    -- Surrogate Primary Key
    {{ dbt_utils.generate_surrogate_key(['res.result_id']) }} AS race_result_sk,
    
    -- Degenerate Natural Key
    res.result_id,

    -- Foreign Keys to Dimension Tables
    r.race_sk AS race_fk,
    d.driver_sk AS driver_fk,
    c.constructor_sk AS constructor_fk,
    s.status_sk AS status_fk,
    
    -- Degenerate Dimensions & Performance Attributes
    res.car_number,
    res.starting_grid_position,
    res.finish_position,
    res.finish_position_text,
    res.finish_position_order,
    
    -- Analytical Flag Measures
    CASE WHEN res.finish_position = 1 THEN TRUE ELSE FALSE END AS is_winner,
    CASE WHEN res.finish_position IN (1, 2, 3) THEN TRUE ELSE FALSE END AS is_podium,
    CASE WHEN res.points_awarded > 0 THEN TRUE ELSE FALSE END AS is_points_finish,
    CASE WHEN res.starting_grid_position > 0 AND res.finish_position IS NOT NULL THEN (res.starting_grid_position - res.finish_position) ELSE 0 END AS positions_gained,

    -- Numeric Measures & Metrics
    res.points_awarded,
    res.laps_completed,
    res.race_time_milliseconds,
    ROUND(res.race_time_milliseconds / 1000.0, 3) AS race_time_seconds,
    ROUND(res.race_time_milliseconds / 60000.0, 2) AS race_time_minutes,
    
    -- Fastest Lap Performance Metrics
    res.fastest_lap_number,
    res.fastest_lap_rank,
    res.fastest_lap_time_str,
    res.fastest_lap_speed_kph

FROM stg_results res
LEFT JOIN dim_races r ON res.race_id = r.race_id
LEFT JOIN dim_drivers d ON res.driver_id = d.driver_id
LEFT JOIN dim_constructors c ON res.constructor_id = c.constructor_id
LEFT JOIN dim_status s ON res.status_id = s.status_id
