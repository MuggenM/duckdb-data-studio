-- Database: open_aq
-- Ensure the network extensions are ready
INSTALL httpfs;
LOAD httpfs;

-- ==========================================
-- 1. VIEW: OWID GLOBAL EMISSIONS & CLIMATE DATA
-- ==========================================
CREATE OR REPLACE TABLE global_co2_emissions AS
SELECT 
    country,
    year::INT AS year,
    iso_code,
    population::UBIGINT AS population,
    gdp::DOUBLE AS gdp,
    co2::DOUBLE AS co2_emissions,
    co2_per_capita::DOUBLE AS co2_per_capita,
    coal_co2::DOUBLE AS coal_co2,
    gas_co2::DOUBLE AS gas_co2,
    oil_co2::DOUBLE AS oil_co2
FROM 'https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv';


-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Top CO2 Emitting Nations (Recent Year)
-- Description: Groups emissions by country for the most recent complete reporting year.
USE open_aq;

SELECT 
    country,
    iso_code,
    population,
    ROUND(co2_emissions, 2) AS total_co2_million_tons,
    ROUND(co2_per_capita, 2) AS co2_tons_per_person
FROM global_co2_emissions
WHERE year = 2021 AND iso_code IS NOT NULL AND iso_code NOT IN ('OWID_WRL', 'OWID_KOS')
ORDER BY total_co2_million_tons DESC
LIMIT 15;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Correlation between GDP and CO2 per Capita
-- Description: Measures relationship between a country's economic size (GDP per Capita) and its carbon footprint.
USE open_aq;

SELECT 
    country,
    year,
    ROUND(gdp / population, 2) AS gdp_per_capita_usd,
    ROUND(co2_per_capita, 2) AS co2_per_capita_tons,
    ROUND(co2_emissions, 2) AS total_co2_million_tons
FROM global_co2_emissions
WHERE year = 2018 AND gdp IS NOT NULL AND population IS NOT NULL
ORDER BY gdp_per_capita_usd DESC
LIMIT 30;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Fossil Fuel Emissions Breakdown (Decadal Trends)
-- Description: Analyzes decadal changes in emissions contribution from Coal, Gas, and Oil globally.
USE open_aq;

SELECT 
    year,
    ROUND(SUM(coal_co2), 2) AS global_coal_co2,
    ROUND(SUM(gas_co2), 2) AS global_gas_co2,
    ROUND(SUM(oil_co2), 2) AS global_oil_co2,
    ROUND(SUM(coal_co2 + gas_co2 + oil_co2), 2) AS total_fossil_co2
FROM global_co2_emissions
WHERE country = 'World' AND year IN (1980, 1990, 2000, 2010, 2020)
GROUP BY ALL
ORDER BY year ASC;
-- === SNIPPET END ===
