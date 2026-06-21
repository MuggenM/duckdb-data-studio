-- Database: noaa_weather
-- Ensure the network extensions are ready
INSTALL httpfs;
LOAD httpfs;

-- ==========================================
-- 1. TABLE: NOAA SEATTLE DAILY WEATHER TELEMETRY
-- ==========================================
CREATE OR REPLACE TABLE daily_weather AS
SELECT 
    date::DATE AS observation_date,
    precipitation::DOUBLE AS precipitation_mm,
    temp_max::DOUBLE AS max_temp_c,
    temp_min::DOUBLE AS min_temp_c,
    wind::DOUBLE AS wind_speed_ms,
    weather AS weather_condition
FROM 'https://raw.githubusercontent.com/EdwinOsayuki/Seattle-Weather-Prediction/refs/heads/main/seattle-weather.csv';

-- ==========================================
-- 2. TABLE: WEATHER CONDITIONS LOOKUP
-- ==========================================
CREATE OR REPLACE TABLE weather_conditions_lookup AS
SELECT 
    weather_condition,
    description,
    icon_code,
    severity_index::INT AS severity_index,
    recommended_activity
FROM (
    VALUES 
    ('drizzle', 'Light rain falling in very small drops', 'cloud_queue', 1, 'Walking / Jogging'),
    ('rain', 'Precipitation in the form of liquid water drops', 'umbrella', 2, 'Indoor museum visits / Cinemas'),
    ('sun', 'Clear skies and direct sunlight', 'wb_sunny', 0, 'Outdoor sports / Hiking'),
    ('snow', 'Precipitation in the form of crystalline water ice', 'ac_unit', 3, 'Skiing / Winter photography'),
    ('fog', 'Thick cloud of tiny water droplets suspended near ground', 'cloudy', 1, 'Cozy indoor cafes')
) AS t(weather_condition, description, icon_code, severity_index, recommended_activity);

-- ==========================================
-- 3. TABLE: WEATHER ALERTS (Generated from extreme days)
-- ==========================================
CREATE OR REPLACE TABLE weather_alerts AS
SELECT 
    uuid() AS alert_id,
    date::DATE AS alert_date,
    CASE 
        WHEN wind::DOUBLE > 7.0 THEN 'High Wind Warning'
        WHEN precipitation::DOUBLE > 25.0 THEN 'Heavy Rainfall Warning'
        WHEN temp_min::DOUBLE < -2.0 THEN 'Freeze Warning'
        ELSE 'Weather Advisory'
    END AS alert_type,
    CASE 
        WHEN wind::DOUBLE > 7.0 OR precipitation::DOUBLE > 25.0 OR temp_min::DOUBLE < -2.0 THEN 'High'
        ELSE 'Medium'
    END AS severity,
    CASE 
        WHEN wind::DOUBLE > 7.0 THEN 'Wind speeds reached ' || ROUND(wind::DOUBLE, 1)::VARCHAR || ' m/s. Secure loose outdoor items.'
        WHEN precipitation::DOUBLE > 25.0 THEN 'Heavy precipitation of ' || ROUND(precipitation::DOUBLE, 1)::VARCHAR || ' mm. Expect localized urban flooding.'
        WHEN temp_min::DOUBLE < -2.0 THEN 'Freezing temperatures expected at ' || ROUND(temp_min::DOUBLE, 1)::VARCHAR || ' °C. Protect exposed piping.'
        ELSE 'Unusual weather conditions detected.'
    END AS alert_description
FROM 'https://raw.githubusercontent.com/EdwinOsayuki/Seattle-Weather-Prediction/refs/heads/main/seattle-weather.csv'
WHERE wind::DOUBLE > 7.0 OR precipitation::DOUBLE > 25.0 OR temp_min::DOUBLE < -2.0;

-- ==========================================
-- 4. TABLE: SEATTLE ANNUAL OUTDOOR EVENTS
-- ==========================================
CREATE OR REPLACE TABLE seattle_events AS
SELECT 
    event_date::DATE AS event_date,
    event_name,
    venue,
    is_outdoor::BOOLEAN AS is_outdoor
FROM (
    VALUES 
    ('2012-01-01', 'New Years Day Resolution Run', 'Seward Park', true),
    ('2012-05-28', 'Northwest Folklife Festival', 'Seattle Center', true),
    ('2012-07-04', 'Seafair Summer Fourth', 'Gas Works Park', true),
    ('2012-09-02', 'Bumbershoot Music Festival', 'Seattle Center', true),
    ('2012-11-25', 'Seattle Marathon', 'Downtown Seattle', true),
    ('2013-01-01', 'New Years Day Resolution Run', 'Seward Park', true),
    ('2013-05-27', 'Northwest Folklife Festival', 'Seattle Center', true),
    ('2013-07-04', 'Seafair Summer Fourth', 'Gas Works Park', true),
    ('2013-09-01', 'Bumbershoot Music Festival', 'Seattle Center', true),
    ('2013-11-24', 'Seattle Marathon', 'Downtown Seattle', true),
    ('2014-01-01', 'New Years Day Resolution Run', 'Seward Park', true),
    ('2014-05-26', 'Northwest Folklife Festival', 'Seattle Center', true),
    ('2014-07-04', 'Seafair Summer Fourth', 'Gas Works Park', true),
    ('2014-08-31', 'Bumbershoot Music Festival', 'Seattle Center', true),
    ('2014-11-30', 'Seattle Marathon', 'Downtown Seattle', true),
    ('2015-01-01', 'New Years Day Resolution Run', 'Seward Park', true),
    ('2015-05-25', 'Northwest Folklife Festival', 'Seattle Center', true),
    ('2015-07-04', 'Seafair Summer Fourth', 'Gas Works Park', true),
    ('2015-09-06', 'Bumbershoot Music Festival', 'Seattle Center', true),
    ('2015-11-29', 'Seattle Marathon', 'Downtown Seattle', true)
) AS t(event_date, event_name, venue, is_outdoor);


-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Monthly Temperature and Precipitation Trends
-- Description: Groups observations by year and month to show average temperature ranges and total rainfall.
-- Category: Analytical
USE noaa_weather;

SELECT 
    date_trunc('month', observation_date) AS observation_month,
    ROUND(AVG(max_temp_c), 2) AS avg_max_temp_c,
    ROUND(AVG(min_temp_c), 2) AS avg_min_temp_c,
    ROUND(SUM(precipitation_mm), 2) AS total_precipitation_mm,
    ROUND(AVG(wind_speed_ms), 2) AS avg_wind_speed_ms
FROM daily_weather
GROUP BY ALL
ORDER BY observation_month DESC;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Weather Impact on Seattle Outdoor Events
-- Description: Joins Seattle outdoor events with daily weather and conditions lookup to inspect how conditions affected historical event days.
-- Category: Analytical
USE noaa_weather;

SELECT 
    e.event_date,
    e.event_name,
    e.venue,
    w.weather_condition,
    l.description AS condition_details,
    l.recommended_activity,
    w.precipitation_mm AS rainfall_mm,
    w.max_temp_c AS max_temp
FROM seattle_events e
JOIN daily_weather w ON e.event_date = w.observation_date
JOIN weather_conditions_lookup l ON w.weather_condition = l.weather_condition
ORDER BY e.event_date DESC;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Severe Weather Alerts and Condition Details
-- Description: Joins weather alerts with daily weather stats and weather condition lookups to map severe incidents.
-- Category: Analytical
USE noaa_weather;

SELECT 
    a.alert_date,
    a.alert_type,
    a.severity,
    a.alert_description,
    w.max_temp_c AS recorded_max_temp,
    w.min_temp_c AS recorded_min_temp,
    w.wind_speed_ms AS recorded_wind_speed,
    l.description AS overall_condition
FROM weather_alerts a
JOIN daily_weather w ON a.alert_date = w.observation_date
JOIN weather_conditions_lookup l ON w.weather_condition = l.weather_condition
ORDER BY a.alert_date DESC
LIMIT 30;
-- === SNIPPET END ===
