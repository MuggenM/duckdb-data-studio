USE clickstream;

-- ==========================================
-- MODULE 1: CUSTOMERS & SESSIONS
-- ==========================================

CREATE TABLE visitors (
    id UBIGINT PRIMARY KEY,
    cookie_id VARCHAR NOT NULL,
    traffic_source VARCHAR, -- 'Organic Search', 'Google Ads', 'Newsletter', 'Direct'
    country_code VARCHAR,
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessions (
    id UBIGINT PRIMARY KEY,
    visitor_id UBIGINT NOT NULL,
    session_token VARCHAR NOT NULL,
    ip_address VARCHAR,
    browser VARCHAR, -- 'Chrome', 'Firefox', 'Safari', 'Edge'
    started_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- ==========================================
-- MODULE 2: INTERACTIVE EVENTSFACTS
-- ==========================================

CREATE TABLE page_views (
    id UBIGINT PRIMARY KEY,
    session_id UBIGINT NOT NULL,
    url_path VARCHAR NOT NULL, -- e.g., "/home", "/products/details", "/cart"
    referrer VARCHAR,
    load_time_ms INT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE campaign_clicks (
    id UBIGINT PRIMARY KEY,
    visitor_id UBIGINT NOT NULL,
    campaign_name VARCHAR NOT NULL, -- e.g., "Summer_Sale_2026", "Retargeting_Active"
    medium VARCHAR NOT NULL, -- 'cpc', 'email', 'social'
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE checkout_events (
    id UBIGINT PRIMARY KEY,
    session_id UBIGINT NOT NULL,
    step VARCHAR NOT NULL, -- '1_view_cart', '2_shipping', '3_payment', '4_success'
    cart_value DOUBLE DEFAULT 0.00,
    is_success BOOLEAN NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Create log indexes for sequence grouping
CREATE INDEX idx_page_views_session ON page_views (session_id, timestamp);
CREATE INDEX idx_checkout_session ON checkout_events (session_id, timestamp);


-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Session Conversion Funnel Analysis
-- Description: Measures user drop-off percentages step-by-step through checkout stages.
USE clickstream;

SELECT 
    step,
    COUNT(DISTINCT session_id) AS active_sessions,
    ROUND(COUNT(DISTINCT session_id) * 100.0 / (
        SELECT COUNT(DISTINCT session_id) FROM checkout_events WHERE step = '1_view_cart'
    ), 2) AS survival_percentage,
    ROUND(AVG(cart_value), 2) AS avg_cart_value
FROM checkout_events
GROUP BY ALL
ORDER BY step ASC;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Page Load Metrics & Bounce Analysis
-- Description: Groups page load performance and session navigation counts by browser.
USE clickstream;

WITH session_counts AS (
    SELECT 
        session_id,
        COUNT(id) AS page_views_count,
        ROUND(AVG(load_time_ms), 2) AS avg_session_load_time
    FROM page_views
    GROUP BY session_id
)
SELECT 
    s.browser,
    COUNT(s.id) AS total_sessions,
    ROUND(AVG(sc.page_views_count), 2) AS avg_pages_per_session,
    ROUND(AVG(sc.avg_session_load_time), 2) AS avg_load_time_ms,
    -- A bounce is defined as sessions viewing exactly 1 page
    ROUND(COUNT(CASE WHEN sc.page_views_count = 1 THEN 1 END) * 100.0 / COUNT(s.id), 2) AS bounce_rate_percentage
FROM sessions s
JOIN session_counts sc ON s.id = sc.session_id
GROUP BY ALL
ORDER BY bounce_rate_percentage DESC;
-- === SNIPPET END ===
