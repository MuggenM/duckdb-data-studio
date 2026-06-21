USE logistics;

-- ==========================================
-- MODULE 1: INFRASTRUCTURE & STAFF
-- ==========================================

CREATE TABLE depots (
    id UBIGINT PRIMARY KEY,
    name VARCHAR NOT NULL, -- e.g., "North Regional Sorting Hub"
    city VARCHAR NOT NULL,
    capacity_shipments INT NOT NULL DEFAULT 10000
);

CREATE TABLE couriers (
    id UBIGINT PRIMARY KEY,
    name VARCHAR NOT NULL,
    vehicle_type VARCHAR NOT NULL, -- 'Electric Van', 'Heavy Truck', 'Bicycle'
    active_status VARCHAR NOT NULL DEFAULT 'active' -- 'active', 'on_leave', 'retired'
);

-- ==========================================
-- MODULE 2: SHIPMENTS & TRANSACTIONS
-- ==========================================

CREATE TABLE shipments (
    id UBIGINT PRIMARY KEY,
    sender_name VARCHAR NOT NULL,
    recipient_city VARCHAR NOT NULL,
    weight_kg DOUBLE NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'created', -- 'created', 'sorted', 'out_for_delivery', 'delivered', 'failed'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tracking_events (
    id UBIGINT PRIMARY KEY,
    shipment_id UBIGINT NOT NULL,
    courier_id UBIGINT,
    depot_id UBIGINT,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    status_event VARCHAR NOT NULL, -- 'manifest_received', 'arrived_at_depot', 'departed_depot', 'delivered', 'delivery_attempt_failed'
    description TEXT
);

CREATE TABLE delivery_feedback (
    shipment_id UBIGINT PRIMARY KEY,
    rating INT NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comments TEXT,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Indexing for analytical routing logs
CREATE INDEX idx_tracking_shipment ON tracking_events (shipment_id, timestamp);
CREATE INDEX idx_shipments_created ON shipments (created_at);


-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Delivery Turnaround Time & Performance
-- Description: Measures transit duration from creation to delivery across vehicle categories.
USE logistics;

SELECT 
    c.vehicle_type,
    COUNT(s.id) AS total_deliveries,
    ROUND(AVG(epoch(te.timestamp - s.created_at) / 3600.0), 2) AS avg_delivery_hours,
    ROUND(MAX(epoch(te.timestamp - s.created_at) / 3600.0), 2) AS max_delivery_hours
FROM shipments s
JOIN tracking_events te ON s.id = te.shipment_id
JOIN couriers c ON te.courier_id = c.id
WHERE te.status_event = 'delivered' AND s.status = 'delivered'
GROUP BY ALL
ORDER BY avg_delivery_hours ASC;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Sorting Hub Processing Latency
-- Description: Measures duration sorting hubs take to process packages (arrived to departed).
USE logistics;

WITH depot_events AS (
    SELECT 
        depot_id,
        shipment_id,
        timestamp AS event_time,
        status_event
    FROM tracking_events
    WHERE depot_id IS NOT NULL AND status_event IN ('arrived_at_depot', 'departed_depot')
),
processing_spans AS (
    SELECT 
        arr.depot_id,
        arr.shipment_id,
        epoch(dep.event_time - arr.event_time) / 60.0 AS duration_minutes
    FROM depot_events arr
    JOIN depot_events dep ON arr.depot_id = dep.depot_id 
                         AND arr.shipment_id = dep.shipment_id
                         AND arr.status_event = 'arrived_at_depot'
                         AND dep.status_event = 'departed_depot'
)
SELECT 
    d.name AS depot_name,
    COUNT(ps.shipment_id) AS items_processed,
    ROUND(AVG(ps.duration_minutes), 2) AS avg_sorting_minutes,
    ROUND(MAX(ps.duration_minutes), 2) AS max_sorting_minutes
FROM processing_spans ps
JOIN depots d ON ps.depot_id = d.id
GROUP BY ALL
ORDER BY avg_sorting_minutes DESC;
-- === SNIPPET END ===
