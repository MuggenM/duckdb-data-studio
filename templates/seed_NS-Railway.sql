-- Database: ns_railway
-- Ensure the network extensions are ready
INSTALL httpfs;
LOAD httpfs;

-- ==========================================
-- 1. VIEW: STATIONS
-- ==========================================
CREATE OR REPLACE TABLE rd_stations AS 
SELECT 
    code AS station_code,
    name_short,
    name_medium,
    name_long,
    country AS country_code,
    type AS station_type,
    geo_lat AS latitude,
    geo_lng AS longitude,
    uic AS uic_code
FROM 'https://opendata.rijdendetreinen.nl/public/stations/stations-2023-09.csv';

-- ==========================================
-- 2. VIEW: TARIFF DISTANCES
-- ==========================================
CREATE OR REPLACE TABLE rd_station_distances AS
SELECT 
    from_code AS from_station_code,
    to_code AS to_station_code,
    distance AS distance_km
FROM 'https://opendata.rijdendetreinen.nl/public/distances/tariff-distances.csv';

-- ==========================================
-- 3. VIEW: TRAIN ARCHIVE (May 2026 Example)
-- ==========================================
-- Creates a physical table and populates it with all historical and current files
CREATE TABLE IF NOT EXISTS nsm_train_archive AS 
SELECT 
    "Service:RDT-ID"::UBIGINT AS journey_id,
    "Service:Train number"::UINTEGER AS train_number,
    "Service:Date"::DATE AS run_date,
    "Service:Type" AS train_type,
    "Service:Company" AS operator,
    "Service:Completely cancelled"::BOOLEAN AS is_cancelled,
    "Service:Maximum delay"::INT AS max_delay_minutes
FROM read_csv(
    list_concat(
        -- Loop 1: Historical yearly files
        [ 'https://opendata.rijdendetreinen.nl/public/services/services-' 
           || y::VARCHAR || '.csv.gz' 
          FOR y IN range(2019, extract('year' FROM current_date)::INT) ],
          
        -- Loop 2: Current year's months up to right now
        [ 'https://opendata.rijdendetreinen.nl/public/services/services-' 
           || extract('year' FROM current_date)::VARCHAR || '-'
           || lpad(m::VARCHAR, 2, '0') || '.csv.gz' 
          FOR m IN range(1, extract('month' FROM current_date)::INT ) ]
    )
);

-- ==========================================
-- 4. VIEW: TRAIN DISRUPTIONS
-- ==========================================
CREATE OR REPLACE TABLE rd_disruptions AS 
SELECT 
    id::UBIGINT AS disruption_id,
    url AS rd_url_slug,
    start_time::TIMESTAMP WITH TIME ZONE AS start_time,
    end_time::TIMESTAMP WITH TIME ZONE AS end_time,
    cause_nl AS cause_dutch,
    cause_en AS cause_english,
    type AS disruption_type
FROM 'https://opendata.rijdendetreinen.nl/public/disruptions/disruptions.csv';
