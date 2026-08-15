WITH stg_constructors AS (
    SELECT * FROM {{ ref('stg_f1_constructors') }}
)
SELECT
    {{ dbt_utils.generate_surrogate_key(['constructor_id']) }} AS constructor_sk,
    constructor_id,
    constructor_ref,
    constructor_name,
    nationality,
    wikipedia_url
FROM stg_constructors
