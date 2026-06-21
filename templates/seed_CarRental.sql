-- ==========================================
-- MODULE 1: CUSTOMERS & COMPLIANCE
-- ==========================================

CREATE TABLE customers (
    id UBIGINT PRIMARY KEY, -- Unsigned BigInt optimized for analytical data-lake keys
    email VARCHAR NOT NULL, -- DuckDB VARCHAR is unlimited length by default
    first_name VARCHAR NOT NULL,
    last_name VARCHAR NOT NULL,
    phone_number VARCHAR NOT NULL,
    status VARCHAR DEFAULT 'active', -- 'active', 'suspended', 'blacklisted'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE driver_licenses (
    customer_id UBIGINT PRIMARY KEY,
    license_number VARCHAR NOT NULL,
    state_province VARCHAR NOT NULL,
    country_code VARCHAR, -- ISO 3166-1 alpha-3 (e.g., 'USA')
    expiration_date DATE NOT NULL,
    is_verified BOOLEAN DEFAULT FALSE,
    verified_at TIMESTAMP WITH TIME ZONE
);

-- ==========================================
-- MODULE 2: LOCATIONS & FLEET
-- ==========================================

CREATE TABLE locations (
    id UBIGINT PRIMARY KEY,
    name VARCHAR NOT NULL, -- e.g., "LAX Airport Hub"
    address_line1 VARCHAR NOT NULL,
    city VARCHAR NOT NULL,
    state_province VARCHAR NOT NULL,
    postal_code VARCHAR NOT NULL,
    country_code VARCHAR,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE vehicle_profiles (
    id UBIGINT PRIMARY KEY,
    category VARCHAR NOT NULL, -- 'Economy', 'SUV', 'Convertible'
    make VARCHAR NOT NULL,     
    model VARCHAR NOT NULL,    
    year INT NOT NULL,
    fuel_type VARCHAR,         -- 'gasoline', 'diesel', 'electric', 'hybrid'
    seating_capacity INT NOT NULL,
    baggage_capacity INT NOT NULL,
    daily_rate DOUBLE NOT NULL -- Analytical systems favor DOUBLE over strict NUMERIC for raw mathematical speed
);

CREATE TABLE cars (
    id UBIGINT PRIMARY KEY,
    vehicle_profile_id UBIGINT,
    current_location_id UBIGINT,
    vin VARCHAR NOT NULL,       
    license_plate VARCHAR NOT NULL,
    color VARCHAR,
    current_odometer INT NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'available', -- 'available', 'rented', 'maintenance', 'retired'
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- MODULE 3: RESERVATIONS & BILLING (The Core Fact Tables)
-- ==========================================

CREATE TABLE reservations (
    id UBIGINT PRIMARY KEY,
    confirmation_code UUID NOT NULL, -- DuckDB natively supports native 128-bit UUID types
    customer_id UBIGINT NOT NULL,
    car_id UBIGINT NOT NULL,
    
    -- Pick-up / Drop-off Logistics
    pickup_location_id UBIGINT NOT NULL,
    dropoff_location_id UBIGINT NOT NULL,
    scheduled_pickup_time TIMESTAMP WITH TIME ZONE NOT NULL,
    scheduled_dropoff_time TIMESTAMP WITH TIME ZONE NOT NULL,
    actual_pickup_time TIMESTAMP WITH TIME ZONE,
    actual_dropoff_time TIMESTAMP WITH TIME ZONE,
    
    status VARCHAR NOT NULL DEFAULT 'confirmed', -- 'confirmed', 'active', 'completed', 'cancelled'
    
    -- Financial breakdown
    rental_cost DOUBLE NOT NULL, 
    insurance_cost DOUBLE DEFAULT 0.00,
    late_fees DOUBLE DEFAULT 0.00,
    tax_amount DOUBLE NOT NULL,
    total_amount DOUBLE NOT NULL,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE payments (
    id UBIGINT PRIMARY KEY,
    reservation_id UBIGINT NOT NULL,
    payment_gateway VARCHAR NOT NULL, -- e.g., 'Stripe'
    transaction_reference VARCHAR NOT NULL,
    amount DOUBLE NOT NULL,
    type VARCHAR NOT NULL,            -- 'deposit', 'final_payment', 'refund'
    status VARCHAR NOT NULL,          -- 'authorized', 'captured', 'failed'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- MODULE 4: OPERATIONS & MAINTENANCE
-- ==========================================

CREATE TABLE maintenance_logs (
    id UBIGINT PRIMARY KEY,
    car_id UBIGINT NOT NULL,
    type VARCHAR NOT NULL, -- 'oil_change', 'tire_rotation', 'body_repair'
    description TEXT,
    cost DOUBLE DEFAULT 0.00,
    odometer_at_service INT NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    technician_notes TEXT
);

CREATE INDEX idx_reservations_car_schedule 
ON reservations (car_id, scheduled_pickup_time, scheduled_dropoff_time);


-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Calculating Fleet Utilization Rate (By Vehicle Class)
-- Description: Calculates the utilization percentage of the car rental fleet by vehicle class.
USE car_rental;

SELECT 
    vp.category,
    vp.make,
    vp.model,
    COUNT(c.id) AS total_fleet_count,
    -- Calculate percentage of vehicles currently with a 'rented' status
    ROUND(COUNT(CASE WHEN c.status = 'rented' THEN 1 END) * 100.0 / COUNT(c.id), 2) AS utilization_percentage,
    ROUND(AVG(vp.daily_rate), 2) AS avg_daily_rate
FROM cars c
JOIN vehicle_profiles vp ON c.vehicle_profile_id = vp.id
WHERE c.status != 'retired'
GROUP BY ALL
ORDER BY utilization_percentage DESC;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Uncovering Distance and Revenue Discrepancies (One-Way Rentals)
-- Description: Uncover distance and revenue discrepancies for one-way rentals compared to round-trips.
USE car_rental;

SELECT 
    loc_start.name AS pickup_hub,
    loc_end.name AS dropoff_hub,
    COUNT(r.id) AS total_trips,
    -- Determine if it's a one-way trip
    CASE WHEN r.pickup_location_id = r.dropoff_location_id THEN 'Round-Trip' ELSE 'One-Way' END AS trip_type,
    ROUND(AVG(r.total_amount), 2) AS average_revenue,
    COUNT(CASE WHEN r.status = 'completed' THEN 1 END) AS completed_rentals
FROM reservations r
JOIN locations loc_start ON r.pickup_location_id = loc_start.id
JOIN locations loc_end ON r.dropoff_location_id = loc_end.id
GROUP BY ALL
ORDER BY total_trips DESC;
-- === SNIPPET END ===
