-- Database: nyc_taxi
-- Ensure the network and json extensions are ready
INSTALL httpfs;
LOAD httpfs;
INSTALL json;
LOAD json;

-- ==========================================
-- 1. TABLE: YELLOW TAXI TRIP DATA (Full Year 2023)
-- ==========================================
CREATE OR REPLACE TABLE yellow_taxi_trips AS
SELECT 
    VendorID AS vendor_id,
    tpep_pickup_datetime::TIMESTAMP AS pickup_time,
    tpep_dropoff_datetime::TIMESTAMP AS dropoff_time,
    passenger_count::INT AS passenger_count,
    trip_distance::DOUBLE AS trip_distance,
    RatecodeID::INT AS rate_code_id,
    store_and_fwd_flag AS store_and_fwd,
    PULocationID::INT AS pickup_location_id,
    DOLocationID::INT AS dropoff_location_id,
    payment_type::INT AS payment_type_id,
    fare_amount::DOUBLE AS fare_amount,
    extra::DOUBLE AS extra_charges,
    mta_tax::DOUBLE AS mta_tax,
    tip_amount::DOUBLE AS tip_amount,
    tolls_amount::DOUBLE AS tolls_amount,
    improvement_surcharge::DOUBLE AS improvement_surcharge,
    total_amount::DOUBLE AS total_amount,
    congestion_surcharge::DOUBLE AS congestion_surcharge
FROM read_parquet(
    [ 'https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2023-' 
       || lpad(m::VARCHAR, 2, '0') || '.parquet' 
      FOR m IN range(1, 13) ]
);

-- ==========================================
-- 2. TABLE: TAXI ZONE LOOKUP
-- ==========================================
CREATE OR REPLACE TABLE taxi_zones AS
SELECT 
    LocationID::INT AS zone_id,
    Borough AS borough,
    Zone AS zone_name,
    service_zone
FROM 'https://d37ci6vzurychx.cloudfront.net/misc/taxi+_zone_lookup.csv';

-- ==========================================
-- 3. TABLE: NYC WEATHER (2020 - 2025)
-- ==========================================
CREATE OR REPLACE TABLE nyc_weather AS
SELECT 
    STATION AS weatherstation_id,
    DATE::DATE AS weather_date,
    TMAX::DOUBLE / 10.0 AS max_temp_c,
    TMIN::DOUBLE / 10.0 AS min_temp_c,
    PRCP::DOUBLE / 10.0 AS precipitation_mm,
    SNOW::DOUBLE AS snowfall_mm
FROM read_csv(
    'https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries&stations=USW00094728,USW00094789,USW00094724&startDate=2020-01-01&endDate=2025-12-31&format=csv',
    header=true,
    auto_detect=true
);

-- ==========================================
-- 4. TABLE: USA WEATHER STATIONS
-- ==========================================
CREATE OR REPLACE TABLE weather_stations AS
SELECT 
    f.id AS id,
    f.properties->>'sname' AS station_name,
    f.geometry.coordinates[1] AS longitude,
    f.geometry.coordinates[2] AS latitude,
    (f.properties->>'elevation')::DOUBLE AS elevation_m,
    f.properties->>'archive_begin' AS archive_begins,
    f.properties->>'archive_end' AS archive_ends,
    f.properties->>'network' AS iem_network,
    f.properties::VARCHAR AS attributes
FROM (
    SELECT unnest(features) AS f 
    FROM read_json_auto('https://mesonet.agron.iastate.edu/geojson/network/NCEI91.geojson')
);


-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Hourly Trip Density and Average Fare
-- Description: Analyzes yellow taxi trips aggregated hourly to find peak demand windows and average fares.
-- Category: Analytical
USE nyc_taxi;

SELECT 
    date_trunc('hour', pickup_time) AS pickup_hour,
    COUNT(*) AS total_trips,
    ROUND(AVG(trip_distance), 2) AS avg_distance_miles,
    ROUND(AVG(fare_amount), 2) AS avg_fare_usd,
    ROUND(AVG(tip_amount), 2) AS avg_tip_usd
FROM yellow_taxi_trips
WHERE pickup_time BETWEEN '2023-01-01 00:00:00' AND '2023-01-31 23:59:59'
GROUP BY ALL
ORDER BY total_trips DESC
LIMIT 20;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Most Profitable Pickup-Dropoff Zones
-- Description: Groups trips by pickup and dropoff zones to rank the highest average fare journeys.
-- Category: Analytical
USE nyc_taxi;

SELECT 
    pz_start.zone_name AS pickup_zone,
    pz_end.zone_name AS dropoff_zone,
    COUNT(*) AS trip_count,
    ROUND(AVG(t.fare_amount), 2) AS avg_fare_usd,
    ROUND(AVG(t.tip_amount), 2) AS avg_tip_usd,
    ROUND(AVG(t.total_amount), 2) AS avg_total_usd
FROM yellow_taxi_trips t
JOIN taxi_zones pz_start ON t.pickup_location_id = pz_start.zone_id
JOIN taxi_zones pz_end ON t.dropoff_location_id = pz_end.zone_id
GROUP BY ALL
HAVING trip_count >= 100
ORDER BY avg_total_usd DESC
LIMIT 25;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Weather Impact on NYC Taxi Demand
-- Description: Joins daily taxi counts and average tip percentage with NYC weather data to analyze how rainy or cold days affect taxi usage.
-- Category: Analytical
USE nyc_taxi;

SELECT 
    w.weather_date,
    s.station_name,
    COUNT(t.pickup_time) AS total_trips,
    ROUND(AVG(t.trip_distance), 2) AS avg_distance_miles,
    ROUND(AVG(t.fare_amount), 2) AS avg_fare_usd,
    ROUND(AVG(t.tip_amount), 2) AS avg_tip_usd
FROM nyc_weather w
JOIN weather_stations s ON w.weatherstation_id = s.id
LEFT JOIN yellow_taxi_trips t ON w.weather_date = t.pickup_time::DATE
GROUP BY ALL
ORDER BY w.weather_date ASC;
-- === SNIPPET END ===
