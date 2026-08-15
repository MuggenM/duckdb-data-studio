WITH stg_races AS (
    SELECT * FROM {{ ref('stg_f1_races') }}
),
stg_circuits AS (
    SELECT * FROM {{ ref('stg_f1_circuits') }}
)
SELECT
    {{ dbt_utils.generate_surrogate_key(['r.race_id']) }} AS race_sk,
    r.race_id,
    r.race_year,
    r.race_round,
    r.grand_prix_name,
    r.race_date,
    r.race_start_time,
    c.circuit_name,
    c.city AS circuit_city,
    c.country AS circuit_country,
    r.wikipedia_url
FROM stg_races r
LEFT JOIN stg_circuits c ON r.circuit_id = c.circuit_id
