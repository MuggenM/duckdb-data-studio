WITH raw_drivers AS (
    SELECT * FROM {{ source('formula1', 'drivers') }}
)
SELECT
    TRY_CAST(driverId AS INT) AS driver_id,
    NULLIF(driverRef, '\N') AS driver_ref,
    TRY_CAST(NULLIF(CAST(number AS VARCHAR), '\N') AS INT) AS permanent_number,
    NULLIF(code, '\N') AS driver_code,
    NULLIF(forename, '\N') AS first_name,
    NULLIF(surname, '\N') AS last_name,
    COALESCE(NULLIF(forename, '\N'), '') || ' ' || COALESCE(NULLIF(surname, '\N'), '') AS full_name,
    TRY_CAST(NULLIF(CAST(dob AS VARCHAR), '\N') AS DATE) AS date_of_birth,
    NULLIF(nationality, '\N') AS nationality,
    NULLIF(url, '\N') AS wikipedia_url
FROM raw_drivers
