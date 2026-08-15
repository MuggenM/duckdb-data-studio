WITH stg_status AS (
    SELECT * FROM {{ ref('stg_f1_status') }}
)
SELECT
    {{ dbt_utils.generate_surrogate_key(['status_id']) }} AS status_sk,
    status_id,
    finishing_status,
    CASE 
        WHEN finishing_status = 'Finished' THEN 'Completed'
        WHEN finishing_status LIKE '+%' THEN 'Lapped'
        WHEN finishing_status IN ('Accident', 'Collision', 'Spun off') THEN 'Incident'
        WHEN finishing_status IN ('Engine', 'Gearbox', 'Transmission', 'Clutch', 'Hydraulics', 'Electrical') THEN 'Mechanical Failure'
        WHEN finishing_status IN ('Disqualified', 'Excluded') THEN 'Disqualified'
        ELSE 'Other Retirement'
    END AS status_category
FROM stg_status
