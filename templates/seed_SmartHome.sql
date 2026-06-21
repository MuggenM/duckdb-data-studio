USE smart_home;

-- ==========================================
-- MODULE 1: HOUSE STRUCTURE & DEVICES
-- ==========================================

CREATE TABLE rooms (
    id UBIGINT PRIMARY KEY,
    name VARCHAR NOT NULL, -- e.g., "Living Room", "Master Bedroom"
    floor INT NOT NULL DEFAULT 1,
    zone VARCHAR NOT NULL  -- e.g., "Living Zone", "Sleeping Zone", "Basement"
);

CREATE TABLE devices (
    id UBIGINT PRIMARY KEY,
    room_id UBIGINT NOT NULL,
    device_type VARCHAR NOT NULL, -- 'Thermostat', 'Smart Plug', 'Light Switch', 'Camera'
    model VARCHAR NOT NULL,
    firmware_version VARCHAR NOT NULL,
    installed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_online BOOLEAN DEFAULT TRUE
);

-- ==========================================
-- MODULE 2: TIME-SERIES TELEMETRY & LOGS
-- ==========================================

CREATE TABLE thermostat_readings (
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    device_id UBIGINT NOT NULL,
    target_temp DOUBLE NOT NULL,
    actual_temp DOUBLE NOT NULL,
    humidity DOUBLE NOT NULL,
    hvac_state VARCHAR NOT NULL -- 'off', 'heating', 'cooling'
);

CREATE TABLE energy_consumption (
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    device_id UBIGINT NOT NULL,
    power_draw_watts DOUBLE NOT NULL,
    voltage DOUBLE DEFAULT 120.0
);

CREATE TABLE device_events (
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    device_id UBIGINT NOT NULL,
    event_type VARCHAR NOT NULL, -- 'connection_drop', 'firmware_updated', 'motion_detected'
    severity VARCHAR NOT NULL, -- 'info', 'warning', 'critical'
    details TEXT
);

-- Create temporal indexing for rapid analytical grouping
CREATE INDEX idx_thermostat_time ON thermostat_readings (timestamp);
CREATE INDEX idx_energy_time ON energy_consumption (timestamp);


-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Hourly Energy Consumption by Room Zone
-- Description: Aggregates energy consumption hourly by house zones for efficiency mapping.
USE smart_home;

SELECT 
    date_trunc('hour', ec.timestamp) AS hour_bucket,
    r.zone,
    ROUND(SUM(ec.power_draw_watts * 1.0) / 1000.0, 4) AS energy_kwh,
    ROUND(AVG(ec.voltage), 2) AS avg_voltage
FROM energy_consumption ec
JOIN devices d ON ec.device_id = d.id
JOIN rooms r ON d.room_id = r.id
GROUP BY ALL
ORDER BY hour_bucket DESC, energy_kwh DESC;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: HVAC Efficiency & Discrepancy Analysis
-- Description: Analyzes rooms where actual temperature deviates from target while HVAC is running.
USE smart_home;

SELECT 
    r.name AS room_name,
    d.model AS device_model,
    tr.hvac_state,
    COUNT(*) AS total_reading_minutes,
    ROUND(AVG(ABS(tr.actual_temp - tr.target_temp)), 2) AS avg_temperature_error,
    ROUND(AVG(tr.humidity), 2) AS avg_humidity
FROM thermostat_readings tr
JOIN devices d ON tr.device_id = d.id
JOIN rooms r ON d.room_id = r.id
WHERE tr.hvac_state IN ('heating', 'cooling')
GROUP BY ALL
HAVING avg_temperature_error > 1.5
ORDER BY avg_temperature_error DESC;
-- === SNIPPET END ===
