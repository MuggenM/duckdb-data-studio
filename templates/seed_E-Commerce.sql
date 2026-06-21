--Production-Ready SQL Schema Blueprint
-- ==========================================
-- MODULE 1: USERS & AUTHENTICATION
-- ==========================================

CREATE TABLE users (
    id UBIGINT PRIMARY KEY, -- Unsigned BigInt optimized for large analytical keys
    email VARCHAR NOT NULL, -- DuckDB VARCHAR is unlimited length by default
    first_name VARCHAR,
    last_name VARCHAR,
    phone_number VARCHAR,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_addresses (
    id UBIGINT PRIMARY KEY,
    user_id UBIGINT,
    address_type VARCHAR, -- 'shipping' or 'billing'
    is_default BOOLEAN DEFAULT FALSE,
    address_line1 VARCHAR NOT NULL,
    address_line2 VARCHAR,
    city VARCHAR NOT NULL,
    state_province VARCHAR,
    postal_code VARCHAR NOT NULL,
    country_code VARCHAR, -- ISO 3166-1 alpha-3 (e.g., 'USA')
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- MODULE 2: PRODUCT CATALOG & INVENTORY
-- ==========================================

CREATE TABLE categories (
    id UBIGINT PRIMARY KEY,
    parent_id UBIGINT, -- For subcategories
    name VARCHAR NOT NULL,
    slug VARCHAR NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE products (
    id UBIGINT PRIMARY KEY,
    category_id UBIGINT,
    name VARCHAR NOT NULL,
    slug VARCHAR NOT NULL,
    description TEXT,
    brand VARCHAR,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE product_variants (
    id UBIGINT PRIMARY KEY,
    product_id UBIGINT,
    sku VARCHAR NOT NULL,
    price DOUBLE NOT NULL, -- Analytical systems favor DOUBLE over strict NUMERIC for raw speed
    compare_at_price DOUBLE,
    weight_kg DOUBLE,
    attributes JSON, -- DuckDB has a dedicated, highly optimized JSON type
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE product_images (
    id UBIGINT PRIMARY KEY,
    product_id UBIGINT,
    variant_id UBIGINT,
    url VARCHAR NOT NULL,
    alt_text VARCHAR,
    sort_order INT DEFAULT 0,
    is_primary BOOLEAN DEFAULT FALSE
);

-- ==========================================
-- MODULE 3: ORDERS & FULFILLMENT (The Core Fact Tables)
-- ==========================================

CREATE TABLE orders (
    id UBIGINT PRIMARY KEY,
    order_number VARCHAR NOT NULL,
    user_id UBIGINT,
    order_status VARCHAR NOT NULL DEFAULT 'pending', 
    
    -- Financial snapshots
    subtotal_amount DOUBLE NOT NULL,
    shipping_amount DOUBLE NOT NULL DEFAULT 0.00,
    tax_amount DOUBLE NOT NULL DEFAULT 0.00,
    discount_amount DOUBLE NOT NULL DEFAULT 0.00,
    total_amount DOUBLE NOT NULL,
    
    shipping_address_id UBIGINT,
    billing_address_id UBIGINT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE order_items (
    id UBIGINT PRIMARY KEY,
    order_id UBIGINT NOT NULL,
    variant_id UBIGINT,
    sku_snapshot VARCHAR NOT NULL,   
    price_snapshot DOUBLE NOT NULL, 
    quantity INT NOT NULL
);

CREATE TABLE payments (
    id UBIGINT PRIMARY KEY,
    order_id UBIGINT NOT NULL,
    payment_gateway VARCHAR NOT NULL, -- e.g., 'Stripe', 'PayPal'
    transaction_reference VARCHAR NOT NULL, 
    amount DOUBLE NOT NULL,
    status VARCHAR NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
