WITH stg_circuits AS (
    SELECT * FROM {{ ref('stg_f1_circuits') }}
)
SELECT
    {{ dbt_utils.generate_surrogate_key(['circuit_id']) }} AS circuit_sk,
    circuit_id,
    circuit_ref,
    circuit_name,
    city,
    country,
    latitude,
    longitude,
    altitude_meters,
    wikipedia_url
FROM stg_circuits
