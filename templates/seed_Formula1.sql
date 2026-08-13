-- Database: formula1
-- Formula 1 World Championship Dataset (1950-2020)
-- Source: Rohan Rao / Ergast F1 Historical Data
INSTALL httpfs;
LOAD httpfs;

-- ==========================================
-- 1. TABLE: CIRCUITS
-- ==========================================
CREATE OR REPLACE TABLE circuits AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/circuits.csv';

-- ==========================================
-- 2. TABLE: CONSTRUCTORS
-- ==========================================
CREATE OR REPLACE TABLE constructors AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/constructors.csv';

-- ==========================================
-- 3. TABLE: DRIVERS
-- ==========================================
CREATE OR REPLACE TABLE drivers AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/drivers.csv';

-- ==========================================
-- 4. TABLE: RACES
-- ==========================================
CREATE OR REPLACE TABLE races AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/races.csv';

-- ==========================================
-- 5. TABLE: RESULTS
-- ==========================================
CREATE OR REPLACE TABLE results AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/results.csv';

-- ==========================================
-- 6. TABLE: QUALIFYING
-- ==========================================
CREATE OR REPLACE TABLE qualifying AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/qualifying.csv';

-- ==========================================
-- 7. TABLE: PIT STOPS
-- ==========================================
CREATE OR REPLACE TABLE pit_stops AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/pit_stops.csv';

-- ==========================================
-- 8. TABLE: LAP TIMES
-- ==========================================
CREATE OR REPLACE TABLE lap_times AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/lap_times.csv';

-- ==========================================
-- 9. TABLE: DRIVER STANDINGS
-- ==========================================
CREATE OR REPLACE TABLE driver_standings AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/driver_standings.csv';

-- ==========================================
-- 10. TABLE: CONSTRUCTOR STANDINGS
-- ==========================================
CREATE OR REPLACE TABLE constructor_standings AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/constructor_standings.csv';

-- ==========================================
-- 11. TABLE: CONSTRUCTOR RESULTS
-- ==========================================
CREATE OR REPLACE TABLE constructor_results AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/constructor_results.csv';

-- ==========================================
-- 12. TABLE: STATUS
-- ==========================================
CREATE OR REPLACE TABLE status AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/status.csv';

-- ==========================================
-- 13. TABLE: SEASONS
-- ==========================================
CREATE OR REPLACE TABLE seasons AS 
SELECT * FROM 'https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2021/2021-09-07/seasons.csv';

-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Top 10 Drivers by Total Grand Prix Victories
-- Description: Aggregates race results to rank Formula 1 drivers by total P1 race wins across all seasons (1950-2020).
-- Category: Analytical
USE formula1;

SELECT 
    d.forename || ' ' || d.surname AS driver_name,
    d.nationality,
    COUNT(res.resultId) AS total_wins,
    MIN(r.year) AS first_win_year,
    MAX(r.year) AS last_win_year
FROM formula1.main.results res
JOIN formula1.main.drivers d ON res.driverId = d.driverId
JOIN formula1.main.races r ON res.raceId = r.raceId
WHERE res.position = '1'
GROUP BY d.driverId, driver_name, d.nationality
ORDER BY total_wins DESC
LIMIT 10;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Constructor Championships & Wins Summary
-- Description: Ranks Formula 1 constructor teams by overall race wins and podium finishes.
-- Category: Analytical
USE formula1;

SELECT 
    c.name AS constructor_name,
    c.nationality,
    COUNT(CASE WHEN res.position = '1' THEN 1 END) AS wins,
    COUNT(CASE WHEN res.positionText IN ('1', '2', '3') THEN 1 END) AS podiums,
    ROUND(SUM(res.points), 1) AS total_points
FROM formula1.main.results res
JOIN formula1.main.constructors c ON res.constructorId = c.constructorId
GROUP BY c.constructorId, c.name, c.nationality
ORDER BY wins DESC, podiums DESC
LIMIT 15;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Fastest Pit Stop Durations
-- Description: Analyzes pit stop duration data to show the fastest team pit stop performances.
-- Category: Analytical
USE formula1;

SELECT 
    r.year,
    r.name AS grand_prix,
    d.forename || ' ' || d.surname AS driver_name,
    c.name AS constructor_name,
    p.stop AS pit_stop_number,
    p.duration AS duration_seconds,
    p.milliseconds
FROM formula1.main.pit_stops p
JOIN formula1.main.races r ON p.raceId = r.raceId
JOIN formula1.main.drivers d ON p.driverId = d.driverId
JOIN formula1.main.results res ON p.raceId = res.raceId AND p.driverId = res.driverId
JOIN formula1.main.constructors c ON res.constructorId = c.constructorId
WHERE p.milliseconds IS NOT NULL AND p.milliseconds > 1000 AND p.milliseconds < 60000
ORDER BY p.milliseconds ASC
LIMIT 15;
-- === SNIPPET END ===
