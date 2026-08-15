WITH stg_drivers AS (
    SELECT * FROM {{ ref('stg_f1_drivers') }}
)
SELECT
    {{ dbt_utils.generate_surrogate_key(['driver_id']) }} AS driver_sk,
    driver_id,
    driver_ref,
    driver_code,
    permanent_number,
    first_name,
    last_name,
    full_name,
    date_of_birth,
    nationality,
    DATEDIFF('year', date_of_birth, CURRENT_DATE) AS current_age,
    wikipedia_url
FROM stg_drivers
