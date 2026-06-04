#!/usr/bin/env python3
import os
import asyncio
import io
import csv
import time
import random
from datetime import datetime, timedelta
from collections import defaultdict
from nicegui import ui, app

# Monkeypatch NiceGUI ui.element to support text() method chaining
def element_text_patch(self, text_val):
    self._text = str(text_val)
    return self
ui.element.text = element_text_patch

from fastapi import Query, Request
import duckdb
from local_file_picker.local_file_picker import local_file_picker
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

def split_sql_trailing_clauses(sql):
    # Parse top-level ORDER BY, LIMIT, or OFFSET to prevent placing WHERE after them
    sql_upper = sql.upper()
    keywords = ['ORDER BY', 'LIMIT', 'OFFSET']
    
    depth = 0
    keyword_idx = -1
    i = 0
    n = len(sql)
    while i < n:
        char = sql[i]
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif depth == 0:
            for kw in keywords:
                kw_len = len(kw)
                if i + kw_len <= n and sql_upper[i:i+kw_len] == kw:
                    prev_char_ok = (i == 0 or not sql_upper[i-1].isalnum() and sql_upper[i-1] != '_')
                    next_char_ok = (i + kw_len == n or not sql_upper[i+kw_len].isalnum() and sql_upper[i+kw_len] != '_')
                    if prev_char_ok and next_char_ok:
                        keyword_idx = i
                        break
            if keyword_idx != -1:
                break
        i += 1
        
    if keyword_idx != -1:
        return sql[:keyword_idx].rstrip(), " " + sql[keyword_idx:].strip()
    return sql, ""

def verify_jwt_token(auth_header: str):
    """Verify standard Bearer JWT token optionally requiring pyjwt."""
    import os
    from fastapi import HTTPException
    
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
        
    try:
        if " " not in auth_header:
            raise ValueError("Invalid auth header format")
        token_type, token = auth_header.split(" ", 1)
        if token_type.lower() != "bearer":
            raise ValueError("Token must be a Bearer token")
            
        import jwt
        secret = APP_SETTINGS.get("jwt_secret", os.environ.get("STUDIO_JWT_SECRET", "duckdb_studio_secret_key_1337"))
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

def get_dynamic_rate_limit(request: Request = None) -> str:
    """Resolve dynamic rate-limit string per endpoint from DB, falling back to dynamic settings default."""
    if request is None:
        return APP_SETTINGS.get("default_rate_limit", "5/minute")
    path = request.url.path
    import re
    match = re.match(r'^/api/(.+?)(?:/stream)?$', path)
    if match:
        endpoint_path = match.group(1)
        if endpoint_path != "list-endpoints":
            try:
                db_conn = duckdb.connect(DB_NAME)
                res = db_conn.execute("SELECT rate_limit FROM _duckdb_studio_api_endpoints WHERE path = ?;", [endpoint_path]).fetchone()
                db_conn.close()
                if res and res[0] and res[0].strip():
                    return res[0].strip()
            except Exception as e:
                print(f"WARNING: Dynamic rate limit lookup failed for {endpoint_path}: {e}", flush=True)
            
    return APP_SETTINGS.get("default_rate_limit", "5/minute")



# --- DATABASE ENGINE & SEEDER ---

class DuckDBExplorer:
    def __init__(self, db_file):
        self.db_file = db_file
        self._conn = None
        self._cursor = None
        
    @property
    def conn(self):
        if self._conn is None:
            self._conn = duckdb.connect(self.db_file)
        return self._conn
        
    @property
    def cursor(self):
        if self._cursor is None:
            self._cursor = self.conn.cursor()
        return self._cursor
        
    def list_tables(self):
        tables = defaultdict(list)
        try:
            for row in self.conn.execute("SELECT table_schema, table_name FROM information_schema.tables").fetchall():
                schema, name = row
                tables[schema].append(name)
        except Exception as e:
            print(f"Error listing tables: {e}")
        return dict(tables)
        
    def list_columns_with_types(self, table, database=None, schema=None):
        try:
            if database and schema:
                query_str = f"SELECT column_name, data_type FROM duckdb_columns WHERE database_name = '{database}' AND schema_name = '{schema}' AND table_name = '{table}' ORDER BY column_index;"
                columns = self.conn.execute(query_str).fetchall()
                return [(row[0], row[1]) for row in columns]
            else:
                columns = self.conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                return [(row[1], row[2]) for row in columns]  # (name, type)
        except Exception as e:
            print(f"Error fetching columns for table {table}: {e}")
            return []
    
    def query(self, sql):
        start_time = time.time()
        try:
            result = self.conn.execute(sql)
            duration_ms = round((time.time() - start_time) * 1000, 2)
            
            if result.description is not None:
                # SELECT / RETURNING queries
                data = result.fetchall()
                columns = [desc[0] for desc in result.description]
                return {
                    'data': data,
                    'columns': columns,
                    'is_select': True,
                    'duration_ms': duration_ms,
                    'affected_rows': len(data)
                }
            else:
                # DDL / DML queries (CREATE, INSERT, UPDATE, etc.)
                affected = result.rowcount
                return {
                    'data': [],
                    'columns': [],
                    'is_select': False,
                    'duration_ms': duration_ms,
                    'affected_rows': affected if affected != -1 else 0
                }
        except Exception as e:
            return {
                'error': str(e),
                'duration_ms': round((time.time() - start_time) * 1000, 2)
            }

    def close(self):
        try:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._cursor = None
        except Exception:
            pass

    def __del__(self):
        self.close()

    def save_custom_query(self, name, description, sql_code, category='Analytical'):
        import uuid
        q_id = str(uuid.uuid4())
        try:
            self.conn.execute("""
                INSERT INTO _duckdb_studio_saved_queries (id, name, description, sql_code, created_at, category)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (q_id, name, description, sql_code, datetime.now(), category))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error saving custom query: {e}")
            return False

    def list_saved_queries(self):
        try:
            rows = self.conn.execute("SELECT id, name, description, sql_code, created_at, category FROM _duckdb_studio_saved_queries ORDER BY created_at DESC").fetchall()
            return [{
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'sql_code': row[3],
                'created_at': row[4],
                'category': row[5] if row[5] else 'Analytical'
            } for row in rows]
        except Exception as e:
            print(f"Error listing saved queries: {e}")
            return []

    def delete_saved_query(self, q_id):
        try:
            self.conn.execute("DELETE FROM _duckdb_studio_saved_queries WHERE id = ?", (q_id,))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error deleting saved query: {e}")
            return False

def calculate_next_run(interval: str, now=None):
    import datetime
    if now is None:
        now = datetime.datetime.now()
    if interval == "Every Minute":
        return now + datetime.timedelta(minutes=1)
    elif interval == "Every 5 Minutes":
        return now + datetime.timedelta(minutes=5)
    elif interval == "Every 15 Minutes":
        return now + datetime.timedelta(minutes=15)
    elif interval == "Every Hour":
        return now + datetime.timedelta(hours=1)
    elif interval == "Every 12 Hours":
        return now + datetime.timedelta(hours=12)
    elif interval == "Daily":
        next_day = now + datetime.timedelta(days=1)
        return datetime.datetime(next_day.year, next_day.month, next_day.day, 0, 0, 0)
    else:
        return now + datetime.timedelta(minutes=1)


def run_background_scheduler():
    import time, datetime, os, duckdb, uuid
    print("INFO: Starting Background Query Scheduler Thread...", flush=True)
    
    export_dir = "/home/martin/volumes/duckdb-studio/exports"
    try:
        os.makedirs(export_dir, exist_ok=True)
    except Exception as e:
        print(f"ERROR: Failed to create export directory: {e}", flush=True)
        
    db_path = DB_NAME
    
    while True:
        try:
            conn = duckdb.connect(db_path)
            
            # Ensure tables exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_scheduled_jobs (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    sql_code VARCHAR,
                    interval_str VARCHAR,
                    export_format VARCHAR,
                    partition_column VARCHAR,
                    export_filename VARCHAR,
                    last_run TIMESTAMP,
                    next_run TIMESTAMP,
                    status VARCHAR,
                    error_message VARCHAR
                );
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_scheduler_logs (
                    id VARCHAR PRIMARY KEY,
                    job_id VARCHAR,
                    job_name VARCHAR,
                    executed_at TIMESTAMP,
                    duration_ms DOUBLE,
                    row_count INTEGER,
                    file_size_bytes INTEGER,
                    status VARCHAR,
                    error_message VARCHAR
                );
            """)
            
            # Get active jobs where next_run <= NOW
            now = datetime.datetime.now()
            jobs = conn.execute("""
                SELECT 
                    id, name, sql_code, interval_str, export_format, 
                    partition_column, export_filename, next_run 
                FROM _duckdb_studio_scheduled_jobs 
                WHERE status = 'Active' AND next_run <= ?;
            """, [now]).fetchall()
            
            for j_id, j_name, j_sql, j_interval, j_format, j_part_col, j_filename, j_next in jobs:
                start_time = time.time()
                row_count = 0
                file_size = 0
                run_status = "Success"
                run_err = None
                
                try:
                    # Construct paths
                    copy_options = f"FORMAT '{j_format.upper()}'"
                    if j_part_col and j_part_col.strip():
                        copy_options += f", PARTITION_BY '{j_part_col.strip()}'"
                        dest_path = os.path.join(export_dir, j_filename + f"_{j_format.lower()}_partitioned")
                        os.makedirs(dest_path, exist_ok=True)
                    else:
                        ext = j_format.lower()
                        dest_path = os.path.join(export_dir, f"{j_filename}.{ext}")
                    
                    # Attach any configured databases if required
                    load_attached_databases_for_connection(conn)
                    
                    # Run the export query
                    conn.execute(f"COPY ({j_sql.strip().rstrip(';')}) TO '{dest_path}' ({copy_options});")
                    
                    # Calculate row count
                    count_df = conn.execute(f"SELECT COUNT(*) FROM ({j_sql.strip().rstrip(';')});").fetchone()
                    row_count = count_df[0] if count_df else 0
                    
                    # Calculate file size
                    if os.path.exists(dest_path):
                        if os.path.isdir(dest_path):
                            for root, dirs, files in os.walk(dest_path):
                                for f in files:
                                    file_size += os.path.getsize(os.path.join(root, f))
                        else:
                            file_size = os.path.getsize(dest_path)
                            
                except Exception as query_ex:
                    run_status = "Failed"
                    run_err = str(query_ex)
                    print(f"ERROR: Scheduled job '{j_name}' failed: {query_ex}", flush=True)
                
                duration_ms = (time.time() - start_time) * 1000.0
                
                # Update job execution status and schedule the next run
                next_run_time = calculate_next_run(j_interval, now)
                conn.execute("""
                    UPDATE _duckdb_studio_scheduled_jobs 
                    SET last_run = ?, next_run = ?, status = ?, error_message = ? 
                    WHERE id = ?;
                """, [now, next_run_time, "Active", run_err, j_id])
                
                # Insert into log history
                log_id = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO _duckdb_studio_scheduler_logs (id, job_id, job_name, executed_at, duration_ms, row_count, file_size_bytes, status, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, [log_id, j_id, j_name, now, duration_ms, row_count, file_size, run_status, run_err])
            
            conn.close()
        except Exception as conn_ex:
            print(f"ERROR: Background Scheduler encountered database error: {conn_ex}", flush=True)
            
        time.sleep(10)


def init_saved_queries_table(db_file):
    """Ensure the custom queries persistence table exists."""
    conn = duckdb.connect(db_file)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _duckdb_studio_saved_queries (
                id VARCHAR PRIMARY KEY,
                name VARCHAR,
                description VARCHAR,
                sql_code VARCHAR,
                created_at TIMESTAMP
            );
        """)
        # Dynamic schema evolution: add category column if missing
        conn.execute("ALTER TABLE _duckdb_studio_saved_queries ADD COLUMN IF NOT EXISTS category VARCHAR;")
        
        # Pre-seed with some default saved queries if it's empty
        res = conn.execute("SELECT count(*) FROM _duckdb_studio_saved_queries").fetchone()
        if res and res[0] == 0:
            import uuid
            default_queries = [
                (str(uuid.uuid4()), "Active High-Value Transactions", "Finds transactions exceeding $300 sorted by amount", "SELECT * FROM sales_transactions WHERE total_amount > 300 ORDER BY total_amount DESC LIMIT 50;", datetime.now(), "Analytical"),
                (str(uuid.uuid4()), "Low Stock Alert", "Finds product categories with low inventory (< 50 items in stock)", "SELECT category, name, stock, price FROM product_inventory WHERE stock < 50 ORDER BY stock ASC;", datetime.now(), "Utility"),
                (str(uuid.uuid4()), "Customer Value Segmentation", "Calculates lifetime value and order frequency per loyalty tier", "SELECT loyalty_tier, COUNT(DISTINCT c.customer_id) AS customer_count, SUM(total_amount) AS total_revenue, AVG(total_amount) AS avg_order_value FROM customer_profiles c JOIN sales_transactions s ON c.customer_id = s.customer_id GROUP BY loyalty_tier ORDER BY total_revenue DESC;", datetime.now(), "Analytical")
            ]
            conn.executemany("INSERT INTO _duckdb_studio_saved_queries VALUES (?, ?, ?, ?, ?, ?)", default_queries)
            conn.commit()
            
        # Migrate any legacy saved queries with null categories to default 'Analytical'
        conn.execute("UPDATE _duckdb_studio_saved_queries SET category = 'Analytical' WHERE category IS NULL;")
        conn.commit()
    except Exception as e:
        print(f"Error initializing saved queries table: {e}")
    finally:
        conn.close()

def seed_database(db_file, force=False, num_customers=400, num_transactions=6500):
    """Seed the database with realistic synthetic data using Faker if empty or forced."""
    conn = duckdb.connect(db_file)
    cursor = conn.cursor()
    
    try:
        tables = cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_name = 'sales_transactions'").fetchall()
        if tables and not force:
            conn.close()
            return False  # Already seeded
            
        if force:
            print("Force resetting database tables...")
            cursor.execute("DROP TABLE IF EXISTS sales_transactions;")
            cursor.execute("DROP TABLE IF EXISTS customer_profiles;")
            cursor.execute("DROP TABLE IF EXISTS product_inventory;")
            
        print("Creating table structures...")
        cursor.execute("""
            CREATE TABLE customer_profiles (
                customer_id INTEGER PRIMARY KEY,
                name VARCHAR,
                email VARCHAR,
                country VARCHAR,
                signup_date DATE,
                loyalty_tier VARCHAR
            );
        """)
        
        cursor.execute("""
            CREATE TABLE product_inventory (
                product_id INTEGER PRIMARY KEY,
                name VARCHAR,
                category VARCHAR,
                price DOUBLE,
                stock INTEGER
            );
        """)
        
        cursor.execute("""
            CREATE TABLE sales_transactions (
                transaction_id VARCHAR PRIMARY KEY,
                transaction_date TIMESTAMP,
                customer_id INTEGER,
                product_id INTEGER,
                quantity INTEGER,
                discount DOUBLE,
                total_amount DOUBLE,
                payment_method VARCHAR
            );
        """)
        
        try:
            from faker import Faker
            fake = Faker()
            Faker.seed(1337)
        except ImportError:
            fake = None

        print("Generating product inventory data...")
        categories = ["Electronics", "Apparel", "Home & Kitchen", "Books", "Sports", "Beauty"]
        product_catalog = {
            "Electronics": ["Smartphone Nexus", "Aura Wireless Headphones", "Quantum Smartwatch", "Pulse ANC Earbuds", "Apex USB-C Dock", "SoundWave speaker"],
            "Apparel": ["Denim Trucker Jacket", "Chino Casual Trousers", "Performance Fleece Hoodie", "Merino Wool Sweater", "Vanguard Leather Boots", "Pima Cotton Tee"],
            "Home & Kitchen": ["AirFry Pro XL", "Barista Espresso Maker", "Chef's Copper Cookware", "RoboVac 9000", "Cloud Memory Pillow", "RapidBoil Kettle"],
            "Books": ["The Clean Code Blueprint", "Modern Data Analytics", "Creative Sparks", "Chronicles of Computing", "Galactic Atlas Vol. 3", "Python Deep Dive"],
            "Sports": ["Zen Yoga Mat", "Hex Adjustable Dumbbells", "ThermoShield Flask", "AeroResistance Bands", "Velocity Running Shoes", "Carbon Road Racer"],
            "Beauty": ["Glow Hydra Serum", "Clay Mask Detox", "Organic Cocoa Body Butter", "Matte Stain Liquid Lipstick", "SunGuard SPF 50", "Mineral Exfoliant"]
        }
        
        cursor.execute("BEGIN TRANSACTION;")
        products = []
        prod_id = 1
        for category, names in product_catalog.items():
            for name in names:
                price = round(random.uniform(12.50, 499.99), 2)
                stock = random.randint(15, 350)
                products.append((prod_id, name, category, price, stock))
                prod_id += 1
        cursor.executemany("INSERT INTO product_inventory VALUES (?, ?, ?, ?, ?)", products)

        print("Generating customer profile data...")
        countries = ["United States", "Germany", "United Kingdom", "Canada", "France", "Japan", "Australia", "Singapore", "Netherlands"]
        loyalty_tiers = ["Bronze", "Silver", "Gold", "Platinum"]
        
        customers = []
        for c_id in range(1, num_customers + 1):
            if fake:
                name = fake.name()
                email = fake.unique.email()
            else:
                name = f"User {c_id}"
                email = f"user_{c_id}@example.com"
            country = random.choice(countries)
            signup = datetime.now() - timedelta(days=random.randint(60, 900))
            tier = random.choices(loyalty_tiers, weights=[50, 30, 15, 5], k=1)[0]
            customers.append((c_id, name, email, country, signup.date(), tier))
        cursor.executemany("INSERT INTO customer_profiles VALUES (?, ?, ?, ?, ?, ?)", customers)

        print("Generating sales transaction history...")
        transactions = []
        start_date = datetime.now() - timedelta(days=365)
        for t_index in range(1, num_transactions + 1):
            txn_id = f"TXN-{100000 + t_index}"
            txn_date = start_date + timedelta(
                seconds=random.randint(0, 365 * 24 * 60 * 60)
            )
            c_id = random.randint(1, num_customers)
            prod = random.choice(products)
            p_id = prod[0]
            p_price = prod[3]
            
            quantity = random.choices([1, 2, 3, 4, 5], weights=[65, 20, 10, 4, 1], k=1)[0]
            discount = random.choices([0.0, 0.05, 0.10, 0.15, 0.25], weights=[70, 15, 8, 5, 2], k=1)[0]
            
            subtotal = p_price * quantity
            total = round(subtotal * (1.0 - discount), 2)
            payment = random.choice(["Credit Card", "PayPal", "Apple Pay", "Google Pay", "Bank Transfer"])
            
            transactions.append((txn_id, txn_date, c_id, p_id, quantity, discount, total, payment))
            
        cursor.executemany("INSERT INTO sales_transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", transactions)
        cursor.execute("COMMIT;")
        conn.commit()
        print("Database Seeding Successful!")
        return True
    except Exception as e:
        print(f"Error during seeding: {e}")
        try:
            cursor.execute("ROLLBACK;")
        except Exception:
            pass
        return False
    finally:
        conn.close()

# --- JUPYTERLAB CONFIGURATION HELPERS ---

def get_studio_config_path():
    """Get the path to studio_config.yaml, using /config if running in Docker, otherwise config/studio_config.yaml."""
    if os.path.exists('/config') and os.path.isdir('/config'):
        return '/config/studio_config.yaml'
    if not os.path.exists('config'):
        os.makedirs('config', exist_ok=True)
    return 'config/studio_config.yaml'

def get_jupyter_config():
    """Load Jupyter URL and Token from environment variables or studio_config.yaml."""
    default_url = "http://localhost:8889"
    default_token = "analytics_secret"
    
    # 1. Check environment variables first
    env_url = os.environ.get("JUPYTER_URL")
    env_token = os.environ.get("JUPYTER_TOKEN")
    
    # 2. Check YAML config file
    config_url = None
    config_token = None
    
    config_path = get_studio_config_path()
    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            if config and isinstance(config, dict):
                jupyter_conf = config.get('jupyter', {})
                if isinstance(jupyter_conf, dict):
                    config_url = jupyter_conf.get('url')
                    config_token = jupyter_conf.get('token')
        except Exception as e:
            print(f"WARNING: Failed to load config from {config_path}: {e}")
            
    final_url = env_url or config_url or default_url
    final_token = env_token or config_token or default_token
    return final_url, final_token

def load_app_settings():
    """Load studio settings from yaml configuration file, providing defaults."""
    config_path = get_studio_config_path()
    defaults = {
        "default_rate_limit": "5/minute",
        "max_safety_limit": 10000,
        "default_page_size": 100,
        "telemetry_retention_days": 30,
        "jwt_secret": "duckdb_studio_secret_key_1337",
        "jwt_issuer": "duckdb_studio",
        "jwt_audience": "duckdb_studio_clients"
    }
    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            if config and isinstance(config, dict):
                settings = config.get('settings', {})
                if isinstance(settings, dict):
                    for k, v in settings.items():
                        defaults[k] = v
        except Exception as e:
            print(f"WARNING: Failed to load settings from {config_path}: {e}")
    return defaults

def save_app_settings(settings_dict, jupyter_dict=None):
    """Save studio settings and optionally jupyter config back to yaml configuration file."""
    config_path = get_studio_config_path()
    try:
        import yaml
        config = {}
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}
        
        config['settings'] = settings_dict
        if jupyter_dict is not None:
            config['jupyter'] = jupyter_dict
        
        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)
            
        global APP_SETTINGS
        APP_SETTINGS = load_app_settings()
        return True
    except Exception as e:
        print(f"ERROR: Failed to save settings to {config_path}: {e}")
        return False

# Load global settings dictionary
APP_SETTINGS = load_app_settings()


# --- ATTACHED DATABASES CONFIGURATION LOAD/SAVE HELPERS ---

def get_config_path():
    """Get the path to attached_databases.yaml, using /config if running in Docker, otherwise config/attached_databases.yaml."""
    if os.path.exists('/config') and os.path.isdir('/config'):
        return '/config/attached_databases.yaml'
    if not os.path.exists('config'):
        os.makedirs('config', exist_ok=True)
    return 'config/attached_databases.yaml'

def load_attached_databases_for_connection(conn):
    """Load and attach all databases stored in attached_databases.yaml to the given connection."""
    config_path = get_config_path()
    if not os.path.exists(config_path):
        return
    try:
        import yaml
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        if not config or 'databases' not in config:
            return
        
        for db in config.get('databases', []):
            db_name = db.get('name')
            db_type = db.get('type')
            db_path = db.get('path')
            options = db.get('options', {})
            
            try:
                # Load extension if required
                if db_type == 'ducklake':
                    conn.execute("INSTALL ducklake; LOAD ducklake;")
                elif db_type == 'sqlite':
                    conn.execute("INSTALL sqlite; LOAD sqlite;")
                elif db_type == 'postgres':
                    conn.execute("INSTALL postgres; LOAD postgres;")
                elif db_type == 'mysql':
                    conn.execute("INSTALL mysql; LOAD mysql;")
                
                # Construct attach SQL
                if db_type == 'ducklake':
                    data_path = options.get('data_path', 'data_parquet/')
                    sql = f"ATTACH 'ducklake:{db_path}' AS {db_name} (DATA_PATH '{data_path}');"
                elif db_type == 'sqlite':
                    sql = f"ATTACH '{db_path}' AS {db_name} (TYPE sqlite);"
                elif db_type == 'postgres':
                    sql = f"ATTACH '{db_path}' AS {db_name} (TYPE postgres);"
                elif db_type == 'mysql':
                    sql = f"ATTACH '{db_path}' AS {db_name} (TYPE mysql);"
                else: # duckdb
                    sql = f"ATTACH '{db_path}' AS {db_name};"
                
                conn.execute(sql)
                print(f"INFO: Successfully auto-attached {db_type} database '{db_name}' from config.")
            except Exception as e:
                print(f"WARNING: Failed to auto-attach database '{db_name}' ({db_type}): {e}")
    except Exception as e:
        print(f"ERROR: Failed to load {config_path}: {e}")

def save_attached_database(db_name, db_type, db_path, data_path=None):
    """Save an attached database to the configuration file attached_databases.yaml."""
    config_path = get_config_path()
    import yaml
    
    config = {'databases': []}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                loaded = yaml.safe_load(f)
                if loaded and isinstance(loaded, dict) and 'databases' in loaded:
                    config = loaded
        except Exception as e:
            print(f"WARNING: Failed to read config file for saving: {e}")
            
    # Remove existing entry if it shares the name to avoid duplicates
    config['databases'] = [db for db in config['databases'] if db.get('name') != db_name]
    
    new_entry = {
        'name': db_name,
        'type': db_type,
        'path': db_path
    }
    if db_type == 'ducklake' and data_path:
        new_entry['options'] = {'data_path': data_path}
        
    config['databases'].append(new_entry)
    
    try:
        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f, default_flow_style=False)
        return True
    except Exception as e:
        print(f"ERROR: Failed to write to {config_path}: {e}")
        return False

def remove_attached_database(db_name):
    """Remove a database entry from the configuration file attached_databases.yaml."""
    config_path = get_config_path()
    if not os.path.exists(config_path):
        return False
    import yaml
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        if not config or 'databases' not in config:
            return False
            
        initial_len = len(config['databases'])
        config['databases'] = [db for db in config['databases'] if db.get('name') != db_name]
        
        if len(config['databases']) == initial_len:
            return False # Nothing removed
            
        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f, default_flow_style=False)
        return True
    except Exception as e:
        print(f"ERROR: Failed to remove db entry: {e}")
        return False

def format_column_projection_query(cols, fq_name, limit=100):
    """
    Formats the injected SQL query:
    - SELECT on the first line.
    - One or more columns per line depending on character length, staying within the editor width (~80 chars).
    - FROM fq_name on its own line.
    - LIMIT limit; on the last line.
    """
    col_names = [f'"{c[0]}"' for c in cols]
    max_line_width = 80
    lines = ["SELECT"]
    
    current_line = []
    current_len = 4  # Indent width
    
    for i, col in enumerate(col_names):
        is_last = (i == len(col_names) - 1)
        suffix = "" if is_last else ","
        col_with_suffix = col + suffix
        col_len = len(col_with_suffix)
        
        # If adding this column to the current line exceeds 80 characters, start a new line
        if current_line and (current_len + 2 + col_len > max_line_width):
            lines.append("    " + ", ".join(current_line) + ",")
            current_line = [col]
            current_len = 4 + len(col)
        else:
            current_line.append(col)
            if len(current_line) == 1:
                current_len = 4 + len(col)
            else:
                current_len += 2 + len(col)  # account for ", "
                
    if current_line:
        lines.append("    " + ", ".join(current_line))
        
    lines.append(f"FROM {fq_name}")
    lines.append(f"LIMIT {limit};")
    
    return "\n".join(lines)

def list_json_unnesting_templates():
    """
    Scans /templates and templates folders for valid JSON unnesting template files (*.yaml, *.yml, *.json).
    Returns a dictionary of {file_path: display_name}.
    """
    templates = {}
    dirs = ['/templates', 'templates']
    for d in dirs:
        if os.path.exists(d) and os.path.isdir(d):
            try:
                for file_name in os.listdir(d):
                    if file_name.endswith(('.yaml', '.yml', '.json')):
                        full_path = os.path.join(d, file_name)
                        try:
                            import yaml
                            with open(full_path, 'r') as f:
                                tmpl = yaml.safe_load(f)
                            if isinstance(tmpl, dict) and 'name' in tmpl and 'query_template' in tmpl:
                                templates[full_path] = f"📁 {tmpl['name']} ({file_name})"
                        except Exception:
                            pass
            except Exception:
                pass
    return templates

# --- APPLICATION PAGE DEFINITION ---
def get_main_db_path():
    """Get the path to the main DuckDB file, using /databases if running in Docker, otherwise databases/your_duckdb_file.duckdb."""
    if os.path.exists('/databases') and os.path.isdir('/databases'):
        return '/databases/your_duckdb_file.duckdb'
    if not os.path.exists('databases'):
        os.makedirs('databases', exist_ok=True)
    return 'databases/your_duckdb_file.duckdb'

DB_NAME = get_main_db_path()

@ui.page('/')
def index():
    global DB_NAME
    ui.query('.nicegui-content').classes('p-0 gap-0')
    # Scoping variables for sidebar explorers
    schema_filter_input = None
    snippet_category_toggle = None
    save_query_category_select = None
    export_db_select = None
    
    # Enable Tailwind glassmorphism and general layout styling
    ui.add_head_html("""
        <style>
            body, html {
                margin: 0 !important;
                padding: 0 !important;
                height: 100vh !important;
                width: 100vw !important;
                overflow: hidden !important;
            }
            .custom-header {
                background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
                border-bottom: 2px solid #334155;
            }
            .sidebar-card {
                border-right: 1px solid #e2e8f0;
            }
            .body--dark .sidebar-card {
                border-right: 1px solid #1e293b;
            }
            .glass-card {
                backdrop-filter: blur(10px);
                border: 1px solid rgba(226, 232, 240, 0.8);
            }
            .body--dark .glass-card {
                border: 1px solid rgba(30, 41, 59, 0.8);
            }
            .body--dark .q-tree .q-icon {
                color: #818cf8 !important;
            }
            .body--dark .q-tree,
            .body--dark .q-tree__node-header,
            .body--dark .q-tree__node-header-content,
            .body--dark .q-tree__node-label,
            .body--dark .q-tree__label,
            .body--dark .q-tree div,
            .body--dark .q-tree span {
                color: #e2e8f0 !important;
            }
            .dark-bg-panel {
                background-color: #ffffff;
                transition: background-color 0.3s, border-color 0.3s;
            }
            .body--dark .dark-bg-panel {
                background-color: #0f172a !important;
                border-color: #1e293b !important;
            }
            .dark-bg-flat {
                background-color: #f8fafc;
                transition: background-color 0.3s;
            }
            .body--dark .dark-bg-flat {
                background-color: #0f172a !important;
            }
            
            /* --- CodeMirror Light/Dark Theme Sync --- */
            .cm-editor {
                background-color: #ffffff !important;
                color: #0f172a !important;
                border: 1px solid #cbd5e1 !important;
                border-radius: 4px;
            }
            .cm-editor .cm-scroller {
                background-color: #ffffff !important;
            }
            .cm-editor .cm-content {
                color: #0f172a !important;
            }
            .cm-editor .cm-gutters {
                background-color: #f1f5f9 !important;
                color: #64748b !important;
                border-right: 1px solid #cbd5e1 !important;
            }

            .body--dark .cm-editor {
                background-color: #0f172a !important;
                color: #f8fafc !important;
                border: 1px solid #1e293b !important;
            }
            .body--dark .cm-editor .cm-scroller {
                background-color: #0f172a !important;
            }
            .body--dark .cm-editor .cm-content {
                color: #f8fafc !important;
            }
            .body--dark .cm-editor .cm-gutters {
                background-color: #1e293b !important;
                color: #94a3b8 !important;
                border-right: 1px solid #334155 !important;
            }

             /* --- Results Layout and Data Grid Scroll --- */
             .q-tab-panels {
                 background-color: transparent !important;
                 height: 100% !important;
             }
             .q-panel.scroll {
                 height: 100% !important;
                 overflow: hidden !important;
             }
             .q-tab-panel {
                 padding: 0 !important;
             }

             /* --- Q-Table Scroll & Fit --- */
             .q-table__container {
                 height: 100% !important;
                 display: flex !important;
                 flex-direction: column !important;
                 flex-wrap: nowrap !important;
             }
             .q-table__middle {
                 flex-grow: 1 !important;
                 height: 100% !important;
                 max-height: 100% !important;
             }

             /* --- Sticky Table Header --- */
             .q-table thead tr th {
                 position: sticky !important;
                 z-index: 2 !important;
                 top: 0 !important;
                 background-color: #ffffff !important;
             }
             .body--dark .q-table thead tr th {
                 background-color: #0f172a !important;
                 color: #cbd5e1 !important;
             }
         </style>
        <script>
            document.addEventListener('keydown', function(e) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    const target = e.target;
                    if (target && (target.tagName === 'TEXTAREA' || target.closest('.cm-editor') || target.closest('.CodeMirror'))) {
                        e.preventDefault();
                    }
                }
            }, true);
        </script>
    """)

    # Session database connection
    explorer = DuckDBExplorer(DB_NAME)
    
    # Load and attach configured databases from yaml
    load_attached_databases_for_connection(explorer.conn)
    
    # Pre-install and load the DuckLake extension on database connection
    try:
        explorer.conn.execute("INSTALL ducklake; LOAD ducklake;")
    except Exception as e:
        print(f"INFO: DuckLake extension could not be pre-loaded: {e}")
    
    # Drop any stale test views from previous runs
    try:
        explorer.conn.execute("DROP VIEW IF EXISTS external_sales;")
    except Exception:
        pass
    
    # State variables per user connection
    saved_queries_filter = None
    query_history = [
        "SELECT * FROM sales_transactions ORDER BY transaction_date DESC LIMIT 100;",
        "SELECT category, SUM(total_amount) AS revenue, COUNT(*) AS count FROM sales_transactions s JOIN product_inventory p ON s.product_id = p.product_id GROUP BY category ORDER BY revenue DESC;",
        "SELECT payment_method, COUNT(*) AS count, SUM(total_amount) AS total_revenue FROM sales_transactions GROUP BY payment_method ORDER BY total_revenue DESC;",
        "SELECT loyalty_tier, AVG(total_amount) AS avg_spent FROM sales_transactions t JOIN customer_profiles c ON t.customer_id = c.customer_id GROUP BY loyalty_tier ORDER BY avg_spent DESC;"
    ]
    current_results = {'columns': [], 'rows': []}

    # Helper function for bytes export
    def get_csv_bytes(columns, rows):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(col, '') for col in columns])
        return output.getvalue().encode('utf-8')

    # --- LIVE LINTER BACKGROUND DEBOUNCED VALIDATOR ---
    validation_task = None

    async def validate_sql_on_change(e):
        app.storage.user['last_query'] = e.value
        nonlocal validation_task
        if validation_task is not None:
            validation_task.cancel()
        validation_task = asyncio.create_task(debounced_validation(e.value))

    async def debounced_validation(sql_str):
        await asyncio.sleep(0.35)
        sql = sql_str.strip()
        if not sql:
            linter_icon.name = 'info'
            linter_icon.color = 'slate'
            linter_label.text = 'No SQL query written'
            linter_label.classes(replace='text-xs font-mono text-slate-500')
            return
            
        loop = asyncio.get_event_loop()
        def run_explain():
            try:
                chk_conn = duckdb.connect(explorer.db_file)
                try:
                    chk_conn.execute(f"EXPLAIN {sql}")
                    return None
                finally:
                    chk_conn.close()
            except Exception as ex:
                return str(ex)
                
        err = await loop.run_in_executor(None, run_explain)
        if err is None:
            linter_icon.name = 'check_circle'
            linter_icon.color = 'emerald'
            linter_label.text = 'SQL Syntax Valid'
            linter_label.classes(replace='text-xs font-mono text-emerald-600 dark:text-emerald-400')
        else:
            linter_icon.name = 'error'
            linter_icon.color = 'rose'
            short_err = err.split('\n')[0]
            if len(short_err) > 85:
                short_err = short_err[:85] + "..."
            linter_label.text = f"Syntax Error: {short_err}"
            linter_label.classes(replace='text-xs font-mono text-rose-600 dark:text-rose-400')

    # --- SAVED QUERIES LIBRARY CALLBACKS ---
    def open_save_query_dialog():
        sql = sql_editor.value.strip()
        if not sql:
            ui.notify('Cannot save an empty query!', type='warning')
            return
        save_query_dialog.open()

    async def handle_save_query():
        name = save_query_name_input.value.strip()
        desc = save_query_desc_input.value.strip()
        category = save_query_category_select.value
        sql = sql_editor.value.strip()
        if not name:
            ui.notify('Please enter a query name!', type='warning')
            return
        success = explorer.save_custom_query(name, desc, sql, category)
        if success:
            ui.notify(f'Query "{name}" saved successfully!', type='success')
            save_query_dialog.close()
            save_query_name_input.value = ''
            save_query_desc_input.value = ''
            save_query_category_select.value = 'Analytical'
            refresh_saved_queries_list()
        else:
            ui.notify('Failed to save query. See system logs.', type='negative')

    def run_snippet_immediately(sql_code):
        sql_editor.value = sql_code
        run_editor_query()
        ui.notify("Snippet executed successfully", type='info')

    def copy_snippet_to_clipboard(sql_code):
        ui.run_javascript(f"navigator.clipboard.writeText({repr(sql_code)})")
        ui.notify("SQL code copied to clipboard!", type='positive')

    def refresh_saved_queries_list():
        saved_queries_container.clear()
        with saved_queries_container:
            queries = explorer.list_saved_queries()
            if not queries:
                ui.label('No saved queries yet. Click "Save Query" to create one.').classes('text-xs text-slate-400 text-center py-4 w-full font-normal')
                return
                
            # Filter the queries based on the user search keyword and category toggle
            filter_text = saved_queries_filter.value.strip().lower() if saved_queries_filter and saved_queries_filter.value else ""
            selected_cat = snippet_category_toggle.value if snippet_category_toggle else "All"
            
            if filter_text or selected_cat != 'All':
                queries = [
                    q for q in queries 
                    if (not filter_text or (
                        filter_text in q['name'].lower() 
                        or (q['description'] and filter_text in q['description'].lower())
                        or filter_text in q['sql_code'].lower()
                    )) and (selected_cat == 'All' or q.get('category', 'Analytical') == selected_cat)
                ]
                if not queries:
                    ui.label('No matching SQL snippets.').classes('text-xs text-slate-400 text-center py-4 w-full font-normal')
                    return

            for q in queries:
                q_id = q['id']
                q_name = q['name']
                q_desc = q['description']
                q_sql = q['sql_code']
                q_cat = q.get('category', 'Analytical')
                
                # Define category badge colors
                if q_cat == 'Analytical':
                    cat_color = 'indigo'
                elif q_cat == 'Utility':
                    cat_color = 'amber'
                else:
                    cat_color = 'teal'
                
                with ui.card().classes('w-full p-2.5 border rounded shadow-none hover:bg-slate-50 dark:hover:bg-slate-900 transition gap-1.5 flex-none').style('border-color: var(--q-slate-200);'):
                    # Top section: Category Badge, Name and Description (Full width)
                    with ui.column().classes('w-full gap-0.5'):
                        with ui.row().classes('w-full items-center gap-1.5 no-wrap'):
                            ui.badge(q_cat, color=cat_color).classes('text-[8px] py-0.5 px-1 flex-none')
                            ui.label(q_name).classes('text-xs font-bold text-slate-800 dark:text-slate-200 break-words whitespace-normal')
                        if q_desc:
                            ui.label(q_desc).classes('text-[10px] text-slate-400 font-normal break-words whitespace-normal')
                    
                    # Handlers and Bottom Action row
                    def make_load_handler(code=q_sql):
                        return lambda _: load_history_query(code)
                    def make_run_handler(code=q_sql):
                        return lambda _: run_snippet_immediately(code)
                    def make_copy_handler(code=q_sql):
                        return lambda _: copy_snippet_to_clipboard(code)
                    def make_delete_handler(query_id=q_id, query_name=q_name):
                        return lambda _: confirm_delete_query(query_id, query_name)
                        
                    with ui.row().classes('w-full justify-between items-center mt-1 pt-1.5 border-t border-slate-100 dark:border-slate-800/50'):
                        with ui.row().classes('items-center gap-1'):
                            ui.button(icon='play_arrow', on_click=make_run_handler()).props('flat dense size=sm color=positive').classes('p-0.5').tooltip('Execute snippet')
                            ui.button(icon='arrow_forward', on_click=make_load_handler()).props('flat dense size=sm color=primary').classes('p-0.5').tooltip('Load to editor')
                            ui.button(icon='content_copy', on_click=make_copy_handler()).props('flat dense size=sm color=secondary').classes('p-0.5').tooltip('Copy SQL')
                        ui.button(icon='delete', on_click=make_delete_handler()).props('flat dense size=sm color=negative').classes('p-0.5').tooltip('Delete snippet')

    def confirm_delete_query(query_id, query_name):
        with ui.dialog() as dialog, ui.card():
            ui.label('Delete Saved Query?').classes('text-lg font-bold text-slate-800 dark:text-white')
            ui.label(f'Are you sure you want to delete "{query_name}"? This action cannot be undone.').classes('text-sm text-slate-500 my-2')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Delete', color='negative', on_click=lambda: perform_delete_query(dialog, query_id))
        dialog.open()

    def perform_delete_query(dialog, query_id):
        dialog.close()
        if explorer.delete_saved_query(query_id):
            ui.notify('Saved query deleted successfully.', type='success')
            refresh_saved_queries_list()
        else:
            ui.notify('Failed to delete query.', type='negative')

    # --- INTERACTIVE VISUAL QUERY BUILDER CALLBACKS ---
    qb_checkboxes = {}

    def populate_builder_tables():
        try:
            # Query active databases from duckdb_databases
            db_rows = explorer.conn.execute("SELECT database_name FROM duckdb_databases").fetchall()
            dbs = [row[0] for row in db_rows if row[0] not in ('system', 'temp') and not row[0].startswith('__')]
            
            qb_db_select.options = {db: db for db in dbs}
            qb_db_select.value = None
            qb_db_select.update()
            
            qb_table_select.options = []
            qb_table_select.value = None
            qb_table_select.update()
            
            qb_columns_container.clear()
            qb_checkboxes.clear()
        except Exception as e:
            print(f"Error populating builder databases: {e}")

    def update_builder_tables_for_db(db_name):
        if not db_name:
            qb_table_select.options = []
            qb_table_select.value = None
            qb_table_select.update()
            qb_columns_container.clear()
            qb_checkboxes.clear()
            return
            
        try:
            # Query schemas and tables for selected database
            query = f"""
                SELECT schema_name, table_name 
                FROM duckdb_tables 
                WHERE database_name = '{db_name}'
                UNION ALL
                SELECT schema_name, view_name AS table_name 
                FROM duckdb_views 
                WHERE database_name = '{db_name}'
                ORDER BY schema_name, table_name;
            """
            rows = explorer.conn.execute(query).fetchall()
            # Format options as "schema.table"
            table_options = [f"{schema}.{table}" for schema, table in rows]
            
            qb_table_select.options = table_options
            qb_table_select.value = None
            qb_table_select.update()
            
            qb_columns_container.clear()
            qb_checkboxes.clear()
        except Exception as e:
            print(f"Error loading tables for builder db {db_name}: {e}")
            qb_table_select.options = []
            qb_table_select.value = None
            qb_table_select.update()
            qb_columns_container.clear()
            qb_checkboxes.clear()

    def handle_builder_table_change(table_path):
        if not table_path or not qb_db_select.value:
            qb_columns_container.clear()
            qb_checkboxes.clear()
            qb_order_select.options = []
            qb_filter_col.options = []
            qb_order_select.update()
            qb_filter_col.update()
            return
            
        try:
            # Split Table Path "schema.table"
            parts = table_path.split('.')
            schema_name = parts[0]
            table_name = parts[1]
            db_name = qb_db_select.value
            
            # Clear existing checkboxes
            qb_columns_container.clear()
            qb_checkboxes.clear()
            
            # Get columns scoped by database and schema
            cols = explorer.list_columns_with_types(table_name, database=db_name, schema=schema_name)
            col_names = [c[0] for c in cols]
            
            qb_order_select.options = ['(None)'] + col_names
            qb_order_select.value = '(None)'
            qb_order_select.update()
            
            qb_filter_col.options = ['(None)'] + col_names
            qb_filter_col.value = '(None)'
            qb_filter_col.update()
            
            with qb_columns_container:
                def toggle_all(e):
                    for cb in qb_checkboxes.values():
                        cb.value = e.value
                ui.checkbox('Select All', value=True, on_change=toggle_all).classes('text-xs')
                for c_name, c_type in cols:
                    cb = ui.checkbox(f"{c_name}", value=True).classes('text-xs')
                    qb_checkboxes[c_name] = cb
        except Exception as e:
            print(f"Error loading builder columns for {table_path}: {e}")
            qb_columns_container.clear()
            qb_checkboxes.clear()

    def reset_query_builder():
        qb_db_select.value = None
        qb_db_select.update()
        qb_table_select.options = []
        qb_table_select.value = None
        qb_table_select.update()
        qb_order_select.value = '(None)'
        qb_dir_select.value = 'ASC'
        qb_limit_input.value = 100
        qb_filter_col.value = '(None)'
        qb_filter_op.value = '='
        qb_filter_val.value = ''
        qb_columns_container.clear()
        qb_checkboxes.clear()
        ui.notify('Visual Query Builder reset', type='info')

    def generate_builder_sql(run_query=False):
        db = qb_db_select.value
        tbl_path = qb_table_select.value
        if not db or not tbl_path:
            ui.notify('Please select both a database and a table first!', type='warning')
            return
            
        parts = tbl_path.split('.')
        schema = parts[0]
        tbl = parts[1]
        
        selected_cols = [c_name for c_name, cb in qb_checkboxes.items() if cb.value]
        if not selected_cols:
            ui.notify('Please select at least one column!', type='warning')
            return
            
        # Fully qualified table name
        fq_tbl = f"{db}.{schema}.{tbl}"
        
        cols_str = ", ".join([f'"{c}"' for c in selected_cols])
        sql = f"SELECT {cols_str}\nFROM {fq_tbl}"
        
        filt_col = qb_filter_col.value
        if filt_col and filt_col != '(None)':
            op = qb_filter_op.value
            val = qb_filter_val.value.strip()
            if op in ['IS NULL', 'IS NOT NULL']:
                sql += f"\nWHERE \"{filt_col}\" {op}"
            elif val:
                try:
                    float(val)
                    sql += f"\nWHERE \"{filt_col}\" {op} {val}"
                except ValueError:
                    escaped_val = val.replace("'", "''")
                    if op == 'LIKE':
                        sql += f"\nWHERE \"{filt_col}\" LIKE '%{escaped_val}%'"
                    else:
                        sql += f"\nWHERE \"{filt_col}\" {op} '{escaped_val}'"
            else:
                ui.notify(f"Filter column '{filt_col}' selected, but no filter value was specified. Skipping filter.", type='warning')
                
        ord_col = qb_order_select.value
        if ord_col and ord_col != '(None)':
            direction = qb_dir_select.value
            sql += f"\nORDER BY \"{ord_col}\" {direction}"
            
        limit = qb_limit_input.value
        if limit is not None and limit > 0:
            sql += f"\nLIMIT {int(limit)}"
            
        sql += ";"
        sql_editor.value = sql
        query_builder_expansion.value = False
        if run_query:
            ui.notify('SQL Generated & Executing!', type='success')
            run_editor_query()
        else:
            ui.notify('SQL Generated and loaded to Editor!', type='info')

    # Global Screen Wrapper
    with ui.column().classes('w-full h-screen gap-0 flex-nowrap overflow-hidden').style('margin: 0; padding: 0;'):
        # Global Premium Tab Bar
        with ui.row().classes('w-full items-center justify-between no-wrap bg-slate-900 dark:bg-slate-950 text-white px-4 border-b border-slate-700 dark:border-slate-800').style('height: 48px;'):
            with ui.row().classes('items-center gap-2 no-wrap flex-none'):
                ui.icon('database', color='primary').classes('text-2xl')
                ui.label('DuckDB Studio').classes('text-lg font-bold text-white')
            
            # Retrieve last selected tab or default to 'Explorer'
            try:
                last_tab = app.storage.user.get('active_tab', 'Explorer')
            except Exception:
                last_tab = 'Explorer'
            if last_tab not in ['Explorer', 'JupyterLab', 'dbt Workbench', 'Code Editor', 'Extensions', 'Database Tools', 'API Endpoints', 'API Docs & Explorer', 'Scheduler', 'Settings']:
                last_tab = 'Explorer'
                
            with ui.tabs(value=last_tab, on_change=lambda e: handle_tab_change_global(e.value)).props('inline-label dense align=right').classes('text-white flex-grow') as tabs:
                studio_tab = ui.tab(name='Explorer', label='', icon='img:/explorer_colored.svg').tooltip('Explorer (SQL & Schema)')
                jupyter_tab = ui.tab(name='JupyterLab', label='', icon='img:/jupyter_orange.svg').tooltip('JupyterLab Notebooks')
                dbt_tab = ui.tab(name='dbt Workbench', label='', icon='img:/dbt_orange.svg').tooltip('dbt Workbench')
                editor_tab = ui.tab(name='Code Editor', label='', icon='img:/vscode_blue.svg').tooltip('Code Editor (VS Code)')
                extensions_tab = ui.tab(name='Extensions', label='', icon='img:/extensions_teal.svg').tooltip('Extensions Manager')
                db_tools_tab = ui.tab(name='Database Tools', label='', icon='img:/db_tools_colored.svg').tooltip('Database Tools & Seeding')
                api_creator_tab = ui.tab(name='API Endpoints', label='', icon='img:/api_endpoint_colored.svg').tooltip('API Endpoints Creator')
                api_docs_tab = ui.tab(name='API Docs & Explorer', label='', icon='img:/swagger_green.svg').tooltip('API Docs & Swagger UI')
                scheduler_tab = ui.tab(name='Scheduler', label='', icon='img:/scheduler_colored.svg').tooltip('Background Query Scheduler')
                settings_tab = ui.tab(name='Settings', label='', icon='img:/settings_colored.svg').tooltip('Studio Settings')
            
        studio_container = ui.row().classes('w-full no-wrap min-h-0 flex-grow').style('margin: 0; padding: 0;')
        jupyter_container = ui.column().classes('w-full min-h-0 flex-grow').style('margin: 0; padding: 0;')
        dbt_workbench_container = ui.column().classes('w-full min-h-0 flex-grow').style('margin: 0; padding: 0;')
        code_editor_container = ui.column().classes('w-full min-h-0 flex-grow').style('margin: 0; padding: 0;')
        extensions_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-4 flex-nowrap').style('margin: 0; padding: 0;')
        db_tools_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        api_creator_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        api_docs_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        scheduler_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        settings_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        
        # Build Database Tools Container Content
        with db_tools_container:
            # Header Card
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('items-center gap-3'):
                    ui.icon('construction', color='primary').classes('text-3xl')
                    ui.label('Database Utilities & Tools').classes('text-2xl font-black text-slate-800 dark:text-white')
                ui.label('Perform high-performance local data backups, restore catalog structures, and re-seed the core database tables with customizable record densities.').classes('text-sm text-slate-500 dark:text-slate-400')
            
            # Sub Cards Layout (Grid / Side-by-Side Cards)
            with ui.grid(columns=(1, 2)).classes('w-full gap-6 flex-none'):
                # CARD 1: EXPORT & BACKUP
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('backup', color='primary').classes('text-2xl')
                        ui.label('Visual Database Backup / Export').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')
                    ui.label('Export all catalog tables, structures, and schemas from the active database to a local directory in highly compressed Parquet format or standard CSV files.').classes('text-xs text-slate-400 leading-relaxed')
                    
                    # Controls
                    with ui.row().classes('w-full gap-3 flex-nowrap items-center'):
                        export_db_select = ui.select(
                            options={},
                            value=None,
                            label='Select Database'
                        ).props('dense outlined').style('width: 180px;')
                        
                        export_format_select = ui.select(
                            options=['PARQUET', 'CSV', 'SQL'],
                            value='PARQUET',
                            label='Export Format'
                        ).props('dense outlined').style('width: 140px;')
                        
                        export_path_input = ui.input(placeholder='Select or type output directory path...').props('dense outlined').classes('flex-grow')
                        
                        async def select_export_dir():
                            start_dir = '/shared' if os.path.exists('/shared') else os.path.abspath('.')
                            picker = local_file_picker(start_dir, upper_limit=None, multiple=False)
                            res = await picker
                            if res:
                                path = res[0]
                                export_path_input.set_value(os.path.dirname(path) if os.path.isfile(path) else path)
                                
                        ui.button(icon='folder_open', on_click=select_export_dir).props('dense outline').classes('p-2')
                    
                    ui.button('Run Backup / Export', icon='play_arrow', color='primary',
                              on_click=lambda: trigger_db_export(export_path_input.value, export_format_select.value, export_db_select.value)).props('elevated dense').classes('px-4 self-end mt-2')

                # CARD 2: IMPORT & RESTORE
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('restore', color='secondary').classes('text-2xl')
                        ui.label('Database Schema Import / Restore').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')
                    ui.label('Re-create catalog tables and load bulk data from a previously exported directory containing schema.sql and Parquet/CSV data formats.').classes('text-xs text-slate-400 leading-relaxed')
                    
                    # Controls
                    with ui.row().classes('w-full gap-3 flex-nowrap items-center'):
                        import_path_input = ui.input(placeholder='Select or type backup directory path...').props('dense outlined').classes('flex-grow')
                        
                        async def select_import_dir():
                            start_dir = '/shared' if os.path.exists('/shared') else os.path.abspath('.')
                            picker = local_file_picker(start_dir, upper_limit=None, multiple=False)
                            res = await picker
                            if res:
                                path = res[0]
                                import_path_input.set_value(os.path.dirname(path) if os.path.isfile(path) else path)
                                
                        ui.button(icon='folder_open', on_click=select_import_dir).props('dense outline').classes('p-2')
                    
                    ui.button('Run Import / Restore', icon='play_arrow', color='secondary',
                              on_click=lambda: trigger_db_import(import_path_input.value)).props('elevated dense').classes('px-4 self-end mt-2')

            # CARD 3: CUSTOMIZABLE SEEDING ENGINE
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4 flex-none'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('science', color='warning').classes('text-2xl')
                    ui.label('Synthetic Seeding Engine').classes('text-lg font-bold text-slate-800 dark:text-white')
                ui.separator().classes('opacity-50')
                
                with ui.row().classes('w-full items-center justify-between gap-6 flex-wrap'):
                    with ui.column().classes('gap-2'):
                        ui.label('Generate structured sales records, loyalty customer profiles, and stock categorisation lists using Fake Analytics Seeder.').classes('text-xs text-slate-400 leading-relaxed max-w-2xl')
                        
                        # Metrics breakdown
                        with ui.row().classes('gap-4 mt-2'):
                            with ui.row().classes('items-center gap-1.5'):
                                ui.label('Transactions:').classes('text-[10px] text-slate-400 font-semibold uppercase')
                                trans_badge = ui.badge('Checking...', color='indigo').classes('text-[10px]')
                            with ui.row().classes('items-center gap-1.5'):
                                ui.label('Customers:').classes('text-[10px] text-slate-400 font-semibold uppercase')
                                cust_badge = ui.badge('Checking...', color='indigo').classes('text-[10px]')
                            with ui.row().classes('items-center gap-1.5'):
                                ui.label('Inventory:').classes('text-[10px] text-slate-400 font-semibold uppercase')
                                invent_badge = ui.badge('Checking...', color='indigo').classes('text-[10px]')
                    
                    # Controls
                    with ui.row().classes('items-center gap-3'):
                        density_select = ui.select(
                            options={
                                '1000': '1,000 Rows (Light)',
                                '6500': '6,500 Rows (Standard)',
                                '15000': '15,000 Rows (Dense)'
                            },
                            value='6500',
                            label='Mock Data Density'
                        ).props('dense outlined').style('width: 220px;')
                        
                        ui.button('Reset & Custom Seed', icon='restart_alt', color='warning',
                                  on_click=lambda: trigger_custom_seed(density_select.value)).props('elevated dense').classes('px-4 py-2')
        
        # Build Extensions Container Content
        with extensions_container:
            # Header Banner Card
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('w-full justify-between items-center no-wrap flex-wrap gap-4'):
                    with ui.column().classes('gap-1'):
                        with ui.row().classes('items-center gap-3'):
                            ui.icon('extension', color='primary').classes('text-3xl')
                            ui.label('DuckDB Extensions Manager').classes('text-2xl font-black text-slate-800 dark:text-white')
                        ui.label('Search, install, and load official DuckDB extensions to unlock spatial, networking, format scanning, and advanced analytics features directly in your active database session.').classes('text-sm text-slate-500 dark:text-slate-400 max-w-3xl')
                    
                    # Refresh action button
                    ui.button('Refresh Extensions', icon='refresh', color='primary',
                              on_click=lambda: refresh_extensions_grid()).props('elevated dense').classes('px-3 flex-none')
            
            # Search & Quick Filters Card
            with ui.card().classes('w-full p-4 shadow-none border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('w-full items-center justify-between gap-4 flex-wrap'):
                    # Search Box
                    ext_search = ui.input(placeholder='Search by name or description...', 
                                          on_change=lambda _: refresh_extensions_grid()).props('outlined dense clearable').classes('w-full md:w-80 font-normal text-xs')
                    
                    # Quick Filter Toggle Buttons
                    with ui.row().classes('items-center gap-2'):
                        ui.label('Quick Filters:').classes('text-xs text-slate-400 font-semibold uppercase')
                        ext_filter = ui.toggle(
                            options={
                                'all': 'All Extensions',
                                'installed': 'Installed Only',
                                'loaded': 'Loaded Only',
                                'core': 'Official Core'
                            },
                            value='all',
                            on_change=lambda _: refresh_extensions_grid()
                        ).props('dense rounded unelevated toggle-color=primary').classes('text-xs')
            
            # Dynamic Extensions Grid (wrapped in scrollable column to fit viewport)
            extensions_grid_wrapper = ui.column().classes('w-full flex-grow overflow-auto min-h-0')
            with extensions_grid_wrapper:
                extensions_grid = ui.grid(columns=(1, 2, 3)).classes('w-full gap-4 mt-2 pb-6')
        
        # Build JupyterLab container content
        with jupyter_container:
            ui.element('iframe').props('id="jupyter-frame" sandbox="allow-scripts allow-same-origin allow-popups allow-forms allow-downloads allow-modals"').classes('w-full h-full border-none')
            ui.run_javascript('''
                (function() {
                    var host = window.location.hostname;
                    var port = window.location.port;
                    var proto = window.location.protocol;
                    var token = "analytics_secret";
                    var targetUrl;
                    if (host.endsWith('.localhost')) {
                        var baseDomain = host.substring(host.indexOf('.'));
                        targetUrl = proto + '//jupyter' + baseDomain + (port ? ':' + port : '') + '/lab?token=' + token;
                    } else {
                        targetUrl = proto + '//' + host + ':8889/lab?token=' + token;
                    }
                    document.getElementById("jupyter-frame").src = targetUrl;
                })();
            ''')

        # Build dbt Workbench container content
        with dbt_workbench_container:
            ui.element('iframe').props('id="dbt-workbench-frame" sandbox="allow-scripts allow-same-origin allow-popups allow-forms allow-downloads allow-modals"').classes('w-full h-full border-none')
            ui.run_javascript('''
                (function() {
                    var host = window.location.hostname;
                    var port = window.location.port;
                    var proto = window.location.protocol;
                    var targetUrl;
                    if (host.endsWith('.localhost')) {
                        var baseDomain = host.substring(host.indexOf('.'));
                        targetUrl = proto + '//workbench' + baseDomain + (port ? ':' + port : '');
                    } else {
                        targetUrl = proto + '//' + host + ':3000';
                    }
                    document.getElementById("dbt-workbench-frame").src = targetUrl;
                })();
            ''')

        # Build Code Editor container content
        with code_editor_container:
            ui.element('iframe').props('id="dbt-code-server-frame" sandbox="allow-scripts allow-same-origin allow-popups allow-forms allow-downloads allow-modals"').classes('w-full h-full border-none')
            ui.run_javascript('''
                (function() {
                    var host = window.location.hostname;
                    var port = window.location.port;
                    var proto = window.location.protocol;
                    var targetUrl;
                    if (host.endsWith('.localhost')) {
                        var baseDomain = host.substring(host.indexOf('.'));
                        targetUrl = proto + '//editor' + baseDomain + (port ? ':' + port : '');
                    } else {
                        targetUrl = proto + '//' + host + ':8443';
                    }
                    document.getElementById("dbt-code-server-frame").src = targetUrl;
                })();
            ''')

        # Build API Creator Container Content
        with api_creator_container:
            # Header Card
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('items-center gap-3'):
                    ui.icon('api', color='primary').classes('text-3xl')
                    ui.label('FastAPI Endpoint Creator').classes('text-2xl font-black text-slate-800 dark:text-white')
                ui.label('Instantly design, test, and expose high-performance HTTP REST API endpoints from raw SQL queries on-the-fly. Support query parameters via the $parameter_name notation in your queries.').classes('text-sm text-slate-500 dark:text-slate-400')
            
            # Sub Cards Layout (Grid / Side-by-Side Cards)
            with ui.grid(columns=(1, 2)).classes('w-full gap-6 flex-none'):
                # CARD 1: CREATE API ENDPOINT FORM
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('add_circle', color='primary').classes('text-2xl')
                        ui.label('Create API Endpoint').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')
                    
                    # Form Inputs
                    api_path_input = ui.input(label='Endpoint Path', placeholder='e.g., recent-sales').props('dense outlined clearable').classes('w-full')
                    api_desc_input = ui.input(label='Description', placeholder='e.g., Returns all sales with quantity >= $min_qty').props('dense outlined clearable').classes('w-full')
                    
                    with ui.column().classes('w-full gap-1'):
                        ui.label('SQL Query Source').classes('text-xs font-semibold text-slate-400')
                        api_sql_input = ui.textarea(placeholder='e.g., SELECT * FROM sales_transactions WHERE quantity >= $min_qty;').props('dense outlined autogrow').classes('w-full font-mono text-xs').style('min-height: 120px;')
                        with ui.row().classes('w-full justify-between items-center gap-2 no-wrap'):
                            ui.label('Use $parameter_name to capture dynamic query parameters (e.g. ?min_qty=10).').classes('text-[10px] text-slate-400 max-w-[60%]')
                            ui.button('Analyze Columns for Auto-Params', icon='analytics', on_click=lambda: handle_analyze_sql_columns()).props('dense outline size=sm color=secondary').classes('text-xs').tooltip('Paste a SELECT query, then click here to auto-generate optional filters!')
                            
                    column_selection_container = ui.column().classes('w-full gap-2 border border-dashed border-slate-300 dark:border-slate-700 rounded-lg p-3 my-1').style('display: none;')
                    
                    with ui.row().classes('w-full items-center justify-between mt-2 flex-wrap gap-2'):
                        api_security_toggle = ui.switch('Require JWT Token Authorization').classes('text-xs font-semibold text-slate-700 dark:text-slate-300')
                        api_rate_limit_input = ui.input(placeholder='Rate Limit (e.g., 10/minute)').props('outlined dense size=sm').classes('w-48 text-xs font-mono').tooltip('Optional custom limit per IP. Leave empty to use default limit.')
                    
                    ui.button('Create Endpoint', icon='bolt', color='primary',
                              on_click=lambda: handle_create_api_endpoint(api_path_input.value, api_desc_input.value, api_sql_input.value, api_security_toggle.value, api_rate_limit_input.value)).props('elevated dense').classes('px-4 self-end mt-2')

                # CARD 2: ACTIVE API ENDPOINTS
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center justify-between no-wrap w-full'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('check_circle', color='positive').classes('text-2xl')
                            ui.label('Exposed HTTP Endpoints').classes('text-lg font-bold text-slate-800 dark:text-white')
                        # Refresh button
                        ui.button(icon='refresh', on_click=lambda: refresh_api_endpoints_grid()).props('flat dense size=sm color=primary').classes('p-1')
                    ui.separator().classes('opacity-50')
                    
                    # Endpoints List Container
                    api_endpoints_list_container = ui.column().classes('w-full gap-4 overflow-auto').style('max-height: 480px;')
            
            # Telemetry Metrics Dashboard Container
            dashboard_container = ui.column().classes('w-full gap-6 mt-4 flex-none')

        # API Creator Helper Functions inside index()
        columns_checkboxes = {}

        def parse_table_from_sql(sql_str):
            import re
            # Match FROM followed by optional spaces, optional quotes, and a word
            # Support database.schema.table formats
            match = re.search(r'(?i)\bFROM\s+["\']?([a-zA-Z0-9_\.\-]+)["\']?', sql_str)
            if match:
                full_table_name = match.group(1)
                # Get just the table name at the end if it's database.schema.table
                table_name_only = full_table_name.split('.')[-1]
                return full_table_name, table_name_only
            return None, None

        def parse_selected_columns_with_aliases(sql_str):
            import re
            # Use (?is) so that dot (.) matches newlines in multi-line SELECT statements
            match = re.search(r'(?is)\bSELECT\s+(.+?)\s+\bFROM\b', sql_str)
            if not match:
                return None
            
            projection = match.group(1).strip()
            # Replace newlines, carriage returns, and tabs with single spaces
            projection = re.sub(r'\s+', ' ', projection)
            if projection == '*':
                return {'*': '*'}
                
            mapping = {}
            parts = projection.split(',')
            for part in parts:
                part = part.strip()
                # Match "expr AS alias" where alias is the last word
                as_match = re.search(r'(?i)(.+?)\s+AS\s+(\w+)', part)
                if as_match:
                    orig = as_match.group(1).strip('"\'`[] ')
                    alias = as_match.group(2).strip('"\'`[] ')
                    orig_clean = orig.split('.')[-1].strip('"\'`[] ')
                    mapping[orig_clean.lower()] = alias
                else:
                    # Space-separated: "col alias"
                    words = part.split()
                    if len(words) > 1:
                        orig = " ".join(words[:-1]).strip('"\'`[] ')
                        alias = words[-1].strip('"\'`[] ')
                        orig_clean = orig.split('.')[-1].strip('"\'`[] ')
                        mapping[orig_clean.lower()] = alias
                    else:
                        orig_clean = part.split('.')[-1].strip('"\'`[] ')
                        mapping[orig_clean.lower()] = orig_clean
            return mapping

        def handle_analyze_sql_columns():
            sql = api_sql_input.value.strip() if api_sql_input.value else ""
            if not sql:
                ui.notify('Please enter a SQL select query first!', type='warning')
                return
            
            full_tbl, tbl_only = parse_table_from_sql(sql)
            if not tbl_only:
                ui.notify('Could not parse a valid table name from the query (looking for FROM <table_name>).', type='warning')
                return
                
            try:
                # Query columns using explorer
                cols = explorer.list_columns_with_types(tbl_only)
                if not cols:
                    # Try with full table name just in case
                    cols = explorer.list_columns_with_types(full_tbl)
                    
                if not cols:
                    ui.notify(f"Could not fetch columns for table '{tbl_only}'. Make sure the table exists in the database.", type='warning')
                    return
                
                # Parse columns specified in the select projection
                proj_map = parse_selected_columns_with_aliases(sql)
                
                def generate_dynamic_where():
                    selected_cols = [c_name for c_name, cb in columns_checkboxes.items() if cb.value]
                    if not selected_cols:
                        ui.notify('No columns selected!', type='warning')
                        return
                        
                    sql = api_sql_input.value.strip()
                    # Strip trailing semicolon
                    has_semicolon = sql.endswith(';')
                    if has_semicolon:
                        sql = sql[:-1].strip()
                        
                    # Split trailing clauses (ORDER BY, LIMIT, OFFSET)
                    sql, trailing = split_sql_trailing_clauses(sql)
                    
                    # Create type mapping and alias-lookup
                    col_type_map = {c_name.lower(): c_type for c_name, c_type in cols}
                    inv_proj_map = {alias.lower(): orig for orig, alias in proj_map.items()} if proj_map else {}
                    
                    # Build parameter clauses
                    clauses = []
                    generate_ranges = range_switch.value
                    
                    for col in selected_cols:
                        # Find original column name to look up its data type
                        orig_col = inv_proj_map.get(col.lower(), col)
                        c_type = col_type_map.get(orig_col.lower(), "")
                        c_type_upper = c_type.upper()
                        is_numeric_or_date = any(t in c_type_upper for t in ['INT', 'DOUBLE', 'FLOAT', 'DECIMAL', 'REAL', 'NUMERIC', 'DATE', 'TIME', 'TIMESTAMP'])
                        
                        if generate_ranges and is_numeric_or_date:
                            clauses.append(f"  AND (${col}_eq IS NULL  OR \"{col}\" = ${col}_eq)")
                            clauses.append(f"  AND (${col}_gt IS NULL  OR \"{col}\" > ${col}_gt)")
                            clauses.append(f"  AND (${col}_gte IS NULL OR \"{col}\" >= ${col}_gte)")
                            clauses.append(f"  AND (${col}_lt IS NULL  OR \"{col}\" < ${col}_lt)")
                            clauses.append(f"  AND (${col}_lte IS NULL OR \"{col}\" <= ${col}_lte)")
                        else:
                            clauses.append(f"  AND (${col} IS NULL OR \"{col}\" = ${col})")
                        
                    # Check if WHERE exists (case-insensitive search)
                    import re
                    has_where = re.search(r'(?i)\bWHERE\b', sql)
                    
                    if has_where:
                        # Append clauses to the existing WHERE block
                        sql += "\n" + "\n".join(clauses)
                    else:
                        # Add WHERE 1=1 and then clauses
                        sql += "\nWHERE 1=1\n" + "\n".join(clauses)
                        
                    # Re-append trailing clauses
                    if trailing:
                        sql += "\n" + trailing.strip()
                        
                    if has_semicolon:
                        sql += ";"
                        
                    api_sql_input.value = sql
                    column_selection_container.style('display: none;')
                    ui.notify('Dynamic WHERE clause injected into your query!', type='success')
                
                # Show container
                column_selection_container.clear()
                column_selection_container.style('display: flex;')
                
                # Define nested drawing helper for toggling and alias resolution
                def draw_columns_grid(show_all):
                    grid_container.clear()
                    columns_checkboxes.clear()
                    
                    display_cols = []
                    if show_all:
                        # Display all columns. If a column has an alias in the query, use the alias!
                        for c_name, c_type in cols:
                            display_name = proj_map.get(c_name.lower(), c_name) if proj_map else c_name
                            display_cols.append((display_name, c_type))
                    else:
                        if proj_map:
                            if '*' in proj_map:
                                display_cols = [(c_name, c_type) for c_name, c_type in cols]
                            else:
                                # Only show columns present in projection mapping
                                for c_name, c_type in cols:
                                    if c_name.lower() in proj_map:
                                        alias_name = proj_map[c_name.lower()]
                                        display_cols.append((alias_name, c_type))
                        else:
                            display_cols = cols
                            
                    print(f"DEBUG_PARSER: show_all={show_all} display_cols={display_cols}", flush=True)
                    with grid_container:
                        for c_name, c_type in display_cols:
                            cb = ui.checkbox(f"{c_name} ({c_type})").classes('text-xs')
                            columns_checkboxes[c_name] = cb
                
                with column_selection_container:
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label(f"Parsed Table: {full_tbl}").classes('text-xs font-bold text-slate-700 dark:text-slate-300')
                        # Reactive show all switch
                        ui.switch('Show all columns', value=False, on_change=lambda e: draw_columns_grid(e.value)).classes('text-xs')
                        
                    range_switch = ui.switch('Enable range filters (>=, <=, etc.) for numeric & date columns', value=False).classes('text-xs font-medium text-slate-600 my-0.5')
                    ui.label("Select columns to add as optional dynamic API parameters:").classes('text-[10px] text-slate-400 -mt-1')
                    
                    grid_container = ui.grid(columns=2).classes('w-full gap-1')
                    
                    # Initial draw (False = restricted to selected columns)
                    draw_columns_grid(False)
                    
                    ui.button('Inject Dynamic Parameters', icon='auto_fix_high', color='secondary',
                               on_click=generate_dynamic_where).props('dense unelevated size=sm').classes('mt-2 self-end text-xs')

                ui.notify(f"Analyzed table '{tbl_only}' successfully!", type='info')
            except Exception as ex:
                ui.notify(f"Error analyzing columns: {ex}", type='negative')

        def handle_create_api_endpoint(endpoint_path, description, sql_code, security_enabled=False, rate_limit=None):
            if not endpoint_path or not endpoint_path.strip():
                ui.notify("Please specify a valid API endpoint path.", type='warning')
                return
            endpoint_path = endpoint_path.strip().strip('/')
            if not sql_code or not sql_code.strip():
                ui.notify("Please specify the SQL query source for this endpoint.", type='warning')
                return
                
            # Clean path from illegal characters
            import re
            if not re.match(r'^[a-zA-Z0-9_\-\/]+$', endpoint_path):
                ui.notify("Path can only contain alphanumeric characters, hyphens, underscores, and slashes.", type='warning')
                return
                
            try:
                import uuid, datetime
                # Check duplicate path
                dup = explorer.conn.execute("SELECT 1 FROM _duckdb_studio_api_endpoints WHERE path = ?", [endpoint_path]).fetchone()
                if dup:
                    ui.notify(f"Endpoint path '/api/{endpoint_path}' already exists. Please use a unique path.", type='negative')
                    return
                    
                rl_value = rate_limit.strip() if rate_limit and rate_limit.strip() else None
                
                explorer.conn.execute("""
                    INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at, security_enabled, rate_limit)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """, [
                    str(uuid.uuid4()),
                    endpoint_path,
                    description.strip() if description else '',
                    sql_code.strip(),
                    datetime.datetime.now(),
                    security_enabled,
                    rl_value
                ])
                ui.notify(f"API Endpoint '/api/{endpoint_path}' created successfully!", type='success')
                
                # Clear form inputs
                api_path_input.value = ''
                api_desc_input.value = ''
                api_sql_input.value = ''
                try:
                    api_security_toggle.value = False
                    api_rate_limit_input.value = ''
                except Exception:
                    pass
                
                refresh_api_endpoints_grid()
            except Exception as err:
                ui.notify(f"Failed to create endpoint: {err}", type='negative')

        def delete_api_endpoint(endpoint_id, endpoint_path):
            try:
                explorer.conn.execute("DELETE FROM _duckdb_studio_api_endpoints WHERE id = ?", [endpoint_id])
                ui.notify(f"API Endpoint '/api/{endpoint_path}' deleted successfully.", type='success')
                refresh_api_endpoints_grid()
            except Exception as err:
                ui.notify(f"Failed to delete endpoint: {err}", type='negative')

        def refresh_api_endpoints_grid():
            api_endpoints_list_container.clear()
            
            # Query aggregate metrics for endpoints
            metrics_map = {}
            try:
                explorer.conn.execute("""
                    CREATE TABLE IF NOT EXISTS _duckdb_studio_api_metrics (
                        endpoint_path VARCHAR,
                        timestamp TIMESTAMP,
                        latency_ms DOUBLE,
                        status_code INTEGER,
                        error_message VARCHAR
                    );
                """)
                m_rows = explorer.conn.execute("""
                    SELECT 
                        endpoint_path,
                        COUNT(*) as total_calls,
                        AVG(latency_ms) as avg_latency,
                        MIN(latency_ms) as min_latency,
                        MAX(latency_ms) as max_latency,
                        SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as total_errors
                    FROM _duckdb_studio_api_metrics
                    GROUP BY endpoint_path;
                """).fetchall()
                for m_path, m_calls, m_avg, m_min, m_max, m_errs in m_rows:
                    metrics_map[m_path] = {
                        'calls': m_calls,
                        'avg': m_avg,
                        'min': m_min,
                        'max': m_max,
                        'errors': m_errs,
                        'error_rate': (m_errs * 100.0 / m_calls) if m_calls > 0 else 0.0
                    }
            except Exception as e:
                print(f"DEBUG: Failed to query API metrics: {e}", flush=True)
            
            try:
                rows = explorer.conn.execute("SELECT id, path, description, sql_code, COALESCE(security_enabled, FALSE), rate_limit FROM _duckdb_studio_api_endpoints ORDER BY created_at DESC;").fetchall()
            except Exception as e:
                with api_endpoints_list_container:
                    ui.label(f"Failed to load endpoints: {e}").classes('text-xs text-negative')
                return
                
            if not rows:
                with api_endpoints_list_container:
                    with ui.column().classes('w-full items-center justify-center py-12 gap-2'):
                        ui.icon('cloud_off', color='grey').classes('text-5xl')
                        ui.label("No dynamic API endpoints active.").classes('text-sm text-slate-400 font-medium')
                        ui.label("Define a path and query in the creator form to expose your first REST API!").classes('text-xs text-slate-500')
                return
                
            with api_endpoints_list_container:
                for ep_id, ep_path, ep_desc, ep_sql, ep_secured, ep_rate_limit in rows:
                    with ui.card().classes('w-full p-4 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-3'):
                        # Top Row: GET Badge + Path
                        with ui.row().classes('w-full items-center justify-between no-wrap'):
                            with ui.row().classes('items-center gap-2 no-wrap'):
                                ui.badge('GET', color='positive').classes('text-[10px] font-bold px-2 py-0.5')
                                if ep_secured:
                                    ui.icon('lock', color='amber').classes('text-xs').tooltip('Requires JWT Authorization')
                                ui.label(f"/api/{ep_path}").classes('text-sm font-bold text-slate-800 dark:text-white truncate')
                                limit_str = ep_rate_limit if ep_rate_limit else f"{APP_SETTINGS.get('default_rate_limit', '5/minute')} (default)"
                                ui.badge(limit_str, color='info').classes('text-[10px] px-2 py-0.5').tooltip('Rate Limit')
                            
                            with ui.row().classes('items-center gap-1'):
                                ui.button(icon='edit', on_click=lambda _, i=ep_id, p=ep_path, d=ep_desc, s=ep_sql, sec=ep_secured, rl=ep_rate_limit: open_edit_api_dialog(i, p, d, s, sec, rl)).props('flat dense size=sm color=primary').classes('p-1').tooltip('Edit Endpoint')
                                ui.button(icon='delete', on_click=lambda _, i=ep_id, p=ep_path: delete_api_endpoint(i, p)).props('flat dense size=sm color=negative').classes('p-1').tooltip('Delete Endpoint')
                            
                        # Middle Row: Description
                        if ep_desc:
                            ui.label(ep_desc).classes('text-xs text-slate-400 font-normal leading-relaxed')
                            
                        # Telemetry Stats Badge/Bar
                        stats = metrics_map.get(ep_path, {
                            'calls': 0,
                            'avg': 0.0,
                            'min': 0.0,
                            'max': 0.0,
                            'errors': 0,
                            'error_rate': 0.0
                        })
                        with ui.row().classes('w-full items-center gap-4 text-[11px] bg-slate-50 dark:bg-slate-900/50 p-2 rounded-lg border border-slate-100 dark:border-slate-850'):
                            with ui.row().classes('items-center gap-1'):
                                ui.icon('analytics', color='primary', size='xs')
                                ui.label('Telemetry:').classes('font-bold text-slate-500')
                            with ui.row().classes('items-center gap-1'):
                                ui.label('Calls:').classes('text-slate-400')
                                ui.label(str(stats['calls'])).classes('font-bold text-slate-700 dark:text-slate-350')
                            with ui.row().classes('items-center gap-1'):
                                ui.label('Avg Latency:').classes('text-slate-400')
                                ui.label(f"{stats['avg']:.1f}ms").classes('font-bold text-slate-700 dark:text-slate-350')
                            with ui.row().classes('items-center gap-1'):
                                ui.label('Error Rate:').classes('text-slate-400')
                                err_color = 'rose-500' if stats['error_rate'] > 0 else 'emerald-500'
                                ui.label(f"{stats['error_rate']:.1f}%").classes(f'font-bold text-{err_color}')
                            
                        # Code Snippet: Monospace SQL code
                        with ui.expansion('Source Query SQL', icon='code').classes('w-full border border-slate-100 dark:border-slate-900 rounded-lg text-xs'):
                            ui.code(ep_sql, language='sql').classes('w-full text-[10px] rounded-lg p-2 dark:bg-slate-950')
                            
                        # Action row: Test / Copy full url
                        with ui.row().classes('w-full items-center justify-between border-t border-slate-100 dark:border-slate-900 pt-2 mt-1 flex-wrap gap-2'):
                            full_relative_path = f"/api/{ep_path}"
                            ui.label(full_relative_path).classes('text-[10px] font-semibold text-slate-400 truncate max-w-[200px]')
                            
                            with ui.row().classes('items-center gap-2'):
                                with ui.link(target=f'/api/{ep_path}', new_tab=True).style('text-decoration: none'):
                                    ui.button('Test Endpoint', icon='open_in_new').props('flat dense size=sm color=primary').classes('text-xs')
                                ui.button('Copy Path', icon='content_copy', on_click=lambda _, p=full_relative_path: ui.run_javascript(f"navigator.clipboard.writeText(window.location.origin + '{p}')")).props('flat dense size=sm color=secondary').classes('text-xs')
            
            # Keep API Docs explorer in perfect sync!
            refresh_api_docs_explorer()
            
            # Keep Telemetry Metrics Dashboard in perfect sync!
            refresh_metrics_dashboard()

        def refresh_metrics_dashboard():
            dashboard_container.clear()
            
            # Query global metrics
            try:
                explorer.conn.execute("""
                    CREATE TABLE IF NOT EXISTS _duckdb_studio_api_metrics (
                        endpoint_path VARCHAR,
                        timestamp TIMESTAMP,
                        latency_ms DOUBLE,
                        status_code INTEGER,
                        error_message VARCHAR
                    );
                """)
                
                global_stats = explorer.conn.execute("""
                    SELECT 
                        COUNT(*),
                        AVG(latency_ms),
                        SUM(CASE WHEN status_code < 400 THEN 1 ELSE 0 END)
                    FROM _duckdb_studio_api_metrics;
                """).fetchone()
                
                total_calls = global_stats[0] if global_stats[0] is not None else 0
                avg_latency = global_stats[1] if global_stats[1] is not None else 0.0
                success_count = global_stats[2] if global_stats[2] is not None else 0
                
                success_rate = (success_count * 100.0 / total_calls) if total_calls > 0 else 100.0
                
                active_endpoints_count = explorer.conn.execute("SELECT COUNT(*) FROM _duckdb_studio_api_endpoints;").fetchone()[0]
                
            except Exception as e:
                print(f"DEBUG: Failed to load global API metrics: {e}", flush=True)
                total_calls = 0
                avg_latency = 0.0
                success_rate = 100.0
                active_endpoints_count = 0

            with dashboard_container:
                with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-6'):
                    # Header row
                    with ui.row().classes('w-full items-center justify-between no-wrap'):
                        with ui.row().classes('items-center gap-3'):
                            ui.icon('analytics', color='primary').classes('text-2xl')
                            ui.label('Live API Telemetry & Performance Dashboard').classes('text-lg font-bold text-slate-800 dark:text-white')
                        
                        # Reset / Clear metrics button
                        def handle_clear_metrics():
                            try:
                                explorer.conn.execute("DELETE FROM _duckdb_studio_api_metrics;")
                                ui.notify('Telemetry logs cleared successfully!', type='positive')
                                refresh_api_endpoints_grid()
                            except Exception as ex:
                                ui.notify(f"Failed to clear telemetry: {ex}", type='negative')
                        
                        ui.button('Clear Telemetry Logs', icon='cleaning_services', on_click=handle_clear_metrics).props('outline dense color=negative size=sm').classes('px-3 text-xs')
                    
                    ui.separator().classes('opacity-50')
                    
                    # 4 KPI Cards Grid
                    with ui.grid(columns=(1, 4)).classes('w-full gap-4'):
                        # KPI 1: Active Endpoints
                        with ui.card().classes('p-4 border border-slate-100 dark:border-slate-800 rounded-xl bg-slate-50 dark:bg-slate-900/40 flex-row items-center gap-4'):
                            ui.icon('dns', color='primary').classes('text-3xl p-2 bg-indigo-50 dark:bg-indigo-950/50 rounded-lg')
                            with ui.column().classes('gap-0'):
                                ui.label('Active API Routes').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                                ui.label(str(active_endpoints_count)).classes('text-xl font-black text-slate-700 dark:text-slate-200')
                                
                        # KPI 2: Total Calls
                        with ui.card().classes('p-4 border border-slate-100 dark:border-slate-800 rounded-xl bg-slate-50 dark:bg-slate-900/40 flex-row items-center gap-4'):
                            ui.icon('call', color='secondary').classes('text-3xl p-2 bg-purple-50 dark:bg-purple-950/50 rounded-lg')
                            with ui.column().classes('gap-0'):
                                ui.label('Total Requests').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                                ui.label(f"{total_calls:,}").classes('text-xl font-black text-slate-700 dark:text-slate-200')

                        # KPI 3: Avg Latency
                        with ui.card().classes('p-4 border border-slate-100 dark:border-slate-800 rounded-xl bg-slate-50 dark:bg-slate-900/40 flex-row items-center gap-4'):
                            ui.icon('speed', color='warning').classes('text-3xl p-2 bg-amber-50 dark:bg-amber-950/50 rounded-lg')
                            with ui.column().classes('gap-0'):
                                ui.label('Avg Latency').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                                ui.label(f"{avg_latency:.1f} ms").classes('text-xl font-black text-slate-700 dark:text-slate-200')

                        # KPI 4: Success Rate
                        with ui.card().classes('p-4 border border-slate-100 dark:border-slate-800 rounded-xl bg-slate-50 dark:bg-slate-900/40 flex-row items-center gap-4'):
                            color_indicator = 'positive' if success_rate >= 95 else ('warning' if success_rate >= 80 else 'negative')
                            bg_class = 'bg-emerald-50 dark:bg-emerald-950/50' if success_rate >= 95 else 'bg-rose-50 dark:bg-rose-950/50'
                            ui.icon('health_and_safety', color=color_indicator).classes(f'text-3xl p-2 {bg_class} rounded-lg')
                            with ui.column().classes('gap-0'):
                                ui.label('Success Rate').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                                ui.label(f"{success_rate:.1f}%").classes('text-xl font-black text-slate-700 dark:text-slate-200')

                    # Metrics Details Table
                    ui.label('Endpoint Performance Analytics').classes('text-sm font-bold text-slate-700 dark:text-slate-300 mt-2')
                    
                    try:
                        detail_rows = explorer.conn.execute("""
                            SELECT 
                                m.endpoint_path,
                                COUNT(*) as calls,
                                AVG(m.latency_ms) as avg_lat,
                                MIN(m.latency_ms) as min_lat,
                                MAX(m.latency_ms) as max_lat,
                                SUM(CASE WHEN m.status_code < 400 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate,
                                MAX(m.timestamp) as last_called
                            FROM _duckdb_studio_api_metrics m
                            GROUP BY m.endpoint_path
                            ORDER BY calls DESC;
                        """).fetchall()
                    except Exception:
                        detail_rows = []
                        
                    if not detail_rows:
                        with ui.column().classes('w-full items-center justify-center py-8 border border-dashed border-slate-200 dark:border-slate-800 rounded-xl bg-slate-50/50 dark:bg-slate-900/20'):
                            ui.icon('hourglass_empty', color='grey').classes('text-3xl')
                            ui.label('No performance data logged yet.').classes('text-xs text-slate-400 font-medium mt-1')
                            ui.label('Hit your exposed API endpoints to see live stats populate here in real-time.').classes('text-[10px] text-slate-500')
                    else:
                        # Draw high quality interactive HTML/CSS table
                        with ui.element('div').classes('w-full overflow-x-auto border border-slate-100 dark:border-slate-800 rounded-lg'):
                            # Table structure
                            with ui.element('table').classes('w-full text-left border-collapse text-xs'):
                                # Table Header
                                with ui.element('thead').classes('bg-slate-100 dark:bg-slate-900 text-slate-500 font-bold uppercase tracking-wider text-[10px]'):
                                    with ui.element('tr'):
                                        ui.element('th').classes('p-3').text('Endpoint Route')
                                        ui.element('th').classes('p-3 text-center').text('Invocations')
                                        ui.element('th').classes('p-3 text-center').text('Avg Latency')
                                        ui.element('th').classes('p-3 text-center').text('Min / Max')
                                        ui.element('th').classes('p-3 text-center').text('Success Ratio')
                                        ui.element('th').classes('p-3 text-right').text('Last Triggered')
                                
                                # Table Body
                                with ui.element('tbody').classes('divide-y divide-slate-100 dark:divide-slate-850 text-slate-700 dark:text-slate-350 font-mono'):
                                    for path, calls, avg_lat, min_lat, max_lat, succ_rate, last_called in detail_rows:
                                        with ui.element('tr').classes('hover:bg-slate-50/50 dark:hover:bg-slate-900/50 transition-colors'):
                                            # Path
                                            ui.element('td').classes('p-3 font-bold text-slate-800 dark:text-slate-200').text(f"/api/{path}")
                                            # Calls
                                            ui.element('td').classes('p-3 text-center font-bold').text(f"{calls:,}")
                                            # Avg Latency
                                            ui.element('td').classes('p-3 text-center text-primary font-bold').text(f"{avg_lat:.1f} ms")
                                            # Min / Max
                                            ui.element('td').classes('p-3 text-center text-slate-500 text-[11px]').text(f"{min_lat:.1f}ms / {max_lat:.1f}ms")
                                            
                                            # Success Rate
                                            succ_color = 'text-emerald-500' if succ_rate >= 95 else ('text-amber-500' if succ_rate >= 80 else 'text-rose-500')
                                            ui.element('td').classes(f'p-3 text-center font-bold {succ_color}').text(f"{succ_rate:.1f}%")
                                            
                                            # Last Triggered
                                            time_str = last_called.strftime('%Y-%m-%d %H:%M:%S') if last_called else '-'
                                            ui.element('td').classes('p-3 text-right text-slate-400 text-[11px]').text(time_str)

        def refresh_api_docs_explorer():
            api_docs_container.clear()
            
            with api_docs_container:
                # Header Card
                with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('menu_book', color='primary').classes('text-3xl')
                        ui.label('Interactive API Docs & Explorer').classes('text-2xl font-black text-slate-800 dark:text-white')
                    ui.label('Inspect all active REST API microservices, dynamically test request payloads, and explore formatted metered responses in real-time.').classes('text-sm text-slate-500 dark:text-slate-400')
                
                try:
                    rows = explorer.conn.execute("SELECT id, path, description, sql_code, COALESCE(security_enabled, FALSE) FROM _duckdb_studio_api_endpoints ORDER BY created_at DESC;").fetchall()
                except Exception as e:
                    ui.label(f"Failed to load endpoints: {e}").classes('text-xs text-negative')
                    return
                    
                if not rows:
                    with ui.card().classes('w-full p-8 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col items-center justify-center gap-2'):
                        ui.icon('cloud_off', color='grey').classes('text-5xl')
                        ui.label("No active API endpoints to document.").classes('text-sm text-slate-400 font-medium')
                        ui.label("Go to 'API Endpoints' tab to create your first dynamic API!").classes('text-xs text-slate-500')
                    return
                    
                # Sub Cards List
                with ui.column().classes('w-full gap-4'):
                    for ep_id, ep_path, ep_desc, ep_sql, ep_secured in rows:
                        with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                            # Header Row: HTTP Method + Path
                            with ui.row().classes('w-full items-center justify-between no-wrap'):
                                with ui.row().classes('items-center gap-2 no-wrap'):
                                    ui.badge('GET', color='positive').classes('text-xs font-bold px-2 py-1')
                                    if ep_secured:
                                        ui.icon('lock', color='amber').classes('text-sm').tooltip('Requires JWT Authorization')
                                    ui.label(f"/api/{ep_path}").classes('text-base font-bold text-slate-800 dark:text-white')
                                    if ep_secured:
                                        ui.badge('JWT SECURED', color='amber').classes('text-[9px] font-bold px-1.5 py-0.5')
                                ui.label(ep_desc if ep_desc else 'No description provided').classes('text-xs text-slate-400 font-normal italic')
                                
                            ui.separator().classes('opacity-50')
                            
                            import re
                            placeholders = re.findall(r'\$([a-zA-Z0-9_]+)', ep_sql)
                            placeholders = list(dict.fromkeys(placeholders))
                            # Remove limit and offset if present in placeholders to avoid duplicates
                            placeholders = [p for p in placeholders if p.lower() not in ['limit', 'offset']]
                            
                            # Build interactive input fields dictionary
                            input_fields = {}
                            
                            with ui.row().classes('w-full gap-4 items-start flex-wrap'):
                                # Authorization Token for Secured APIs
                                if ep_secured:
                                    with ui.column().classes('gap-2').style('width: 250px;'):
                                        ui.label('Authorization Token').classes('text-xs font-bold text-amber-500 dark:text-amber-400')
                                        input_fields['__jwt__'] = ui.input(placeholder='Bearer <token>').props('outlined dense').classes('w-full font-mono text-xs')
                                        ui.label('HTTP Bearer Header').classes('text-[10px] text-slate-400 -mt-1')
                                        
                                # Standard Paging parameters
                                with ui.column().classes('gap-2').style('width: 140px;'):
                                    ui.label('limit').classes('text-xs font-bold text-slate-700 dark:text-slate-300')
                                    input_fields['limit'] = ui.input(placeholder='e.g., 100').props('outlined dense type=number').classes('w-full')
                                    ui.label('Query parameter').classes('text-[10px] text-slate-400 -mt-1')
                                    
                                with ui.column().classes('gap-2').style('width: 140px;'):
                                    ui.label('offset').classes('text-xs font-bold text-slate-700 dark:text-slate-300')
                                    input_fields['offset'] = ui.input(placeholder='e.g., 0').props('outlined dense type=number').classes('w-full')
                                    ui.label('Query parameter').classes('text-[10px] text-slate-400 -mt-1')
                                    
                                # Custom placeholders
                                for p in placeholders:
                                    with ui.column().classes('gap-2').style('width: 180px;'):
                                        ui.label(p).classes('text-xs font-bold text-indigo-500 dark:text-indigo-400')
                                        input_fields[p] = ui.input(placeholder=f'Value for ${p}').props('outlined dense').classes('w-full')
                                        ui.label('Dynamic query filter').classes('text-[10px] text-slate-400 -mt-1')
                                        
                            # Action Row
                            with ui.row().classes('w-full justify-end gap-2 pt-2'):
                                clear_btn = ui.button('Clear', color='grey').props('flat size=sm')
                                execute_btn = ui.button('Execute Request', icon='bolt', color='primary').props('elevated size=sm').classes('px-4')
                                
                            # Response Container (Hidden initially)
                            response_panel = ui.column().classes('w-full gap-3 mt-4 border border-slate-100 dark:border-slate-800 rounded-xl p-4 bg-slate-50 dark:bg-slate-950').style('display: none;')
                            
                            with response_panel:
                                # Stats row
                                with ui.row().classes('w-full justify-between items-center no-wrap'):
                                    with ui.row().classes('items-center gap-2'):
                                        status_badge = ui.badge('', color='positive').classes('text-xs font-bold px-2 py-0.5')
                                        latency_label = ui.label('').classes('text-xs font-semibold text-slate-500')
                                    url_label = ui.label('').classes('text-[10px] font-mono text-slate-400 truncate max-w-[400px] cursor-pointer').tooltip('Click to copy relative API URL')
                                    
                                ui.separator().classes('opacity-30')
                                
                                # Response body container
                                response_code_block_wrapper = ui.column().classes('w-full overflow-auto max-h-[300px]')
                                
                            # Wire action handlers using helper closures
                            def make_clear_handler(inputs=input_fields, panel=response_panel):
                                def handle_clear():
                                    for inp in inputs.values():
                                        inp.value = ''
                                    panel.style('display: none;')
                                return handle_clear
                                
                            def make_execute_handler(path=ep_path, inputs=input_fields, panel=response_panel, s_badge=status_badge, lat_lbl=latency_label, u_lbl=url_label, code_wrapper=response_code_block_wrapper):
                                async def handle_execute():
                                    panel.style('display: flex;')
                                    s_badge.text = "Loading..."
                                    s_badge.color = "amber"
                                    lat_lbl.text = ""
                                    u_lbl.text = ""
                                    code_wrapper.clear()
                                    
                                    # Fetch values
                                    params = {}
                                    headers = {}
                                    for k, inp in inputs.items():
                                        if inp.value and inp.value.strip():
                                            if k == '__jwt__':
                                                val = inp.value.strip()
                                                if not val.lower().startswith('bearer '):
                                                    val = f"Bearer {val}"
                                                headers['Authorization'] = val
                                            else:
                                                params[k] = inp.value.strip()
                                            
                                    import time, httpx, json
                                    start_time = time.perf_counter()
                                    
                                    # Form target url
                                    target_url = f"/api/{path}"
                                    query_str = "&".join([f"{k}={v}" for k, v in params.items()])
                                    full_url_display = f"{target_url}?{query_str}" if query_str else target_url
                                    
                                    try:
                                        # Execute dynamic endpoint internally on local interface asynchronously
                                        async with httpx.AsyncClient() as client:
                                            response = await client.get(f"http://127.0.0.1:8085/api/{path}", params=params, headers=headers, timeout=5.0)
                                        latency = int((time.perf_counter() - start_time) * 1000)
                                        status = response.status_code
                                        
                                        # Render metered stats
                                        s_badge.text = f"HTTP {status}"
                                        if status == 200:
                                            s_badge.color = "positive"
                                        else:
                                            s_badge.color = "negative"
                                            
                                        lat_lbl.text = f"Latency: {latency} ms"
                                        u_lbl.text = f"GET {full_url_display}"
                                        
                                        # Bind click to copy relative URL
                                        def make_copy_url_callback(u=full_url_display):
                                            return lambda _: [
                                                ui.run_javascript(f"navigator.clipboard.writeText(window.location.origin + '{u}')"),
                                                ui.notify('Full API URL copied to clipboard!', type='positive')
                                            ]
                                        u_lbl.on('click', make_copy_url_callback())
                                        
                                        # Format response body
                                        try:
                                            resp_body = json.dumps(response.json(), indent=2)
                                        except:
                                            resp_body = response.text
                                            
                                        with code_wrapper:
                                            ui.code(resp_body, language='json').classes('text-[10px] w-full p-2 rounded-lg bg-slate-900 text-slate-100 font-mono')
                                            
                                    except Exception as err:
                                        s_badge.text = "Error"
                                        s_badge.color = "negative"
                                        lat_lbl.text = ""
                                        u_lbl.text = f"Failed: GET {full_url_display}"
                                        with code_wrapper:
                                            ui.code(f"Connection failed: {err}", language='text').classes('text-[10px] w-full p-2 rounded-lg bg-slate-900 text-red-400 font-mono')
                                            
                                return handle_execute
                                
                            clear_btn.on_click(make_clear_handler())
                            execute_btn.on_click(make_execute_handler())

        # Initialize API Endpoints Table & Preseed
        def init_api_endpoints_table():
            try:
                explorer.conn.execute("""
                    CREATE TABLE IF NOT EXISTS _duckdb_studio_api_endpoints (
                        id VARCHAR PRIMARY KEY,
                        path VARCHAR UNIQUE,
                        description VARCHAR,
                        sql_code VARCHAR,
                        created_at TIMESTAMP,
                        security_enabled BOOLEAN DEFAULT FALSE,
                        rate_limit VARCHAR DEFAULT NULL
                    );
                """)
                # Ensure the columns exist for existing databases
                try:
                    explorer.conn.execute("ALTER TABLE _duckdb_studio_api_endpoints ADD COLUMN security_enabled BOOLEAN DEFAULT FALSE;")
                except Exception:
                    pass
                try:
                    explorer.conn.execute("ALTER TABLE _duckdb_studio_api_endpoints ADD COLUMN rate_limit VARCHAR DEFAULT NULL;")
                except Exception:
                    pass
                explorer.conn.execute("""
                    CREATE TABLE IF NOT EXISTS _duckdb_studio_api_metrics (
                        endpoint_path VARCHAR,
                        timestamp TIMESTAMP,
                        latency_ms DOUBLE,
                        status_code INTEGER,
                        error_message VARCHAR
                    );
                """)
                existing = explorer.conn.execute("SELECT COUNT(*) FROM _duckdb_studio_api_endpoints;").fetchone()[0]
                if existing == 0:
                    import uuid, datetime
                    explorer.conn.execute("""
                        INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at)
                        VALUES (?, ?, ?, ?, ?);
                    """, [
                        str(uuid.uuid4()),
                        'top-products',
                        'Returns list of items in product inventory filtered by minimum stock density.',
                        'SELECT name, category, price, stock FROM product_inventory WHERE stock >= $min_stock ORDER BY price DESC;',
                        datetime.datetime.now()
                    ])
                
                # Check and insert test2 endpoint
                test2_exists = explorer.conn.execute("SELECT COUNT(*) FROM _duckdb_studio_api_endpoints WHERE path = 'test2';").fetchone()[0]
                if test2_exists == 0:
                    import uuid, datetime
                    explorer.conn.execute("""
                        INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at)
                        VALUES (?, ?, ?, ?, ?);
                    """, [
                        str(uuid.uuid4()),
                        'test2',
                        'Dummy endpoint for testing parameters',
                        'SELECT 1 as column1, 2 as column2 WHERE ($my_param IS NULL OR 1 = $my_param);',
                        datetime.datetime.now()
                    ])
            except Exception as e:
                print(f"DEBUG: Failed to initialize _duckdb_studio_api_endpoints table: {e}", flush=True)

        init_api_endpoints_table()
        refresh_api_endpoints_grid()

        # Build Background Query Scheduler Container Content
        with scheduler_container:
            # Header Card
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('items-center gap-3'):
                    ui.icon('schedule', color='primary').classes('text-3xl')
                    ui.label('Background Query Scheduler & Exporter').classes('text-2xl font-black text-slate-800 dark:text-white')
                ui.label('Expose, automate, and export high-performance DuckDB query results to Parquet, CSV, or JSON in the background on periodic schedules. Supports automatic data partitioning.').classes('text-sm text-slate-500 dark:text-slate-400')

            # Sub Cards Layout (Grid)
            with ui.grid(columns=(1, 2)).classes('w-full gap-6 flex-none'):
                # CARD 1: CREATE SCHEDULER JOB FORM
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('add_alarm', color='primary').classes('text-2xl')
                        ui.label('Schedule New Export Job').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')

                    # Preset from Saved Query helper
                    try:
                        queries = explorer.list_saved_queries()
                        query_options = {q[3]: f"{q[1]} ({q[5] if q[5] else 'Analytical'})" for q in queries}
                    except Exception:
                        query_options = {}

                    def handle_preset_query_change(e):
                        if e.value:
                            scheduler_sql_input.value = e.value
                            # Auto set job name if empty
                            for q in queries:
                                if q[3] == e.value:
                                    if not scheduler_name_input.value:
                                        scheduler_name_input.value = f"Export {q[1]}"
                                    if not scheduler_filename_input.value:
                                        scheduler_filename_input.value = q[1].lower().replace(' ', '_')

                    ui.select(options=query_options, label='Preset: Load from Saved Query', on_change=handle_preset_query_change).props('dense outlined clearable').classes('w-full')

                    scheduler_name_input = ui.input(label='Job Name', placeholder='e.g., Hourly Sales Report').props('dense outlined clearable').classes('w-full')
                    
                    with ui.column().classes('w-full gap-1'):
                        ui.label('SQL Query Source').classes('text-xs font-semibold text-slate-400')
                        scheduler_sql_input = ui.textarea(placeholder='e.g., SELECT * FROM sales;').props('dense outlined autogrow').classes('w-full font-mono text-xs').style('min-height: 100px;')

                    with ui.row().classes('w-full gap-4'):
                        scheduler_interval_select = ui.select(
                            options=['Every Minute', 'Every 5 Minutes', 'Every 15 Minutes', 'Every Hour', 'Every 12 Hours', 'Daily'],
                            label='Interval Schedule', value='Every Hour'
                        ).props('dense outlined').classes('flex-grow')

                        scheduler_format_select = ui.select(
                            options=['Parquet', 'CSV', 'JSON'],
                            label='Export Format', value='Parquet'
                        ).props('dense outlined').classes('w-32')

                    scheduler_partition_input = ui.input(label='Partition Column (Optional)', placeholder='e.g., category').props('dense outlined clearable').classes('w-full').tooltip('Partition directory export by this column (DuckDB native PARTITION_BY)')
                    scheduler_filename_input = ui.input(label='Export Base Filename', placeholder='e.g., hourly_sales').props('dense outlined clearable').classes('w-full')

                    def handle_create_scheduled_job():
                        name = scheduler_name_input.value
                        sql = scheduler_sql_input.value
                        interval = scheduler_interval_select.value
                        fmt = scheduler_format_select.value
                        part_col = scheduler_partition_input.value
                        filename = scheduler_filename_input.value

                        if not name or not sql or not filename:
                            ui.notify('Please fill out all required fields (Name, SQL, and Export Filename)!', type='warning')
                            return

                        # Safe character validation for filename
                        import re
                        filename_clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', filename)

                        try:
                            import uuid, datetime
                            job_id = str(uuid.uuid4())
                            now = datetime.datetime.now()
                            next_run = calculate_next_run(interval, now)

                            explorer.conn.execute("""
                                INSERT INTO _duckdb_studio_scheduled_jobs (
                                    id, name, sql_code, interval_str, export_format, 
                                    partition_column, export_filename, last_run, next_run, status, error_message
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """, [job_id, name, sql, interval, fmt, part_col, filename_clean, None, next_run, 'Active', None])

                            ui.notify(f"Scheduled export job '{name}' created successfully!", type='success')
                            
                            # Clear form
                            scheduler_name_input.value = ''
                            scheduler_sql_input.value = ''
                            scheduler_partition_input.value = ''
                            scheduler_filename_input.value = ''

                            refresh_scheduler_jobs_list()
                        except Exception as err:
                            ui.notify(f"Failed to create scheduled job: {err}", type='negative')

                    ui.button('Create Export Job', icon='schedule', color='primary', on_click=handle_create_scheduled_job).props('elevated dense').classes('px-4 self-end mt-2')

                # CARD 2: ACTIVE JOBS LIST
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center justify-between no-wrap w-full'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('assignment', color='positive').classes('text-2xl')
                            ui.label('Active Scheduled Jobs').classes('text-lg font-bold text-slate-800 dark:text-white')
                        ui.button(icon='refresh', on_click=lambda: refresh_scheduler_jobs_list()).props('flat dense size=sm color=primary').classes('p-1')
                    ui.separator().classes('opacity-50')

                    scheduler_jobs_list_container = ui.column().classes('w-full gap-4 overflow-auto').style('max-height: 480px;')

            # SECTION 2: EXECUTION LOGS
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                with ui.row().classes('w-full items-center justify-between no-wrap'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('history', color='primary').classes('text-2xl')
                        ui.label('Job Execution Logs').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.button('Clear Execution Logs', icon='cleaning_services', on_click=lambda: handle_clear_scheduler_logs()).props('outline dense color=negative size=sm').classes('px-3 text-xs')
                
                ui.separator().classes('opacity-50')
                scheduler_logs_table_container = ui.column().classes('w-full gap-2')

        # Scheduler Helpers
        def handle_clear_scheduler_logs():
            try:
                explorer.conn.execute("DELETE FROM _duckdb_studio_scheduler_logs;")
                ui.notify('Scheduled query logs cleared successfully!', type='positive')
                refresh_scheduler_logs_table()
            except Exception as ex:
                ui.notify(f"Failed to clear logs: {ex}", type='negative')

        def refresh_scheduler_jobs_list():
            scheduler_jobs_list_container.clear()
            try:
                explorer.conn.execute("""
                    CREATE TABLE IF NOT EXISTS _duckdb_studio_scheduled_jobs (
                        id VARCHAR PRIMARY KEY,
                        name VARCHAR,
                        sql_code VARCHAR,
                        interval_str VARCHAR,
                        export_format VARCHAR,
                        partition_column VARCHAR,
                        export_filename VARCHAR,
                        last_run TIMESTAMP,
                        next_run TIMESTAMP,
                        status VARCHAR,
                        error_message VARCHAR
                    );
                """)
                rows = explorer.conn.execute("SELECT id, name, sql_code, interval_str, export_format, partition_column, export_filename, last_run, next_run, status, error_message FROM _duckdb_studio_scheduled_jobs ORDER BY name ASC;").fetchall()
            except Exception as e:
                with scheduler_jobs_list_container:
                    ui.label(f"Failed to load scheduled jobs: {e}").classes('text-xs text-negative')
                return

            if not rows:
                with scheduler_jobs_list_container:
                    with ui.column().classes('w-full items-center justify-center py-12 gap-2'):
                        ui.icon('alarm_off', color='grey').classes('text-5xl')
                        ui.label("No active background export jobs.").classes('text-sm text-slate-400 font-medium')
                        ui.label("Define a query and schedule interval in the form to configure background automation!").classes('text-xs text-slate-500')
                return

            with scheduler_jobs_list_container:
                for j_id, j_name, j_sql, j_interval, j_format, j_part_col, j_filename, j_last, j_next, j_status, j_err in rows:
                    with ui.card().classes('w-full p-4 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-3'):
                        # Top Row
                        with ui.row().classes('w-full items-center justify-between no-wrap'):
                            with ui.row().classes('items-center gap-2 no-wrap'):
                                status_color = 'positive' if j_status == 'Active' else 'grey'
                                ui.badge(j_status, color=status_color).classes('text-[10px] font-bold px-2 py-0.5')
                                ui.label(j_name).classes('text-sm font-bold text-slate-800 dark:text-white truncate')
                            
                            with ui.row().classes('items-center gap-1'):
                                # Toggle Status Action
                                def make_toggle_status_handler(job_id=j_id, curr_status=j_status):
                                    def toggle_status():
                                        new_status = 'Active' if curr_status == 'Inactive' else 'Inactive'
                                        try:
                                            explorer.conn.execute("UPDATE _duckdb_studio_scheduled_jobs SET status = ? WHERE id = ?;", [new_status, job_id])
                                            ui.notify(f"Job status changed to {new_status}!", type='success')
                                            refresh_scheduler_jobs_list()
                                        except Exception as e:
                                            ui.notify(f"Failed to toggle status: {e}", type='negative')
                                    return toggle_status

                                # Run Now Manual Trigger Action
                                def make_run_now_handler(job_id=j_id, name=j_name, sql=j_sql, fmt=j_format, part_col=j_part_col, filename=j_filename):
                                    def run_now():
                                        import time, uuid, os, datetime
                                        ui.notify(f"Triggering scheduled job '{name}' manually...", type='info')
                                        start_t = time.time()
                                        row_cnt = 0
                                        file_sz = 0
                                        status = "Success"
                                        err = None
                                        export_dir = "/home/martin/volumes/duckdb-studio/exports"

                                        try:
                                            copy_options = f"FORMAT '{fmt.upper()}'"
                                            if part_col and part_col.strip():
                                                copy_options += f", PARTITION_BY '{part_col.strip()}'"
                                                dest_path = os.path.join(export_dir, filename + f"_{fmt.lower()}_partitioned")
                                                os.makedirs(dest_path, exist_ok=True)
                                            else:
                                                ext = fmt.lower()
                                                dest_path = os.path.join(export_dir, f"{filename}.{ext}")

                                            # Load attached databases
                                            load_attached_databases_for_connection(explorer.conn)
                                            
                                            # Run manual export
                                            explorer.conn.execute(f"COPY ({sql.strip().rstrip(';')}) TO '{dest_path}' ({copy_options});")
                                            
                                            # Row count
                                            count_df = explorer.conn.execute(f"SELECT COUNT(*) FROM ({sql.strip().rstrip(';')});").fetchone()
                                            row_cnt = count_df[0] if count_df else 0
                                            
                                            # File size
                                            if os.path.exists(dest_path):
                                                if os.path.isdir(dest_path):
                                                    for root, dirs, files in os.walk(dest_path):
                                                        for f in files:
                                                            file_sz += os.path.getsize(os.path.join(root, f))
                                                else:
                                                    file_sz = os.path.getsize(dest_path)
                                                    
                                            ui.notify(f"Manual export completed successfully! Exported {row_cnt:,} rows.", type='success')
                                        except Exception as ex:
                                            status = "Failed"
                                            err = str(ex)
                                            ui.notify(f"Manual trigger failed: {ex}", type='negative')

                                        dur = (time.time() - start_t) * 1000.0
                                        now = datetime.datetime.now()
                                        
                                        try:
                                            # Update last_run
                                            explorer.conn.execute("UPDATE _duckdb_studio_scheduled_jobs SET last_run = ? WHERE id = ?;", [now, job_id])
                                            # Log execution
                                            explorer.conn.execute("""
                                                INSERT INTO _duckdb_studio_scheduler_logs (id, job_id, job_name, executed_at, duration_ms, row_count, file_size_bytes, status, error_message)
                                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                                            """, [str(uuid.uuid4()), job_id, name, now, dur, row_cnt, file_sz, status, err])
                                            
                                            refresh_scheduler_jobs_list()
                                            refresh_scheduler_logs_table()
                                        except Exception as db_err:
                                            print(f"DEBUG: Failed manual run telemetry update: {db_err}", flush=True)

                                    return run_now

                                # Delete Action
                                def make_delete_handler(job_id=j_id, name=j_name):
                                    def delete_job():
                                        try:
                                            explorer.conn.execute("DELETE FROM _duckdb_studio_scheduled_jobs WHERE id = ?;", [job_id])
                                            ui.notify(f"Scheduled job '{name}' deleted.", type='success')
                                            refresh_scheduler_jobs_list()
                                        except Exception as e:
                                            ui.notify(f"Failed to delete scheduled job: {e}", type='negative')
                                    return delete_job

                                toggle_icon = 'pause' if j_status == 'Active' else 'play_arrow'
                                ui.button(icon=toggle_icon, on_click=make_toggle_status_handler()).props('flat dense size=sm color=secondary').tooltip('Pause/Resume Job')
                                ui.button(icon='play_circle_outline', on_click=make_run_now_handler()).props('flat dense size=sm color=primary').tooltip('Trigger Manually Now')
                                ui.button(icon='delete', on_click=make_delete_handler()).props('flat dense size=sm color=negative').tooltip('Delete Job')

                        # Info row
                        with ui.row().classes('w-full items-center gap-4 text-[11px] bg-slate-50 dark:bg-slate-900/50 p-2 rounded-lg border border-slate-100 dark:border-slate-850'):
                            with ui.row().classes('items-center gap-1'):
                                ui.icon('schedule', color='slate', size='xs')
                                ui.label('Schedule:').classes('font-bold text-slate-500')
                                ui.label(j_interval).classes('font-bold text-slate-700 dark:text-slate-350')
                            with ui.row().classes('items-center gap-1'):
                                ui.icon('save', color='slate', size='xs')
                                ui.label('Format:').classes('font-bold text-slate-500')
                                ui.label(f"{j_format} ({j_filename})").classes('font-bold text-slate-700 dark:text-slate-355')
                            if j_part_col:
                                with ui.row().classes('items-center gap-1'):
                                    ui.icon('folder', color='slate', size='xs')
                                    ui.label('Partition:').classes('font-bold text-slate-500')
                                    ui.label(j_part_col).classes('font-bold text-indigo-500')

                        # Code Expansion
                        with ui.expansion('Automated Query SQL', icon='code').classes('w-full border border-slate-100 dark:border-slate-900 rounded-lg text-xs'):
                            ui.code(j_sql, language='sql').classes('w-full text-[10px] rounded-lg p-2 dark:bg-slate-950')

                        # Last & Next Run times
                        with ui.row().classes('w-full justify-between items-center text-[10px] text-slate-400 border-t border-slate-100 dark:border-slate-900 pt-2'):
                            last_str = j_last.strftime('%Y-%m-%d %H:%M:%S') if j_last else 'Never'
                            next_str = j_next.strftime('%Y-%m-%d %H:%M:%S') if j_next else 'Pending'
                            ui.label(f"Last Export: {last_str}")
                            ui.label(f"Next Target: {next_str}")
                            
                        # Error message if any
                        if j_err:
                            ui.label(f"⚠️ Last Error: {j_err}").classes('text-[10px] text-rose-500 font-mono w-full p-2 bg-rose-50 dark:bg-rose-950/20 rounded border border-rose-100 dark:border-rose-900')

        def refresh_scheduler_logs_table():
            scheduler_logs_table_container.clear()
            try:
                explorer.conn.execute("""
                    CREATE TABLE IF NOT EXISTS _duckdb_studio_scheduler_logs (
                        id VARCHAR PRIMARY KEY,
                        job_id VARCHAR,
                        job_name VARCHAR,
                        executed_at TIMESTAMP,
                        duration_ms DOUBLE,
                        row_count INTEGER,
                        file_size_bytes INTEGER,
                        status VARCHAR,
                        error_message VARCHAR
                    );
                """)
                logs = explorer.conn.execute("SELECT job_name, executed_at, duration_ms, row_count, file_size_bytes, status, error_message FROM _duckdb_studio_scheduler_logs ORDER BY executed_at DESC LIMIT 50;").fetchall()
            except Exception as e:
                with scheduler_logs_table_container:
                    ui.label(f"Failed to load logs: {e}").classes('text-xs text-negative')
                return

            if not logs:
                with scheduler_logs_table_container:
                    with ui.column().classes('w-full items-center justify-center py-8 border border-dashed border-slate-200 dark:border-slate-800 rounded-xl bg-slate-50/50 dark:bg-slate-900/20'):
                        ui.icon('hourglass_empty', color='grey').classes('text-3xl')
                        ui.label('No scheduler executions logged yet.').classes('text-xs text-slate-400 font-medium mt-1')
                        ui.label('Schedules will automatically execute in the background when their target time is met.').classes('text-[10px] text-slate-500')
                return

            with scheduler_logs_table_container:
                with ui.element('div').classes('w-full overflow-x-auto border border-slate-100 dark:border-slate-800 rounded-lg'):
                    with ui.element('table').classes('w-full text-left border-collapse text-xs'):
                        with ui.element('thead').classes('bg-slate-100 dark:bg-slate-900 text-slate-500 font-bold uppercase tracking-wider text-[10px]'):
                            with ui.element('tr'):
                                ui.element('th').classes('p-3').text('Job Name')
                                ui.element('th').classes('p-3 text-center').text('Executed At')
                                ui.element('th').classes('p-3 text-center').text('Duration')
                                ui.element('th').classes('p-3 text-center').text('Rows')
                                ui.element('th').classes('p-3 text-center').text('File Size')
                                ui.element('th').classes('p-3 text-right').text('Status')
                        
                        with ui.element('tbody').classes('divide-y divide-slate-100 dark:divide-slate-855 text-slate-700 dark:text-slate-350 font-mono'):
                            for name, exec_time, duration, rows_cnt, bytes_cnt, status, err_msg in logs:
                                with ui.element('tr').classes('hover:bg-slate-50/50 dark:hover:bg-slate-900/50 transition-colors'):
                                    ui.element('td').classes('p-3 font-bold text-slate-800 dark:text-slate-200').text(name)
                                    ui.element('td').classes('p-3 text-center text-slate-500').text(exec_time.strftime('%Y-%m-%d %H:%M:%S'))
                                    ui.element('td').classes('p-3 text-center text-primary').text(f"{duration:.1f}ms")
                                    ui.element('td').classes('p-3 text-center').text(f"{rows_cnt:,}")
                                    
                                    # Beautify file size
                                    if bytes_cnt >= 1024 * 1024:
                                        sz_str = f"{bytes_cnt / (1024*1024):.2f} MB"
                                    elif bytes_cnt >= 1024:
                                        sz_str = f"{bytes_cnt / 1024:.2f} KB"
                                    else:
                                        sz_str = f"{bytes_cnt} B"
                                    ui.element('td').classes('p-3 text-center text-slate-400').text(sz_str)
                                    
                                    status_cls = 'text-emerald-500' if status == 'Success' else 'text-rose-500'
                                    status_label = status if status == 'Success' else f"Failed: {err_msg[:20]}..." if err_msg else 'Failed'
                                    ui.element('td').classes(f'p-3 text-right font-bold {status_cls}').text(status_label).tooltip(err_msg if err_msg else status)

        refresh_scheduler_jobs_list()
        refresh_scheduler_logs_table()

        # Build Settings Container Content
        with settings_container:
            # Header Card
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('items-center gap-3'):
                    ui.icon('settings', color='primary').classes('text-3xl')
                    ui.label('Application & Studio Settings').classes('text-2xl font-black text-slate-800 dark:text-white')
                ui.label('Configure global rate limits, security tokens, telemetry settings, query safety constraints, and JupyterLab integrations.').classes('text-sm text-slate-500 dark:text-slate-400')

            # Fetch existing jupyter configuration
            j_url, j_token = get_jupyter_config()

            # Create inputs bound to APP_SETTINGS and Jupyter
            with ui.grid(columns=(1, 2)).classes('w-full gap-6 flex-none'):
                # Card 1: Rate Limiting & Query Limits
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('speed', color='primary').classes('text-2xl')
                        ui.label('Rate Limiting & Safety Limits').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')
                    
                    settings_default_rate_limit = ui.input('Default Endpoint Rate Limit', value=APP_SETTINGS.get('default_rate_limit', '5/minute')).props('outlined dense').classes('w-full').tooltip('The default rate limit per dynamic API route (e.g. 5/minute, 60/hour).')
                    settings_max_safety_limit = ui.number('Maximum Safety Limit (Rows)', value=APP_SETTINGS.get('max_safety_limit', 10000), format='%d').props('outlined dense').classes('w-full').tooltip('Maximum rows that can be returned in a standard API request or preview.')
                    settings_default_page_size = ui.number('Default Page Size', value=APP_SETTINGS.get('default_page_size', 100), format='%d').props('outlined dense').classes('w-full').tooltip('Default pagination page size for dynamic API query execution.')

                # Card 2: Security & JWT
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('security', color='primary').classes('text-2xl')
                        ui.label('Security & JWT tokens').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')
                    
                    settings_jwt_secret = ui.input('JWT Signature Secret', value=APP_SETTINGS.get('jwt_secret', 'duckdb_studio_secret_key_1337'), password=True, password_toggle_button=True).props('outlined dense').classes('w-full').tooltip('HMAC HS256 secret key used for signing/verifying security tokens.')
                    settings_jwt_issuer = ui.input('JWT Issuer Name', value=APP_SETTINGS.get('jwt_issuer', 'duckdb_studio')).props('outlined dense').classes('w-full')
                    settings_jwt_audience = ui.input('JWT Audience Name', value=APP_SETTINGS.get('jwt_audience', 'duckdb_studio_clients')).props('outlined dense').classes('w-full')

                # Card 3: Telemetry Configuration
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('analytics', color='primary').classes('text-2xl')
                        ui.label('Telemetry & Performance Retention').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')
                    
                    settings_telemetry_retention = ui.number('Telemetry Retention (Days)', value=APP_SETTINGS.get('telemetry_retention_days', 30), format='%d').props('outlined dense').classes('w-full').tooltip('Number of days to store REST API execution telemetry before cleanup.')

                # Card 4: JupyterLab Integration
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('terminal', color='primary').classes('text-2xl')
                        ui.label('JupyterLab Credentials').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')
                    
                    settings_jupyter_url = ui.input('Jupyter Server URL', value=j_url).props('outlined dense').classes('w-full').tooltip('Base URL of the JupyterLab interface container.')
                    settings_jupyter_token = ui.input('Jupyter Security Token', value=j_token, password=True, password_toggle_button=True).props('outlined dense').classes('w-full').tooltip('Token query parameter required to authenticate JupyterLab session.')

            # Actions row
            with ui.row().classes('w-full justify-end gap-3 p-4'):
                def handle_save_settings():
                    new_settings = {
                        "default_rate_limit": settings_default_rate_limit.value.strip() if settings_default_rate_limit.value else "5/minute",
                        "max_safety_limit": int(settings_max_safety_limit.value) if settings_max_safety_limit.value is not None else 10000,
                        "default_page_size": int(settings_default_page_size.value) if settings_default_page_size.value is not None else 100,
                        "telemetry_retention_days": int(settings_telemetry_retention.value) if settings_telemetry_retention.value is not None else 30,
                        "jwt_secret": settings_jwt_secret.value.strip() if settings_jwt_secret.value else "duckdb_studio_secret_key_1337",
                        "jwt_issuer": settings_jwt_issuer.value.strip() if settings_jwt_issuer.value else "duckdb_studio",
                        "jwt_audience": settings_jwt_audience.value.strip() if settings_jwt_audience.value else "duckdb_studio_clients"
                    }
                    new_jupyter = {
                        "url": settings_jupyter_url.value.strip() if settings_jupyter_url.value else "http://localhost:8889",
                        "token": settings_jupyter_token.value.strip() if settings_jupyter_token.value else "analytics_secret"
                    }
                    
                    if save_app_settings(new_settings, new_jupyter):
                        ui.notify('Settings successfully saved and reloaded!', type='success')
                    else:
                        ui.notify('Failed to save settings. Check logs for details.', type='negative')

                ui.button('Save Studio Configuration', icon='save', on_click=handle_save_settings).props('elevated color=primary').classes('px-6 py-2 text-sm font-bold rounded-lg')

        # Bind visibility based on active tab
        studio_container.bind_visibility_from(tabs, 'value', value='Explorer')
        jupyter_container.bind_visibility_from(tabs, 'value', value='JupyterLab')
        dbt_workbench_container.bind_visibility_from(tabs, 'value', value='dbt Workbench')
        code_editor_container.bind_visibility_from(tabs, 'value', value='Code Editor')
        extensions_container.bind_visibility_from(tabs, 'value', value='Extensions')
        db_tools_container.bind_visibility_from(tabs, 'value', value='Database Tools')
        api_creator_container.bind_visibility_from(tabs, 'value', value='API Endpoints')
        api_docs_container.bind_visibility_from(tabs, 'value', value='API Docs & Explorer')
        scheduler_container.bind_visibility_from(tabs, 'value', value='Scheduler')
        settings_container.bind_visibility_from(tabs, 'value', value='Settings')
        
    # Main split layout container
    with studio_container:
        
        # Configure Splitter to partition Left Sidebar and Right Content
        with ui.splitter(value=20).classes('w-full h-full') as main_splitter:
            
            # --- LEFT SIDEBAR (DATABASE METADATA & HISTORY) ---
            with main_splitter.before:
                with ui.column().classes('w-full h-full p-4 sidebar-card q-pa-md gap-4 flex-nowrap').style('background-color: var(--q-slate-50);'):
                    
                    # Branding Header
                    with ui.row().classes('items-center w-full justify-between no-wrap'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('schema', color='primary').classes('text-xl')
                            ui.label('Schema Explorer').classes('text-sm font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400')
                        
                        # Refresh schema button
                        ui.button(icon='refresh', on_click=lambda: refresh_schema_tree()).props('flat fab-mini').classes('text-slate-600')
                    
                    ui.separator()
                    
                    # Active DB File Indicator
                    with ui.card().classes('w-full p-3 glass-card border-none shadow-none dark-bg-flat'):
                        with ui.column().classes('w-full gap-2'):
                            with ui.row().classes('items-center justify-between w-full no-wrap'):
                                with ui.row().classes('items-center gap-2 no-wrap'):
                                    ui.icon('folder_open', color='secondary').classes('text-lg')
                                    ui.label('Database Connection').classes('text-xs text-slate-500 font-semibold uppercase')
                                ui.button(icon='add', on_click=lambda: attach_db_dialog.open()).props('flat fab-mini dense').classes('text-slate-600').tooltip('Attach external database')
                            
                            ui.separator().classes('my-1 opacity-50')
                            
                            # Container for attached databases
                            databases_container = ui.column().classes('w-full gap-1 pl-1')
                    
                    # Database Schema Explorer & Saved Queries Library
                    with ui.expansion('🌳 Schema Browser', icon='account_tree', value=True).classes('w-full border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel text-xs text-slate-700 dark:text-slate-300 font-bold'):
                        with ui.column().classes('w-full gap-2 p-2'):
                            schema_filter_input = ui.input(placeholder='Filter tables, views, columns...', on_change=lambda _: refresh_schema_tree()).props('outlined dense clearable').classes('w-full font-normal text-xs').style('font-size: 11px;')
                            schema_container = ui.column().classes('w-full overflow-auto gap-0 text-slate-800 dark:text-slate-100').style('max-height: 340px;')

                    with ui.expansion('💾 SQL Snippets Library', icon='bookmark', value=True).classes('w-full border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel text-xs text-slate-700 dark:text-slate-300 font-bold'):
                        with ui.column().classes('w-full gap-2 p-2'):
                            saved_queries_filter = ui.input(placeholder='Filter snippets...', on_change=lambda _: refresh_saved_queries_list()).props('outlined dense clearable').classes('w-full font-normal text-xs').style('font-size: 11px;')
                            snippet_category_toggle = ui.toggle(
                                options={'All': 'All', 'Analytical': 'Analytics', 'Utility': 'Utility', 'DDL/DML': 'DDL/DML'},
                                value='All',
                                on_change=lambda _: refresh_saved_queries_list()
                            ).props('dense unelevated').classes('w-full text-xs font-normal').style('font-size: 10px;')
                            saved_queries_container = ui.column().classes('w-full overflow-auto gap-2 text-slate-800 dark:text-slate-100').style('max-height: 260px;')
                    
                    # Seeding Actions
                    with ui.row().classes('w-full mt-auto pt-2 justify-between gap-1 no-wrap'):
                        ui.button('Import File', icon='file_upload', color='primary',
                                  on_click=lambda: open_import_dialog()).props('elevated dense').classes('text-xs flex-grow')
                        ui.button('Reset & Reseed', icon='restart_alt', color='warning', 
                                  on_click=lambda: confirm_reseed()).props('outline dense').classes('text-xs flex-grow')

            # --- RIGHT WORKSPACE (SQL EDITOR, ACTIONS, GRAPHICS, GRID) ---
            with main_splitter.after:
                with ui.column().classes('w-full h-full p-6 gap-4 flex-nowrap overflow-hidden'):
                    
                    # Top Workspace Bar
                    with ui.row().classes('w-full items-center justify-between no-wrap'):
                        with ui.column().classes('gap-0'):
                            ui.label('SQL Workspace').classes('text-2xl font-extrabold text-slate-800 dark:text-white')
                            ui.label('Write queries, inspect results, and plot live analytics dashboards').classes('text-sm text-slate-500')
                        
                        # Mode switches
                        with ui.row().classes('items-center gap-3'):
                            ui.dark_mode().bind_value(app.storage.user, 'dark_mode')
                            ui.icon('light_mode', color='amber').classes('text-lg')
                            
                            def toggle_theme(e):
                                sql_editor.theme = 'monokai' if e.value else 'basicLight'
                                sql_editor.update()
                                
                            ui.switch(value=app.storage.user.get('dark_mode', False), on_change=toggle_theme).bind_value(app.storage.user, 'dark_mode').props('color=indigo')
                            ui.icon('dark_mode', color='indigo').classes('text-lg')
                    
                    # SQL Editor Card Container
                    with ui.card().classes('w-full p-4 shadow-sm border-slate-200 dark:border-slate-800'):
                        
                        # 🧱 INTERACTIVE VISUAL QUERY BUILDER
                        with ui.expansion('🧱 Interactive Visual Query Builder', icon='auto_awesome', value=False).classes('w-full border border-dashed border-indigo-200 dark:border-indigo-900 rounded-lg p-2 dark-bg-panel mb-3 text-xs text-indigo-600 dark:text-indigo-400 font-bold') as query_builder_expansion:
                            with ui.column().classes('w-full gap-3 p-2'):
                                ui.label('Select table and fields to construct standard SQL queries automatically:').classes('text-xs text-slate-500 font-normal')
                                
                                # Grid for Dropdowns
                                with ui.row().classes('w-full items-center gap-4 flex-wrap'):
                                    qb_db_select = ui.select(options=[], value=None, label='Select Database', on_change=lambda e: update_builder_tables_for_db(e.value)).props('dense outlined clearable').style('width: 180px;')
                                    qb_table_select = ui.select(options=[], value=None, label='1. Select Table', on_change=lambda e: handle_builder_table_change(e.value)).props('dense outlined clearable').style('width: 220px;')
                                    qb_order_select = ui.select(options=[], label='3. Order By (Optional)').props('dense outlined').style('width: 200px;')
                                    qb_dir_select = ui.select(options={'ASC': 'Ascending', 'DESC': 'Descending'}, value='ASC', label='Direction').props('dense outlined').style('width: 120px;')
                                    qb_limit_input = ui.number(value=100, label='4. Limit Rows', min=1).props('dense outlined').style('width: 100px;')

                                # Columns multi-select checkbox list
                                with ui.column().classes('w-full gap-1 border border-slate-200 dark:border-slate-800 rounded p-3 dark-bg-flat'):
                                    ui.label('2. Select Columns').classes('text-xs font-bold text-slate-600 dark:text-slate-400')
                                    qb_columns_container = ui.row().classes('w-full gap-3 flex-wrap items-center max-h-32 overflow-y-auto pr-1')
                                
                                # Filter (WHERE) Conditions
                                with ui.row().classes('w-full items-center gap-3 flex-wrap border border-slate-200 dark:border-slate-800 rounded p-3 dark-bg-flat'):
                                    ui.label('5. Add Filter (WHERE)').classes('text-xs font-bold text-slate-600 dark:text-slate-400 w-full')
                                    qb_filter_col = ui.select(options=[], label='Filter Column').props('dense outlined').style('width: 180px;')
                                    qb_filter_op = ui.select(options={
                                        '=': '=',
                                        '>': '>',
                                        '<': '<',
                                        '>=': '>=',
                                        '<=': '<=',
                                        'LIKE': 'Contains (LIKE)',
                                        'IS NULL': 'Is NULL',
                                        'IS NOT NULL': 'Is Not NULL'
                                    }, value='=', label='Operator').props('dense outlined').style('width: 140px;')
                                    qb_filter_val = ui.input(label='Filter Value', placeholder='e.g., Electronics or 250').props('dense outlined').style('width: 180px;')
                                
                                # Builder Actions
                                with ui.row().classes('w-full justify-end gap-2 mt-2'):
                                    ui.button('Reset Builder', icon='restart_alt', color='warning', on_click=lambda: reset_query_builder()).props('outline dense')
                                    ui.button('Generate SQL', icon='code', color='secondary', on_click=lambda: generate_builder_sql(run_query=False)).props('dense')
                                    ui.button('Generate & Execute', icon='flash_on', color='primary', on_click=lambda: generate_builder_sql(run_query=True)).props('dense')

                        # SQL Quick actions toolbar
                        with ui.row().classes('w-full justify-between items-center no-wrap gap-2 pb-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('code', color='primary').classes('text-xl')
                                ui.label('SQL Query Editor').classes('font-semibold text-slate-700 dark:text-slate-300')
                            
                            # Templates Selector
                            templates_options = {
                                "SELECT * FROM sales_transactions ORDER BY transaction_date DESC LIMIT 100;": "Select Top 100 Transactions",
                                "SELECT category, SUM(total_amount) AS revenue, COUNT(*) AS count FROM sales_transactions s JOIN product_inventory p ON s.product_id = p.product_id GROUP BY category ORDER BY revenue DESC;": "Revenue by Product Category",
                                "SELECT payment_method, COUNT(*) AS count, SUM(total_amount) AS total_revenue FROM sales_transactions GROUP BY payment_method ORDER BY total_revenue DESC;": "Payment Method Breakdown",
                                "SELECT loyalty_tier, AVG(total_amount) AS avg_spent FROM sales_transactions t JOIN customer_profiles c ON t.customer_id = c.customer_id GROUP BY loyalty_tier ORDER BY avg_spent DESC;": "Average Customer Spend by Loyalty Tier",
                                "SELECT DATE_TRUNC('month', transaction_date)::DATE AS month, SUM(total_amount) AS total_revenue FROM sales_transactions GROUP BY 1 ORDER BY 1;": "Monthly Revenue Trend",
                                "-- 🦆 Attach a DuckLake table format database\n-- Replace the paths below with your metadata DB file and Parquet data folder:\nATTACH 'ducklake:path/to/metadata.db' AS my_lakehouse (DATA_PATH 'path/to/data_parquet/');\n\n-- Now you can query tables in your lakehouse as usual:\n-- SELECT * FROM my_lakehouse.my_table LIMIT 10;": "Attach & Query DuckLake Database"
                            }
                            
                            def handle_template_select(e):
                                if e.value:
                                    sql_editor.value = e.value
                                    sql_select.value = None # reset selector
                            
                            sql_select = ui.select(
                                options=templates_options, 
                                label='Query Templates', 
                                on_change=handle_template_select
                            ).props('dense outlined color=indigo').style('width: 280px; font-size: 13px;')
                        
                        # SQL Code Editor itself (using NiceGUI CodeMirror component with linter bound)
                        initial_query = app.storage.user.get('last_query', query_history[0])
                        initial_dark = app.storage.user.get('dark_mode', False)
                        initial_theme = 'monokai' if initial_dark else 'basicLight'
                        sql_editor = ui.codemirror(
                            value=initial_query, 
                            language='sql', 
                            theme=initial_theme,
                            on_change=validate_sql_on_change
                        ).classes('w-full border rounded shadow-inner').style('height: 160px; font-size: 14px;')
                        
                        # Live Linter Status Strip
                        with ui.row().classes('w-full items-center no-wrap gap-2 px-3 py-1.5 -mt-2 rounded bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800') as linter_strip:
                            linter_icon = ui.icon('check_circle', color='emerald').classes('text-base')
                            linter_label = ui.label('SQL Syntax Valid').classes('text-xs font-mono text-emerald-600 dark:text-emerald-400')

                        # Control Buttons
                        with ui.row().classes('w-full justify-between items-center pt-3 no-wrap'):
                            with ui.row().classes('gap-2'):
                                ui.button('Execute Query', icon='play_arrow', color='primary', 
                                          on_click=lambda: run_editor_query()).props('elevated').classes('px-4')
                                ui.button('Explain Query', icon='troubleshoot', color='secondary', 
                                          on_click=lambda: trigger_explain_query()).props('elevated').classes('px-3')
                                ui.button('Save Query', icon='bookmark_add', color='positive',
                                          on_click=lambda: open_save_query_dialog()).props('elevated')
                                ui.button('Format SQL', icon='format_align_left', color='secondary',
                                          on_click=lambda: format_sql_query()).props('outline')
                                ui.button('Clear', icon='delete_sweep', color='negative',
                                          on_click=lambda: sql_editor.set_value('')).props('flat')
                            
                            ui.label('Press Ctrl+Enter inside workspace to run').classes('text-xs text-slate-400 font-mono hidden md:block')
                    
                    # --- RESULTS COMPONENT PANELS ---
                    with ui.card().classes('w-full flex-grow p-4 shadow-sm border-slate-200 dark:border-slate-800 overflow-hidden min-h-0 flex-nowrap'):
                        
                        # Result Status Banner
                        with ui.row().classes('w-full justify-between items-center no-wrap border-b pb-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('analytics', color='primary').classes('text-xl')
                                ui.label('Output Results').classes('font-semibold text-slate-700 dark:text-slate-300')
                            
                            # Info statistics label
                            status_label = ui.label('No queries run yet').classes('text-sm text-slate-500 font-mono')
                        
                        # Tabs setup
                        with ui.tabs().classes('w-full border-b text-indigo-500') as result_tabs:
                            grid_tab = ui.tab('Data Grid', icon='table_chart')
                            chart_tab = ui.tab('Analytics Chart', icon='bar_chart')
                            map_tab = ui.tab('Geo Map', icon='map')
                            profile_tab = ui.tab('Query Profiler', icon='speed')
                            history_tab = ui.tab('Session History', icon='history')
                            log_tab = ui.tab('System Log', icon='info')
                        
                        with ui.tab_panels(result_tabs, value=grid_tab).classes('w-full bg-transparent flex-grow'):
                            
                            # DATA GRID TAB
                            with ui.tab_panel(grid_tab).classes('p-0 pt-4 gap-4 flex-col h-full min-h-0'):
                                with ui.row().classes('w-full justify-between items-center gap-4'):
                                    # Local filtering
                                    grid_search = ui.input(placeholder='Filter results...').props('outlined dense clearable').style('width: 250px;')
                                    # Export buttons
                                    with ui.row().classes('gap-2'):
                                        csv_download_btn = ui.button('Export CSV', icon='download', color='positive', 
                                                                     on_click=lambda: trigger_csv_download()).props('dense elevated').classes('px-3')
                                        csv_download_btn.disable()
                                        parquet_download_btn = ui.button('Export Parquet', icon='download', color='positive', 
                                                                         on_click=lambda: trigger_parquet_download()).props('dense elevated').classes('px-3')
                                        parquet_download_btn.disable()
                                
                                # Interactive Result Table
                                grid_container = ui.column().classes('w-full flex-grow overflow-auto border rounded dark-bg-panel min-h-0 h-full flex-nowrap')
                                with grid_container:
                                    result_table = ui.table(
                                        columns=[], 
                                        rows=[]
                                    ).props('dense flat bordered separator=cell table-header-class=bg-indigo-500').classes('w-full shadow-none')
                                    # Bind the search input to table filtering
                                    result_table.bind_filter(grid_search, 'value')
                            
                            with ui.tab_panel(chart_tab).classes('p-0 pt-4 gap-4 flex-col h-full min-h-0 overflow-auto'):
                                # Chart Controls
                                with ui.card().classes('w-full p-4 border-none shadow-none dark-bg-flat'):
                                    with ui.row().classes('w-full items-center gap-4 flex-wrap justify-between'):
                                        with ui.row().classes('items-center gap-3 flex-wrap'):
                                            x_axis_select = ui.select(options=[], label='X-Axis (Category/Date)').props('dense outlined').style('width: 180px;')
                                            y_axis_select = ui.select(options=[], label='Y-Axis (Metric)').props('dense outlined').style('width: 180px;')
                                            chart_type_select = ui.select(options=['bar', 'line', 'scatter', 'pie'], value='bar', label='Chart Type').props('dense outlined').style('width: 130px;')
                                        
                                        ui.button('Generate Chart', icon='auto_awesome', color='primary', 
                                                  on_click=lambda: render_chart()).props('dense elevated').classes('px-3')
                                
                                # Chart Plot Area
                                chart_container = ui.column().classes('w-full h-80 justify-center items-center border border-dashed rounded p-4 dark-bg-panel chart-container-panel flex-nowrap flex-none')
                                with chart_container:
                                    ui.label('Execute a SELECT query and configure X/Y columns to build a chart.').classes('text-slate-400')
                            
                            # GEO MAP TAB
                            with ui.tab_panel(map_tab).classes('p-0 pt-4 gap-4 flex-col h-full min-h-0 overflow-auto'):
                                # Map Controls
                                with ui.card().classes('w-full p-4 border-none shadow-none dark-bg-flat'):
                                    with ui.row().classes('w-full items-center gap-4 flex-wrap justify-between'):
                                        with ui.row().classes('items-center gap-3 flex-wrap'):
                                            lat_select = ui.select(options=[], label='Latitude Column').props('dense outlined').style('width: 180px;')
                                            lng_select = ui.select(options=[], label='Longitude Column').props('dense outlined').style('width: 180px;')
                                            label_select = ui.select(options=[], label='Label Column (Optional)').props('dense outlined').style('width: 180px;')
                                        
                                        ui.button('Plot Map', icon='map', color='primary', 
                                                  on_click=lambda: render_map()).props('dense elevated').classes('px-3')
                                
                                # Map Plot Area
                                map_container = ui.column().classes('w-full h-80 justify-center items-center border border-dashed rounded p-4 dark-bg-panel map-container-panel flex-nowrap flex-none')
                                with map_container:
                                    ui.label('Execute a SELECT query with coordinates to plot a map.').classes('text-slate-400')

                            # QUERY PROFILER TAB
                            with ui.tab_panel(profile_tab).classes('p-0 pt-4 gap-4 flex-col h-full min-h-0 overflow-auto'):
                                # Profiler Controls
                                with ui.card().classes('w-full p-4 border-none shadow-none dark-bg-flat'):
                                    with ui.row().classes('w-full items-center gap-4 flex-wrap justify-between'):
                                        with ui.row().classes('items-center gap-3 flex-wrap'):
                                            profile_mode_select = ui.select(
                                                options=['Logical Plan (EXPLAIN)', 'Execution Profile (EXPLAIN ANALYZE)'],
                                                value='Logical Plan (EXPLAIN)',
                                                label='Profiler Mode'
                                            ).props('dense outlined').style('width: 280px;')
                                        
                                        ui.button('Profile Query', icon='speed', color='secondary',
                                                  on_click=lambda: run_profiler_query()).props('dense elevated').classes('px-3')
                                
                                # Dynamic Profiler Container
                                profiler_container = ui.column().classes('w-full gap-4 flex-nowrap flex-grow min-h-0 h-full overflow-auto')
                                with profiler_container:
                                    ui.label('Click "Profile Query" or use "Explain Query" to analyze execution plan.').classes('text-slate-400')

                            # SESSION HISTORY TAB
                            with ui.tab_panel(history_tab).classes('p-0 pt-4 gap-2 flex-col'):
                                ui.label('Recent Queries in this Session:').classes('text-sm text-slate-500 pb-2')
                                history_container = ui.column().classes('w-full gap-2 overflow-auto max-h-80')

                            # SYSTEM LOG TAB
                            with ui.tab_panel(log_tab).classes('p-0 pt-4 gap-4 flex-col'):
                                with ui.card().classes('w-full p-4 border-none shadow-none dark-bg-flat'):
                                    ui.label('Database Specifications').classes('font-bold text-slate-700 dark:text-slate-300 text-base')
                                    ui.separator().classes('my-2')
                                    
                                    with ui.grid(columns=2).classes('w-full gap-4'):
                                        with ui.column().classes('gap-1'):
                                            ui.label('DuckDB Engine Version:').classes('text-xs text-slate-400')
                                            ui.label(duckdb.__version__).classes('text-sm font-mono font-bold text-slate-800 dark:text-slate-200')
                                            
                                            ui.label('Database File Path:').classes('text-xs text-slate-400 mt-2')
                                            ui.label(os.path.abspath(DB_NAME)).classes('text-xs font-mono break-all text-slate-800 dark:text-slate-200')
                                        
                                        with ui.column().classes('gap-1'):
                                            ui.label('Database Connection Mode:').classes('text-xs text-slate-400')
                                            ui.label('Read & Write SQL Sandbox').classes('text-sm font-bold text-indigo-500')
                                            
                                            ui.label('Size on Disk:').classes('text-xs text-slate-400 mt-2')
                                            db_size = os.path.getsize(DB_NAME) if os.path.exists(DB_NAME) else 0
                                            ui.label(f"{db_size / (1024*1024):.2f} MB").classes('text-sm font-mono font-bold text-slate-800 dark:text-slate-200')

    # --- CALLBACKS ENCAPSULATED INSIDE INDEX CLIENT CONTEXT ---

    def get_table_statistics(schema_name, table_name, database_name='main', attached_dbs=None):
        """Perform analytical scans on a table to compute card metrics and column statistics."""
        # Open a dedicated connection for this background thread to ensure thread safety
        thread_conn = duckdb.connect(explorer.db_file)
        try:
            # Re-attach any databases that were attached in the main session
            try:
                thread_conn.execute("INSTALL ducklake; LOAD ducklake;")
            except Exception:
                pass
                
            if attached_dbs is not None:
                for db, path in attached_dbs:
                    if db not in ('main', 'system', 'temp'):
                        try:
                            attach_queries = getattr(explorer, 'attached_dbs_queries', {})
                            if db in attach_queries:
                                thread_conn.execute(attach_queries[db])
                            else:
                                thread_conn.execute(f"ATTACH '{path}' AS {db}")
                        except Exception as attach_ex:
                            print(f"DEBUG: Could not attach {db} in thread: {attach_ex}")

            # 1. Fetch columns and their types for this specific database and schema
            columns_query = f"""
                SELECT column_name, data_type 
                FROM duckdb_columns 
                WHERE database_name = '{database_name}' 
                  AND schema_name = '{schema_name}' 
                  AND table_name = '{table_name}' 
                ORDER BY column_index;
            """
            columns_res = thread_conn.execute(columns_query).fetchall()
            cols = [(row[0], row[1]) for row in columns_res]
            
            # Fallback to PRAGMA table_info if needed
            if not cols:
                try:
                    columns_res = thread_conn.execute(f"PRAGMA table_info('{database_name}.{schema_name}.{table_name}')").fetchall()
                    cols = [(row[1], row[2]) for row in columns_res]
                except Exception:
                    pass
            
            # 2. Get total row count
            qualified_table = f"{database_name}.{schema_name}.{table_name}"
            row_count_res = thread_conn.execute(f"SELECT count(*) FROM {qualified_table}").fetchone()
            total_rows = row_count_res[0] if row_count_res else 0
            
            if total_rows == 0:
                return {
                    'total_rows': 0,
                    'disk_size': 0,
                    'columns': [{
                        'name': name,
                        'type': type_str,
                        'nullity': 100.0,
                        'cardinality': 0,
                        'min': 'N/A',
                        'max': 'N/A',
                        'avg': 'N/A',
                        'stddev': 'N/A'
                    } for name, type_str in cols]
                }
                
            # 3. Estimate disk footprint (pragma_storage_info on qualified name or fallback)
            try:
                block_size_res = thread_conn.execute("SELECT block_size FROM pragma_database_size()").fetchone()
                block_size = block_size_res[0] if block_size_res else 262144
                
                blocks_res = thread_conn.execute(f"SELECT count(DISTINCT block_id) FROM pragma_storage_info('{qualified_table}') WHERE block_id IS NOT NULL").fetchone()
                disk_size = blocks_res[0] * block_size if blocks_res else 0
            except Exception:
                disk_size = 0
                
            if disk_size == 0:
                # Hybrid nominal size estimator:
                nominal_widths = {
                    'TINYINT': 1, 'UTINYINT': 1,
                    'SMALLINT': 2, 'USMALLINT': 2,
                    'INTEGER': 4, 'UINTEGER': 4,
                    'BIGINT': 8, 'UBIGINT': 8, 'HUGEINT': 16,
                    'FLOAT': 4, 'REAL': 4, 'DOUBLE': 8,
                    'DATE': 4, 'TIME': 8, 'TIMESTAMP': 8, 'TIMESTAMPTZ': 8,
                    'BOOLEAN': 1,
                }
                bytes_per_row = 0
                for name, type_str in cols:
                    base_type = type_str.split('(')[0].strip().upper()
                    bytes_per_row += nominal_widths.get(base_type, 24)
                disk_size = total_rows * bytes_per_row
                
            # 4. Construct SQL statement for column-level statistics
            select_parts = []
            numeric_types = {
                'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT',
                'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT', 'UHUGEINT',
                'FLOAT', 'DOUBLE', 'DECIMAL', 'REAL'
            }
            datetime_types = {
                'DATE', 'TIME', 'TIMESTAMP', 'TIMESTAMPTZ'
            }
            
            for name, type_str in cols:
                base_type = type_str.split('(')[0].strip().upper()
                esc = f'"{name}"'
                part = f'count({esc}), count(DISTINCT {esc})'
                
                if base_type in numeric_types or base_type in datetime_types:
                    part += f', min({esc}), max({esc})'
                else:
                    part += ', NULL, NULL'
                    
                if base_type in numeric_types:
                    part += f', avg({esc}), stddev({esc})'
                else:
                    part += ', NULL, NULL'
                    
                select_parts.append(part)
                
            stats_sql = f"SELECT {', '.join(select_parts)} FROM {qualified_table};"
            stats_res = thread_conn.execute(stats_sql).fetchone()
            
            # 5. Map results back to columns
            columns_stats = []
            col_idx = 0
            for name, type_str in cols:
                non_null_count = stats_res[col_idx]
                distinct_count = stats_res[col_idx + 1]
                c_min = stats_res[col_idx + 2]
                c_max = stats_res[col_idx + 3]
                c_avg = stats_res[col_idx + 4]
                c_stddev = stats_res[col_idx + 5]
                col_idx += 6
                
                null_count = total_rows - non_null_count
                nullity_ratio = (null_count / total_rows) * 100.0
                
                def fmt_val(val, is_float=False):
                    if val is None:
                        return 'N/A'
                    if isinstance(val, (int, float)):
                        if is_float:
                            return f"{val:.4f}"
                        return f"{val:,}" if isinstance(val, int) else f"{val:.2f}"
                    return str(val)
                    
                columns_stats.append({
                    'name': name,
                    'type': type_str,
                    'nullity': nullity_ratio,
                    'cardinality': distinct_count,
                    'min': fmt_val(c_min),
                    'max': fmt_val(c_max),
                    'avg': fmt_val(c_avg, is_float=True),
                    'stddev': fmt_val(c_stddev, is_float=True)
                })
                
            return {
                'total_rows': total_rows,
                'disk_size': disk_size,
                'columns': columns_stats
            }
        finally:
            thread_conn.close()

    async def open_table_inspector(schema_name, table_name, database_name='main'):
        """Open the overlay dialog and calculate/render table stats in a background thread."""
        inspector_dialog.open()
        inspector_content.clear()
        
        with inspector_content:
            with ui.column().classes('w-full items-center justify-center py-12 gap-3'):
                ui.spinner(size='4em', color='indigo')
                ui.label(f'Analyzing table {database_name}.{schema_name}.{table_name}...').classes('text-sm text-slate-500 dark:text-slate-400 font-semibold animate-pulse')
        
        loop = asyncio.get_event_loop()
        try:
            # Query attached databases safely inside the main thread before passing to the background thread!
            attached_dbs = []
            try:
                attached_dbs = explorer.conn.execute("SELECT database_name, path FROM duckdb_databases WHERE path IS NOT NULL").fetchall()
            except Exception as db_ex:
                print(f"DEBUG: Could not query databases in main thread: {db_ex}")
                
            stats = await loop.run_in_executor(None, get_table_statistics, schema_name, table_name, database_name, attached_dbs)
            
            # Populate UI with dynamic statistical insights
            inspector_content.clear()
            with inspector_content:
                # Header Section
                with ui.row().classes('w-full items-center justify-between no-wrap border-b border-slate-100 dark:border-slate-800 pb-3'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('analytics', color='primary').classes('text-3xl')
                        with ui.column().classes('gap-0'):
                            ui.label(f"Table Inspector: {table_name}").classes('text-xl font-extrabold text-slate-800 dark:text-white')
                            ui.label(f"Schema Path: {database_name}.{schema_name}.{table_name}").classes('text-xs text-slate-400 dark:text-slate-500 font-mono')
                    ui.button(icon='close', on_click=inspector_dialog.close).props('flat round dense').classes('text-slate-500')
                    
                # High-Level Metrics Row
                with ui.row().classes('w-full gap-4 justify-between no-wrap'):
                    # Rows count card
                    with ui.card().classes('flex-grow p-4 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                        ui.icon('dns', color='secondary').classes('text-2xl')
                        ui.label(f"{stats['total_rows']:,}").classes('text-2xl font-black text-slate-800 dark:text-white')
                        ui.label('Total Rows').classes('text-xs text-slate-400 font-semibold uppercase')
                        
                    # Columns count card
                    with ui.card().classes('flex-grow p-4 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                        ui.icon('view_column', color='primary').classes('text-2xl')
                        ui.label(f"{len(stats['columns'])}").classes('text-2xl font-black text-slate-800 dark:text-white')
                        ui.label('Columns Count').classes('text-xs text-slate-400 font-semibold uppercase')
                        
                    # Disk footprint card
                    with ui.card().classes('flex-grow p-4 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                        ui.icon('save', color='warning').classes('text-2xl')
                        b = stats['disk_size']
                        if b >= 1024*1024:
                            size_str = f"{b/(1024*1024):.2f} MB"
                        elif b >= 1024:
                            size_str = f"{b/1024:.2f} KB"
                        else:
                            size_str = f"{b} Bytes"
                        ui.label(size_str).classes('text-2xl font-black text-slate-800 dark:text-white')
                        ui.label('Est. Size').classes('text-xs text-slate-400 font-semibold uppercase')
                        
                # Table Data Analysis Container
                ui.label('Detailed Column Statistics').classes('text-xs font-bold uppercase text-slate-400 mt-2 pl-1')
                
                with ui.scroll_area().classes('w-full h-80 border border-slate-100 dark:border-slate-800 rounded-lg dark-bg-panel p-2'):
                    with ui.element('table').classes('w-full text-left border-collapse text-xs'):
                        with ui.element('thead').classes('text-slate-400 dark:text-slate-500 uppercase font-semibold border-b border-slate-100 dark:border-slate-800'):
                                with ui.element('tr'):
                                    with ui.element('th').classes('pb-3 pl-2'):
                                        ui.label('Column')
                                    with ui.element('th').classes('pb-3'):
                                        ui.label('Type')
                                    with ui.element('th').classes('pb-3'):
                                        ui.label('Nullity')
                                    with ui.element('th').classes('pb-3'):
                                        ui.label('Distinct')
                                    with ui.element('th').classes('pb-3'):
                                        ui.label('Min')
                                    with ui.element('th').classes('pb-3'):
                                        ui.label('Max')
                                    with ui.element('th').classes('pb-3'):
                                        ui.label('Avg')
                                    with ui.element('th').classes('pb-3'):
                                        ui.label('Std Dev')
                                
                        with ui.element('tbody').classes('text-slate-700 dark:text-slate-200 font-medium'):
                            for c in stats['columns']:
                                n_val = c['nullity']
                                if n_val > 50:
                                    n_cls = 'text-rose-500 font-bold'
                                elif n_val > 10:
                                    n_cls = 'text-amber-500 font-semibold'
                                else:
                                    n_cls = 'text-emerald-500'
                                    
                                with ui.element('tr').classes('border-b border-slate-100/50 dark:border-slate-800/50 hover:bg-slate-50/50 dark:hover:bg-slate-800/30'):
                                    with ui.element('td').classes('py-3 pl-2 font-mono font-bold text-slate-800 dark:text-white'):
                                        ui.label(c['name'])
                                    with ui.element('td').classes('py-3 text-slate-400 font-mono'):
                                        ui.label(c['type'])
                                    with ui.element('td').classes(f'py-3 {n_cls}'):
                                        ui.label(f"{n_val:.1f}%")
                                    with ui.element('td').classes('py-3 font-mono'):
                                        ui.label(f"{c['cardinality']:,}")
                                    with ui.element('td').classes('py-3 font-mono text-slate-500'):
                                        ui.label(c['min'])
                                    with ui.element('td').classes('py-3 font-mono text-slate-500'):
                                        ui.label(c['max'])
                                    with ui.element('td').classes('py-3 font-mono text-slate-500'):
                                        ui.label(c['avg'])
                                    with ui.element('td').classes('py-3 font-mono text-slate-500'):
                                        ui.label(c['stddev'])
                                    
        except Exception as ex:
            inspector_content.clear()
            with inspector_content:
                ui.notify(f"Inspection failed: {str(ex)}", type='negative')
                ui.label(f"Error analyzing table: {str(ex)}").classes('text-rose-500 text-center py-8 w-full font-bold')

    def refresh_databases_list():
        """Fetch all attached databases from duckdb_databases and render them beautifully."""
        def detach_database_action(db_name):
            try:
                explorer.conn.execute(f"DETACH {db_name};")
                remove_attached_database(db_name)
                ui.notify(f"Successfully detached database '{db_name}'", type='success')
                refresh_schema_tree()
            except Exception as e:
                ui.notify(f"Failed to detach database: {str(e)}", type='negative', duration=5)

        databases_container.clear()
        try:
            db_rows = explorer.conn.execute("SELECT database_name, path FROM duckdb_databases ORDER BY database_name").fetchall()
            
            with databases_container:
                for db_name, db_path in db_rows:
                    if db_name in ('system', 'temp') or db_name.startswith('__'):
                        continue
                        
                    is_main = db_name == 'main'
                    badge_color = 'indigo' if is_main else 'emerald'
                    
                    with ui.row().classes('w-full items-center justify-between no-wrap gap-1 py-1 px-1.5 rounded hover:bg-slate-100/50 dark:hover:bg-slate-800/50 transition'):
                        with ui.row().classes('items-center gap-2 no-wrap truncate'):
                            db_icon = 'storage' if is_main else 'cloud_queue'
                            ui.icon(db_icon, color=badge_color).classes('text-sm')
                            with ui.column().classes('gap-0 truncate'):
                                ui.label(db_name).classes('text-xs font-bold text-slate-800 dark:text-slate-100')
                                if db_path:
                                    # Show filename or path
                                    path_display = os.path.basename(db_path) if not db_path.startswith('ducklake:') else db_path
                                    ui.label(path_display).classes('text-[10px] font-mono text-slate-400 truncate').style('max-width: 130px;')
                                else:
                                    ui.label('In-Memory').classes('text-[10px] text-slate-400 font-mono')
                        
                        if not is_main:
                            ui.button(icon='delete', on_click=lambda db=db_name: detach_database_action(db)).props('flat fab-mini dense').classes('text-slate-400 hover:text-rose-500').tooltip('Detach database')
                        else:
                            ui.badge('Active', color=badge_color).classes('text-[8px] py-0.5 px-1.5')
        except Exception as e:
            print(f"Error refreshing databases list: {e}")

    def refresh_schema_tree():
        """Scan all attached databases and schemas to build a 4-level catalog schema tree view."""
        schema_container.clear()
        
        try:
            refresh_databases_list()
        except Exception as e:
            print(f"Error refreshing databases list inside refresh_schema_tree: {e}")
            
        with schema_container:
            # Query active databases from duckdb_databases to ensure empty catalogs are always displayed in the tree!
            try:
                db_rows = explorer.conn.execute("SELECT database_name FROM duckdb_databases ORDER BY database_name").fetchall()
                all_dbs = [row[0] for row in db_rows if row[0] not in ('system', 'temp') and not row[0].startswith('__')]
            except Exception as e:
                print(f"Error querying duckdb_databases: {e}")
                all_dbs = []

            # Query global tables and views across all attached databases, excluding system catalogs
            query_tables = """
                SELECT database_name, schema_name, table_name, 'BASE TABLE' AS table_type 
                FROM duckdb_tables
                WHERE database_name != 'system' AND schema_name NOT IN ('information_schema', 'pg_catalog')
                UNION ALL
                SELECT database_name, schema_name, view_name AS table_name, 'VIEW' AS table_type 
                FROM duckdb_views
                WHERE database_name != 'system' AND schema_name NOT IN ('information_schema', 'pg_catalog')
                ORDER BY database_name, schema_name, table_name;
            """
            try:
                rows = explorer.conn.execute(query_tables).fetchall()
            except Exception as e:
                print(f"Error querying global catalogs: {e}")
                rows = []
                
            if not all_dbs and not rows:
                ui.label('No tables found. Try "Reset & Reseed".').classes('text-xs text-slate-400 text-center py-4 w-full')
                return

            # Group tables by database and schema
            db_dict = defaultdict(lambda: defaultdict(list))
            
            # Seed the db_dict with all active databases so they are always rendered
            for db in all_dbs:
                _ = db_dict[db]
                
            table_types = {}
            for db, schema, table, t_type in rows:
                if db in db_dict:
                    db_dict[db][schema].append(table)
                    table_types[(db, schema, table)] = t_type

            nodes = []
            for db_name, schemas in db_dict.items():
                if db_name.startswith('__') or db_name == 'system':
                    continue
                    
                db_node = {
                    'id': db_name,
                    'label': f"Database: {db_name}",
                    'icon': 'storage',  # storage icon for database/lakehouse
                    'expanded': False,
                    'children': []
                }
                
                for schema_name, tables in schemas.items():
                    schema_node = {
                        'id': f"{db_name}.{schema_name}",
                        'label': f"Schema: {schema_name}",
                        'icon': 'folder',
                        'expanded': False,
                        'children': []
                    }
                    
                    for table in tables:
                        table_id = f"{db_name}.{schema_name}.{table}"
                        # Fetch columns for this table using database and schema scope
                        cols = explorer.list_columns_with_types(table, database=db_name, schema=schema_name)
                        col_nodes = [
                            {
                                'id': f"{table_id}.{c_name}",
                                'label': f"{c_name} ({c_type})",
                                'icon': 'label'
                            } for c_name, c_type in cols
                        ]
                        
                        t_type = table_types.get((db_name, schema_name, table), 'BASE TABLE')
                        table_icon = 'visibility' if t_type == 'VIEW' else 'table_chart'
                        
                        table_node = {
                            'id': table_id,
                            'label': table,
                            'icon': table_icon,
                            'children': col_nodes,
                            'expanded': False  # tables are collapsed by default!
                        }
                        schema_node['children'].append(table_node)
                    db_node['children'].append(schema_node)
                nodes.append(db_node)

            # Bottom-up filtering & auto-expansion
            filter_text = schema_filter_input.value.strip().lower() if schema_filter_input and schema_filter_input.value else ""
            expanded_keys = []
            if filter_text:
                filtered_nodes = []
                for db_node in nodes:
                    db_match = filter_text in db_node['label'].lower() or filter_text in db_node['id'].lower()
                    
                    filtered_schemas = []
                    for schema_node in db_node['children']:
                        schema_match = db_match or filter_text in schema_node['label'].lower() or filter_text in schema_node['id'].lower()
                        
                        filtered_tables = []
                        for table_node in schema_node['children']:
                            table_match = schema_match or filter_text in table_node['label'].lower() or filter_text in table_node['id'].lower()
                            
                            filtered_cols = []
                            for col_node in table_node['children']:
                                col_match = table_match or filter_text in col_node['label'].lower() or filter_text in col_node['id'].lower()
                                if col_match:
                                    filtered_cols.append(col_node)
                                    
                            if table_match or filtered_cols:
                                new_table_node = dict(table_node)
                                new_table_node['children'] = filtered_cols
                                filtered_tables.append(new_table_node)
                                expanded_keys.append(table_node['id'])
                                
                        if schema_match or filtered_tables:
                            new_schema_node = dict(schema_node)
                            new_schema_node['children'] = filtered_tables
                            filtered_schemas.append(new_schema_node)
                            expanded_keys.append(schema_node['id'])
                            
                    if db_match or filtered_schemas:
                        new_db_node = dict(db_node)
                        new_db_node['children'] = filtered_schemas
                        filtered_nodes.append(new_db_node)
                        expanded_keys.append(db_node['id'])
                nodes = filtered_nodes

            # Debugging logs removed to prevent file modification reloads
            pass

            async def handle_node_click(e):
                print(f"DEBUG: handle_node_click triggered with value: {e.value}", flush=True)
                val = e.value
                if not val:
                    return
                parts = val.split('.')
                # If len(parts) == 3, it is a Table node (e.g. database.schema.table)
                if len(parts) == 3:
                    db_name = parts[0]
                    schema_name = parts[1]
                    tbl_name = parts[2]
                    
                    # Trigger the Table Inspector overlay immediately!
                    asyncio.create_task(open_table_inspector(schema_name, tbl_name, db_name))
                    
                    # Fully qualified select statement to prevent namespace collisions
                    cols = explorer.list_columns_with_types(tbl_name, database=db_name, schema=schema_name)
                    if cols:
                        sql = format_column_projection_query(cols, f"{db_name}.{schema_name}.{tbl_name}")
                    else:
                        sql = f"SELECT * FROM {db_name}.{schema_name}.{tbl_name} LIMIT 100;"
                    sql_editor.value = sql
                    run_editor_query()
                    
            tree_widget = ui.tree(nodes, label_key='label', on_select=handle_node_click).props('dense accordion').classes('text-slate-800 dark:text-slate-100')
            if filter_text and expanded_keys:
                tree_widget.expand(expanded_keys)
                    
    def handle_tab_change_global(value):
        """Callback to store the selected tab globally in user session storage and trigger tab change logic."""
        try:
            app.storage.user['active_tab'] = value
        except Exception:
            pass
        handle_tab_change(value)

    def handle_tab_change(value):
        """Callback to detect global tab changes and auto-refresh the extensions list or seeding metrics when their tabs are opened."""
        if value == 'Extensions':
            refresh_extensions_grid()
        elif value == 'Database Tools':
            refresh_seeding_metrics()
        elif value == 'API Endpoints':
            refresh_api_endpoints_grid()
        elif value == 'Scheduler':
            refresh_scheduler_jobs_list()
            refresh_scheduler_logs_table()

    def refresh_extensions_grid():
        """Fetch available extensions from DuckDB's local metadata catalog and render beautiful, interactive cards inside the grid wrapper."""
        try:
            rows = explorer.conn.execute("SELECT extension_name, loaded, installed, description, extension_version, installed_from FROM duckdb_extensions() ORDER BY extension_name;").fetchall()
        except Exception as ex:
            ui.notify(f"Failed to query extensions catalog: {ex}", type='negative')
            return
            
        extensions_grid.clear()
        
        search_query = ext_search.value.strip().lower() if ext_search.value else ""
        filter_mode = ext_filter.value
        
        ext_list = []
        for r in rows:
            name, loaded, installed, desc, version, source = r
            
            # Apply search filter
            if search_query and (search_query not in name.lower() and (desc and search_query not in desc.lower())):
                continue
                
            # Apply quick filters
            if filter_mode == 'installed' and not installed:
                continue
            if filter_mode == 'loaded' and not loaded:
                continue
            if filter_mode == 'core' and source != 'core':
                continue
                
            ext_list.append({
                'name': name,
                'loaded': loaded,
                'installed': installed,
                'desc': desc if desc else "Provides analytical and database engine utilities.",
                'version': version if version else "N/A",
                'source': source if source else "repository"
            })
            
        if not ext_list:
            with extensions_grid:
                with ui.column().classes('col-span-full items-center justify-center py-12 gap-2 text-slate-400 w-full'):
                    ui.icon('search_off', size='lg')
                    ui.label('No matching extensions found. Try adjusting your filter or search query.').classes('text-sm font-semibold text-center')
            return
            
        with extensions_grid:
            for ext in ext_list:
                # Local binding helper to prevent lambda capture loop scoping issues
                def make_click_handler(name=ext['name'], is_installed=ext['installed']):
                    return lambda _: asyncio.create_task(trigger_extension_action(name, is_installed))
                    
                with ui.card().classes('p-5 border border-slate-200 dark:border-slate-800 shadow-sm dark-bg-panel hover:shadow-md transition gap-3 rounded-xl flex-col justify-between h-56'):
                    with ui.column().classes('gap-2 w-full'):
                        # Header Row
                        with ui.row().classes('w-full justify-between items-center no-wrap'):
                            ui.label(ext['name']).classes('text-base font-extrabold text-slate-800 dark:text-white font-mono')
                            
                            # Status Badges
                            with ui.row().classes('gap-1 items-center'):
                                if ext['loaded']:
                                    ui.label('Loaded').classes('text-[10px] font-bold text-emerald-600 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-950/30 px-2 py-0.5 rounded-full border border-emerald-200 dark:border-emerald-900')
                                if ext['installed']:
                                    ui.label('Installed').classes('text-[10px] font-bold text-indigo-600 bg-indigo-50 dark:text-indigo-400 dark:bg-indigo-950/30 px-2 py-0.5 rounded-full border border-indigo-200 dark:border-indigo-900')
                                else:
                                    ui.label('Available').classes('text-[10px] font-bold text-slate-500 bg-slate-100 dark:text-slate-400 dark:bg-slate-800 px-2 py-0.5 rounded-full border border-slate-200 dark:border-slate-700')
                                    
                        # Description
                        ui.label(ext['desc']).classes('text-xs text-slate-500 dark:text-slate-400 line-clamp-3 leading-relaxed')
                        
                    # Footer actions
                    with ui.row().classes('w-full justify-between items-center border-t border-slate-100 dark:border-slate-800/80 pt-3 mt-auto no-wrap'):
                        with ui.column().classes('gap-0 text-[10px] text-slate-400 font-semibold uppercase'):
                            ui.label(f"Ver: {ext['version']}")
                            ui.label(f"Src: {ext['source']}")
                            
                        # Action Button
                        if ext['loaded']:
                            with ui.row().classes('items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-bold text-xs pr-1'):
                                ui.icon('check_circle', size='xs')
                                ui.label('Active')
                        elif ext['installed']:
                            ui.button('Load', icon='power', color='primary',
                                      on_click=make_click_handler()).props('dense elevated').classes('px-2.5 text-xs')
                        else:
                            ui.button('Install', icon='download', color='secondary',
                                      on_click=make_click_handler()).props('dense elevated').classes('px-2.5 text-xs')

    async def trigger_extension_action(ext_name, is_installed):
        """Execute database INSTALL/LOAD statements inside an asynchronous background executor to keep UI thread fully responsive."""
        if not is_installed:
            action_text = f"installing and loading extension '{ext_name}'"
            sql = f"INSTALL {ext_name}; LOAD {ext_name};"
        else:
            action_text = f"loading extension '{ext_name}'"
            sql = f"LOAD {ext_name};"
            
        ui.notify(f"Initiating {action_text} in background thread...", type='info')
        
        loop = asyncio.get_event_loop()
        def run_db_op():
            # Run query safely on connection thread
            explorer.conn.execute(sql)
            
        try:
            await loop.run_in_executor(None, run_db_op)
            ui.notify(f"Successfully loaded extension '{ext_name}'!", type='success')
            refresh_extensions_grid()
        except Exception as e:
            ui.notify(f"Failed to execute extension operation: {e}", type='negative', duration=5)

    def refresh_seeding_metrics():
        """Query real-time row counts for the synthetic database tables and color Quasar badges accordingly."""
        explorer.close()
        
        def check_row_count(table_name):
            try:
                res = explorer.conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
                return res[0] if res else 0
            except Exception as ex:
                print(f"DEBUG: check_row_count failed for {table_name}: {ex}", flush=True)
                return 0
                
        tx_count = check_row_count('sales_transactions')
        cust_count = check_row_count('customer_profiles')
        inv_count = check_row_count('product_inventory')
        
        def get_color(cnt):
            if cnt == 0:
                return 'grey'
            elif cnt < 2000:
                return 'indigo'
            elif cnt < 8000:
                return 'teal'
            else:
                return 'orange'
        
        trans_badge.set_text(f"{tx_count:,} Rows")
        trans_badge.props(f"color={get_color(tx_count)}")
        trans_badge.update()
        
        cust_badge.set_text(f"{cust_count:,} Rows")
        cust_badge.props(f"color={get_color(cust_count)}")
        cust_badge.update()
        
        invent_badge.set_text(f"{inv_count:,} Rows")
        invent_badge.props(f"color={get_color(inv_count)}")
        invent_badge.update()
        
        # Dynamically populate export database select options
        try:
            db_rows = explorer.conn.execute("SELECT database_name FROM duckdb_databases").fetchall()
            dbs = [row[0] for row in db_rows if row[0] not in ('system', 'temp') and not row[0].startswith('__')]
            options = {db: db for db in dbs}
            export_db_select.options = options
            
            # Select current active database context
            current_active = explorer.conn.execute("SELECT current_database()").fetchone()[0]
            if export_db_select.value not in options:
                if current_active in options:
                    export_db_select.value = current_active
                elif dbs:
                    export_db_select.value = dbs[0]
            export_db_select.update()
        except Exception as db_err:
            print(f"DEBUG: Failed to update export_db_select options: {db_err}", flush=True)

    async def trigger_db_export(target_path, format_type, selected_db=None):
        """Export all catalog tables, structures, and schemas from the selected database to a local directory."""
        if not target_path or not target_path.strip():
            ui.notify("Please specify a valid export directory path.", type='warning')
            return
            
        target_path = os.path.abspath(target_path.strip())
        
        # Determine database name if not specified
        if not selected_db:
            try:
                selected_db = explorer.conn.execute("SELECT current_database()").fetchone()[0]
            except Exception:
                selected_db = 'memory'
                
        ui.notify(f"Starting database export ({format_type}) for database '{selected_db}' to: {target_path}...", type='info')
        
        loop = asyncio.get_event_loop()
        def do_export():
            os.makedirs(target_path, exist_ok=True)
            
            # Switch active database context if a specific database is selected
            original_db = None
            if selected_db:
                try:
                    original_db = explorer.conn.execute("SELECT current_database()").fetchone()[0]
                    explorer.conn.execute(f"USE {selected_db};")
                except Exception as switch_ex:
                    print(f"Error switching database to {selected_db}: {switch_ex}", flush=True)
            
            try:
                if format_type.upper() == 'SQL':
                    import datetime
                    backup_file = os.path.join(target_path, "backup.sql")
                    
                    # Fetch sequences for selected database
                    try:
                        seq_rows = explorer.conn.execute(f"""
                            SELECT sql FROM duckdb_sequences() 
                            WHERE database_name = '{selected_db}'
                              AND schema_name NOT IN ('information_schema', 'pg_catalog')
                        """).fetchall()
                        sequences_ddl = [r[0] for r in seq_rows if r[0]]
                    except Exception as e:
                        print(f"Error querying sequences: {e}", flush=True)
                        sequences_ddl = []
                        
                    # Fetch tables DDLs and names for selected database
                    try:
                        tables_rows = explorer.conn.execute(f"""
                            SELECT table_name, sql FROM duckdb_tables()
                            WHERE database_name = '{selected_db}'
                              AND NOT internal 
                              AND NOT temporary
                              AND schema_name NOT IN ('information_schema', 'pg_catalog')
                        """).fetchall()
                        tables_ddl = [r[1] for r in tables_rows if r[1]]
                        table_names = [r[0] for r in tables_rows if r[0]]
                    except Exception as e:
                        print(f"Error querying duckdb_tables: {e}", flush=True)
                        tables_ddl = []
                        table_names = []
                        
                    # Fetch views DDLs for selected database
                    try:
                        views_rows = explorer.conn.execute(f"""
                            SELECT sql FROM duckdb_views()
                            WHERE database_name = '{selected_db}'
                              AND NOT internal 
                              AND NOT temporary
                              AND schema_name NOT IN ('information_schema', 'pg_catalog')
                        """).fetchall()
                        views_ddl = [r[0] for r in views_rows if r[0]]
                    except Exception as e:
                        print(f"Error querying duckdb_views: {e}", flush=True)
                        views_ddl = []
                    
                    # Write to backup.sql
                    with open(backup_file, 'w', encoding='utf-8') as f:
                        f.write("-- DuckDB Standard SQL Backup\n")
                        f.write(f"-- Selected Database: {selected_db}\n")
                        f.write(f"-- Generated on: {datetime.datetime.now().isoformat()}\n\n")
                        
                        if sequences_ddl:
                            f.write("-- === SEQUENCES ===\n")
                            for ddl in sequences_ddl:
                                f.write(f"{ddl.strip()};\n")
                            f.write("\n")
                            
                        if tables_ddl:
                            f.write("-- === TABLES ===\n")
                            for ddl in tables_ddl:
                                f.write(f"{ddl.strip()};\n")
                            f.write("\n")
                            
                        if table_names:
                            f.write("-- === DATA INSERTIONS ===\n")
                            f.write("BEGIN TRANSACTION;\n\n")
                            for tbl in table_names:
                                f.write(f"-- Data for table {tbl}\n")
                                try:
                                    res = explorer.conn.execute(f"SELECT * FROM {tbl}")
                                    cols = [desc[0] for desc in res.description]
                                    cols_str = ", ".join(f'"{c}"' for c in cols)
                                    rows = res.fetchall()
                                    for row in rows:
                                        vals = []
                                        for val in row:
                                            if val is None:
                                                vals.append("NULL")
                                            elif isinstance(val, bool):
                                                vals.append("TRUE" if val else "FALSE")
                                            elif isinstance(val, (int, float)):
                                                vals.append(str(val))
                                            elif isinstance(val, (datetime.datetime, datetime.date)):
                                                vals.append(f"'{val.isoformat()}'")
                                            elif isinstance(val, bytes):
                                                vals.append(f"X'{val.hex()}'")
                                            else:
                                                s = str(val).replace("'", "''")
                                                vals.append(f"'{s}'")
                                        vals_str = ", ".join(vals)
                                        f.write(f"INSERT INTO {tbl} ({cols_str}) VALUES ({vals_str});\n")
                                    f.write("\n")
                                except Exception as data_ex:
                                    print(f"Error exporting data for table {tbl}: {data_ex}", flush=True)
                            f.write("COMMIT;\n\n")
                            
                        if views_ddl:
                            f.write("-- === VIEWS ===\n")
                            for ddl in views_ddl:
                                f.write(f"{ddl.strip()};\n")
                            f.write("\n")
                else:
                    escaped_path = target_path.replace("'", "''")
                    sql = f"EXPORT DATABASE '{escaped_path}' (FORMAT {format_type});"
                    explorer.conn.execute(sql)
            finally:
                if original_db:
                    try:
                        explorer.conn.execute(f"USE {original_db};")
                    except Exception as restore_ex:
                        print(f"Error restoring original database context: {restore_ex}", flush=True)
            
        try:
            await loop.run_in_executor(None, do_export)
            ui.notify(f"Database exported successfully to {target_path}!", type='positive')
        except Exception as e:
            ui.notify(f"Export failed: {e}", type='negative', duration=7)

    async def trigger_db_import(source_path):
        """Re-create catalog tables and load bulk data from a previously exported directory."""
        if not source_path or not source_path.strip():
            ui.notify("Please specify a valid import directory path.", type='warning')
            return
            
        source_path = os.path.abspath(source_path.strip())
        if not os.path.exists(source_path) or not os.path.isdir(source_path):
            ui.notify(f"Import directory does not exist: {source_path}", type='negative')
            return
            
        schema_file = os.path.join(source_path, "schema.sql")
        backup_file = os.path.join(source_path, "backup.sql")
        
        is_sql_format = os.path.exists(backup_file)
        if not is_sql_format and not os.path.exists(schema_file):
            ui.notify(f"Import directory must contain schema.sql or backup.sql", type='negative')
            return
            
        ui.notify(f"Starting database import from: {source_path}...", type='info')
        
        loop = asyncio.get_event_loop()
        def do_import():
            # Dynamically parse DDL schema/backup file to find all tables, views, and sequences to drop them
            tables_to_drop = []
            views_to_drop = []
            sequences_to_drop = []
            
            parse_file = backup_file if is_sql_format else schema_file
            if os.path.exists(parse_file):
                try:
                    with open(parse_file, 'r') as sf:
                        for line in sf:
                            line_upper = line.upper().strip()
                            if line_upper.startswith("CREATE TABLE "):
                                parts = line.split()
                                if len(parts) >= 3:
                                    tbl_name = parts[2].split('(')[0].strip('("` \t;')
                                    if '.' in tbl_name:
                                        tbl_name = tbl_name.split('.')[-1]
                                    tables_to_drop.append(tbl_name)
                            elif line_upper.startswith("CREATE VIEW "):
                                parts = line.split()
                                if len(parts) >= 3:
                                    view_name = parts[2].split('(')[0].strip('("` \t;')
                                    if '.' in view_name:
                                        view_name = view_name.split('.')[-1]
                                    views_to_drop.append(view_name)
                            elif line_upper.startswith("CREATE SEQUENCE "):
                                parts = line.split()
                                if len(parts) >= 3:
                                    seq_name = parts[2].split('(')[0].strip('("` \t;')
                                    if '.' in seq_name:
                                        seq_name = seq_name.split('.')[-1]
                                    sequences_to_drop.append(seq_name)
                except Exception as parse_ex:
                    print(f"DEBUG: Failed to parse {parse_file}: {parse_ex}")

            print(f"DEBUG: Pre-cleaning catalog - Tables: {tables_to_drop}, Views: {views_to_drop}, Sequences: {sequences_to_drop}", flush=True)
            
            # Drop views first to avoid view dependency conflicts
            for view in views_to_drop:
                try:
                    explorer.conn.execute(f"DROP VIEW IF EXISTS {view};")
                except Exception as drop_ex:
                    print(f"DEBUG: Failed to drop view {view}: {drop_ex}", flush=True)
                    
            # Drop tables next
            for tbl in tables_to_drop:
                try:
                    explorer.conn.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")
                except Exception as drop_ex:
                    print(f"DEBUG: Failed to drop table {tbl}: {drop_ex}", flush=True)
                    
            # Drop sequences last
            for seq in sequences_to_drop:
                try:
                    explorer.conn.execute(f"DROP SEQUENCE IF EXISTS {seq};")
                except Exception as drop_ex:
                    print(f"DEBUG: Failed to drop sequence {seq}: {drop_ex}", flush=True)

            if is_sql_format:
                with open(backup_file, 'r', encoding='utf-8') as f:
                    full_sql = f.read()
                explorer.conn.execute(full_sql)
            else:
                escaped_path = source_path.replace("'", "''")
                sql = f"IMPORT DATABASE '{escaped_path}';"
                explorer.conn.execute(sql)
            
        try:
            await loop.run_in_executor(None, do_import)
            ui.notify("Database imported/restored successfully!", type='positive')
            refresh_schema_tree()
            refresh_seeding_metrics()
        except Exception as e:
            print(f"DEBUG: trigger_db_import failed: {e}", flush=True)
            ui.notify(f"Import failed: {e}", type='negative', duration=7)

    async def trigger_custom_seed(density_value):
        """Wipe and re-seed the core database tables according to chosen densities, running asynchronously to prevent UI lockup."""
        if density_value == '1000':
            num_customers = 100
            num_transactions = 1000
            density_label = "1,000"
        elif density_value == '15000':
            num_customers = 1000
            num_transactions = 15000
            density_label = "15,000"
        else:
            num_customers = 400
            num_transactions = 6500
            density_label = "6,500"
            
        ui.notify(f"Re-seeding database with {density_label} sales records... Please wait.", type='info')
        
        loop = asyncio.get_event_loop()
        def do_seeding():
            explorer.close()
            try:
                success = seed_database(DB_NAME, force=True, num_customers=num_customers, num_transactions=num_transactions)
                return success
            finally:
                explorer.__init__(DB_NAME)
                
        try:
            success = await loop.run_in_executor(None, do_seeding)
            if success:
                ui.notify(f"Successfully re-seeded database with {density_label} sales records!", type='positive')
                refresh_schema_tree()
                refresh_seeding_metrics()
                try:
                    populate_builder_tables()
                except Exception:
                    pass
            else:
                ui.notify("Failed to re-seed database. Check logs.", type='negative')
        except Exception as e:
            ui.notify(f"Error during re-seeding: {e}", type='negative')

    def format_sql_query():
        """Apply basic SQL formatting rules to clean up the editor."""
        raw = sql_editor.value.strip()
        if not raw:
            return
        
        keywords = ["SELECT ", "FROM ", "WHERE ", "JOIN ", "LEFT JOIN ", "GROUP BY ", "ORDER BY ", "LIMIT ", "HAVING "]
        formatted = raw
        for kw in keywords:
            formatted = formatted.replace(kw.lower(), "\n" + kw.strip() + " ")
            formatted = formatted.replace(kw.upper(), "\n" + kw.strip() + " ")
            formatted = formatted.replace(kw, "\n" + kw.strip() + " ")
        
        lines = [line.strip() for line in formatted.split('\n') if line.strip()]
        sql_editor.value = "\n".join(lines)
        ui.notify("SQL query formatted", type='info')

    def update_query_history_list():
        """Repopulate the query history list under the session history tab."""
        history_container.clear()
        with history_container:
            for idx, q_sql in enumerate(reversed(query_history)):
                # Use a specific local binding helper to prevent lambda capture loop issue
                def make_click_handler(s=q_sql):
                    return lambda _: load_history_query(s)
                
                with ui.card().classes('w-full p-3 border rounded shadow-none hover:bg-slate-50 dark:hover:bg-slate-900 cursor-pointer transition').on('click', make_click_handler()):
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label(q_sql).classes('text-xs font-mono truncate flex-grow').style('max-width: 85%;')
                        ui.icon('keyboard_arrow_right', color='slate')

    def load_history_query(sql_str):
        sql_editor.value = sql_str
        ui.notify("Query loaded to editor.", type='info')

    def run_editor_query():
        """Execute the SQL code in the editor, and update tables, statistics, and graphs."""
        sql = sql_editor.value.strip()
        if not sql:
            ui.notify('Please write a query first!', type='warning')
            return
            
        # Append to query history if it's new
        if sql not in query_history:
            query_history.append(sql)
            if len(query_history) > 10:
                query_history.pop(0)
            update_query_history_list()
            
        status_label.text = "Running query..."
        
        # Check for ducklake attach statement and create target directories if needed
        if "ducklake:" in sql.lower() and "attach " in sql.lower():
            import re
            # Ensure ducklake extension is loaded
            try:
                explorer.conn.execute("INSTALL ducklake; LOAD ducklake;")
            except Exception:
                pass
                
            # Extract metadata db file path
            meta_match = re.search(r"attach\s+'ducklake:([^']+)'", sql, re.IGNORECASE)
            if meta_match:
                meta_path = meta_match.group(1).strip()
                meta_dir = os.path.dirname(meta_path)
                if meta_dir:
                    os.makedirs(meta_dir, exist_ok=True)
                    
            # Extract data parquet folder path
            data_match = re.search(r"data_path\s+'([^']+)'", sql, re.IGNORECASE)
            if data_match:
                data_path = data_match.group(1).strip()
                os.makedirs(data_path, exist_ok=True)
        
        # Run the query in DuckDB
        res = explorer.query(sql)
        
        if 'error' in res:
            status_label.text = f"Error occurred (duration: {res['duration_ms']}ms)"
            ui.notify(f"Query Error: {res['error']}", type='negative', duration=5)
            result_table.columns = []
            result_table.rows = []
            csv_download_btn.disable()
            parquet_download_btn.disable()
            return
            
        # Capture successful attach/detach queries to replicate in thread sessions
        if "attach " in sql.lower():
            import re
            match = re.search(r"attach\s+(?:database\s+)?('[^']+'|\"[^\"]+\")\s+as\s+(\w+)", sql, re.IGNORECASE)
            if match:
                db_alias = match.group(2)
                if not hasattr(explorer, 'attached_dbs_queries'):
                    explorer.attached_dbs_queries = {}
                explorer.attached_dbs_queries[db_alias] = sql
                print(f"DEBUG: Captured ATTACH statement for {db_alias}: {sql}", flush=True)
        elif "detach " in sql.lower():
            import re
            match = re.search(r"detach\s+(?:database\s+)?(\w+)", sql, re.IGNORECASE)
            if match:
                db_alias = match.group(1)
                if hasattr(explorer, 'attached_dbs_queries') and db_alias in explorer.attached_dbs_queries:
                    del explorer.attached_dbs_queries[db_alias]
                    print(f"DEBUG: Removed ATTACH statement for detached database {db_alias}", flush=True)

        # Force refresh of schema and databases tree if attach/detach statements are executed
        if "attach " in sql.lower() or "detach " in sql.lower():
            refresh_schema_tree()

        # Display success metrics
        status_label.text = f"Completed in {res['duration_ms']}ms | Rows: {res['affected_rows']}"
        
        if res['is_select']:
            cols = [{'name': name, 'label': name, 'field': name, 'sortable': True} for name in res['columns']]
            result_table.columns = cols
            
            mapped_rows = [dict(zip(res['columns'], row)) for row in res['data']]
            result_table.rows = mapped_rows
            
            current_results['columns'] = res['columns']
            current_results['rows'] = mapped_rows
            csv_download_btn.enable()
            parquet_download_btn.enable()
            
            # Reset chart container to the initial placeholder label
            chart_container.clear()
            with chart_container:
                ui.label('Execute a SELECT query and configure X/Y columns to build a chart.').classes('text-slate-400')
            
            # Reset map container to the initial placeholder label
            map_container.clear()
            with map_container:
                ui.label('Execute a SELECT query with coordinates to plot a map.').classes('text-slate-400')
            
            # Reset profiler container to the initial placeholder label
            profiler_container.clear()
            with profiler_container:
                ui.label('Click "Profile Query" or use "Explain Query" to analyze execution plan.').classes('text-slate-400')
            
            x_axis_select.options = res['columns']
            y_axis_select.options = res['columns']
            
            if len(res['columns']) >= 2:
                x_axis_select.value = res['columns'][0]
                y_axis_select.value = res['columns'][1]
            elif len(res['columns']) == 1:
                x_axis_select.value = res['columns'][0]
                y_axis_select.value = res['columns'][0]
                
            # Populate Map options
            lat_select.options = res['columns']
            lng_select.options = res['columns']
            label_select.options = [''] + res['columns']
            
            # Automatically detect coordinate and label columns
            detected_lat = None
            detected_lng = None
            detected_label = None
            
            for col in res['columns']:
                col_lower = col.lower()
                if not detected_lat and ('latitude' in col_lower or col_lower == 'lat'):
                    detected_lat = col
                elif not detected_lng and ('longitude' in col_lower or col_lower in ('lon', 'lng', 'long')):
                    detected_lng = col
                elif not detected_label and any(k in col_lower for k in ('name', 'label', 'city', 'location', 'address', 'agency', 'descriptor', 'title')):
                    detected_label = col
            
            lat_select.value = detected_lat or (res['columns'][0] if len(res['columns']) > 0 else None)
            lng_select.value = detected_lng or (res['columns'][1] if len(res['columns']) > 1 else (res['columns'][0] if len(res['columns']) > 0 else None))
            label_select.value = detected_label or ''
                
            ui.notify("Query executed successfully. Displaying results grid.", type='success')
        else:
            result_table.columns = [{'name': 'Result', 'label': 'Execution Log', 'field': 'Result'}]
            result_table.rows = [{'Result': f"DML query executed. Rows affected: {res['affected_rows']}"}]
            csv_download_btn.disable()
            parquet_download_btn.disable()
            ui.notify(f"DML Query succeeded. {res['affected_rows']} rows affected.", type='success')
            
        # Ensure schema browser tree is refreshed to capture all catalog changes immediately
        refresh_schema_tree()

    def trigger_csv_download():
        """Trigger standard browser CSV download for the active query results."""
        if not current_results['columns'] or not current_results['rows']:
            ui.notify('No active data to export!', type='warning')
            return
        csv_bytes = get_csv_bytes(current_results['columns'], current_results['rows'])
        ui.download(csv_bytes, f"duckdb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        ui.notify('CSV Export completed successfully.', type='success')

    def trigger_parquet_download():
        """Trigger standard browser Parquet download for the active query results."""
        if not current_results['columns'] or not current_results['rows']:
            ui.notify('No active data to export!', type='warning')
            return
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
            table = pa.Table.from_pylist(current_results['rows'])
            sink = pa.BufferOutputStream()
            pq.write_table(table, sink)
            parquet_bytes = sink.getvalue().to_pybytes()
            ui.download(parquet_bytes, f"duckdb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.parquet")
            ui.notify('Parquet Export completed successfully.', type='success')
        except Exception as ex:
            ui.notify(f"Parquet Export failed: {ex}", type='negative')

    def render_chart():
        """Read the active result dataset and build a responsive Apache EChart plotting."""
        x_col = x_axis_select.value
        y_col = y_axis_select.value
        chart_type = chart_type_select.value
        
        if not x_col or not y_col:
            ui.notify('Please select both X and Y columns to plot!', type='warning')
            return
            
        rows = current_results['rows']
        if not rows:
            ui.notify('No data available in grid to plot.', type='warning')
            return
            
        x_data = []
        y_data = []
        
        for r in rows:
            val_x = r.get(x_col, '')
            val_y = r.get(y_col, 0)
            
            if isinstance(val_x, (datetime, timedelta)):
                val_x = str(val_x)
                
            try:
                val_y = float(val_y) if val_y is not None else 0
            except (ValueError, TypeError):
                val_y = 0
                
            x_data.append(val_x)
            y_data.append(val_y)
            
        if len(x_data) > 150:
            x_data = x_data[:150]
            y_data = y_data[:150]
            ui.notify('Dataset capped at 150 points for optimal visualization performance.', type='info')
            
        # Prepare and render the chart dynamically in the container
        
        if chart_type == 'pie':
            pie_data = [{'name': str(x), 'value': y} for x, y in zip(x_data, y_data)]
            options = {
                'title': {
                    'text': f'{y_col} distribution by {x_col}',
                    'left': 'center',
                    'textStyle': {'fontFamily': 'Outfit, Inter, sans-serif'}
                },
                'tooltip': {
                    'trigger': 'item',
                    'formatter': '{a} <br/>{b} : {c} ({d}%)'
                },
                'legend': {
                    'orient': 'vertical',
                    'left': 'left',
                    'type': 'scroll',
                    'show': len(pie_data) < 25
                },
                'series': [
                    {
                        'name': y_col,
                        'type': 'pie',
                        'radius': '55%',
                        'center': ['50%', '60%'],
                        'data': pie_data,
                        'emphasis': {
                            'itemStyle': {
                                'shadowBlur': 10,
                                'shadowOffsetX': 0,
                                'shadowColor': 'rgba(0, 0, 0, 0.5)'
                            }
                        }
                    }
                ]
            }
        else:
            options = {
                'title': {
                    'text': f'{y_col} by {x_col}',
                    'left': 'center',
                    'textStyle': {'fontFamily': 'Outfit, Inter, sans-serif'}
                },
                'tooltip': {
                    'trigger': 'axis',
                    'axisPointer': {'type': 'shadow'}
                },
                'xAxis': {
                    'type': 'category',
                    'data': x_data,
                    'axisLabel': {'rotate': 30, 'interval': 'auto'}
                },
                'yAxis': {
                    'type': 'value'
                },
                'series': [{
                    'name': y_col,
                    'type': chart_type,
                    'data': y_data,
                    'itemStyle': {
                        'color': '#6366f1' if chart_type == 'bar' else '#3b82f6'
                    },
                    'smooth': True
                }]
            }
            
        chart_container.clear()
        with chart_container:
            ui.echart(options=options).classes('w-full h-full')
        ui.notify(f'Successfully generated {chart_type} chart!', type='success')
        
    def render_map():
        """Read the active result dataset and build an interactive Leaflet map plotting coordinates."""
        lat_col = lat_select.value
        lng_col = lng_select.value
        label_col = label_select.value
        
        if not lat_col or not lng_col:
            ui.notify('Please select both Latitude and Longitude columns to plot!', type='warning')
            return
            
        rows = current_results['rows']
        if not rows:
            ui.notify('No data available in grid to plot.', type='warning')
            return
            
        valid_points = []
        for r in rows:
            try:
                lat_val = r.get(lat_col)
                lng_val = r.get(lng_col)
                
                if lat_val is None or lng_val is None:
                    continue
                    
                lat_float = float(lat_val)
                lng_float = float(lng_val)
                
                # Check for physical coordinates bounds
                if -90 <= lat_float <= 90 and -180 <= lng_float <= 180:
                    label_val = str(r.get(label_col, '')) if label_col and r.get(label_col) is not None else f"Coord: {lat_float:.5f}, {lng_float:.5f}"
                    valid_points.append({'lat': lat_float, 'lng': lng_float, 'label': label_val})
            except (ValueError, TypeError):
                pass
                
        if not valid_points:
            ui.notify('No valid coordinates found in the active dataset. Ensure they are numerical coordinates.', type='warning')
            return
            
        if len(valid_points) > 200:
            valid_points = valid_points[:200]
            ui.notify('Plotting capped at 200 markers for optimal map performance.', type='info')
            
        # Calculate average center
        avg_lat = sum(p['lat'] for p in valid_points) / len(valid_points)
        avg_lng = sum(p['lng'] for p in valid_points) / len(valid_points)
        
        map_container.clear()
        
        async def init_map():
            with map_container:
                m = ui.leaflet(center=(avg_lat, avg_lng), zoom=10).classes('w-full h-full')
            await m.initialized()
            for p in valid_points:
                marker = m.marker(latlng=(p['lat'], p['lng']))
                m.run_layer_method(marker.id, 'bindPopup', p['label'])
                m.run_layer_method(marker.id, 'bindTooltip', p['label'])
                
        import asyncio
        asyncio.create_task(init_map())
        ui.notify(f"Successfully plotted {len(valid_points)} points on the map!", type='success')

    def trigger_explain_query():
        """Switch active tab to Query Profiler and run the profiler query."""
        result_tabs.set_value(profile_tab)
        run_profiler_query()

    def copy_plan_to_clipboard(text):
        """Helper to copy query plan text to browser clipboard using JS API."""
        ui.run_javascript(f"navigator.clipboard.writeText({repr(text)})")
        ui.notify('Plan copied to clipboard!', type='success')

    def json_plan_to_mermaid(json_str: str) -> str:
        """Parse DuckDB's FORMAT JSON explain structure into a styled Mermaid TD flowchart."""
        import json
        try:
            plan = json.loads(json_str)
            nodes = []
            connections = []
            node_counter = [0]
            
            def traverse(node, parent_id=None):
                node_counter[0] += 1
                node_id = f"node{node_counter[0]}"
                
                name = node.get("name", node.get("operator_name", "Unknown"))
                extra = node.get("extra_info", {})
                
                cardinality = extra.get("Estimated Cardinality", extra.get("cardinality", ""))
                timing = node.get("operator_timing", None)
                
                label_parts = [f"<b>{name}</b>"]
                if timing is not None:
                    if timing >= 1.0:
                        label_parts.append(f"Time: {timing:.2f}s")
                    elif timing >= 0.001:
                        label_parts.append(f"Time: {timing*1000:.2f}ms")
                    else:
                        label_parts.append(f"Time: {timing*1000000:.1f}µs")
                
                if cardinality:
                    label_parts.append(f"Est: {cardinality} rows")
                
                if name == "FILTER" and "Filter" in extra:
                    label_parts.append(f"<small>Cond: {extra['Filter']}</small>")
                elif name == "HASH_JOIN" and "Join Condition" in extra:
                    label_parts.append(f"<small>On: {extra['Join Condition']}</small>")
                elif "Scan" in name or "SCAN" in name:
                    table = extra.get("Table", extra.get("table", ""))
                    if table:
                        label_parts.append(f"<small>Table: {table}</small>")
                        
                label = "<br/>".join(label_parts)
                label = label.replace('"', "'").replace('[', '(').replace(']', ')')
                
                cls = "otherClass"
                name_lower = name.lower()
                if "scan" in name_lower:
                    cls = "scanClass"
                elif "join" in name_lower:
                    cls = "joinClass"
                elif "filter" in name_lower:
                    cls = "filterClass"
                elif "sort" in name_lower or "order" in name_lower:
                    cls = "sortClass"
                elif "projection" in name_lower:
                    cls = "projClass"
                    
                nodes.append(f'{node_id}["{label}"]::: {cls}')
                
                if parent_id:
                    connections.append(f"{parent_id} --> {node_id}")
                    
                for child in node.get("children", []):
                    traverse(child, node_id)
                    
            if isinstance(plan, list):
                traverse(plan[0])
            elif isinstance(plan, dict):
                children = plan.get("children", [])
                if children:
                    traverse(children[0])
                else:
                    traverse(plan)
                    
            mermaid_code = "graph TD\n"
            mermaid_code += "  classDef scanClass fill:#0284c7,stroke:#bae6fd,stroke-width:1.5px,color:#fff;\n"
            mermaid_code += "  classDef joinClass fill:#ea580c,stroke:#ffedd5,stroke-width:1.5px,color:#fff;\n"
            mermaid_code += "  classDef filterClass fill:#dc2626,stroke:#fee2e2,stroke-width:1.5px,color:#fff;\n"
            mermaid_code += "  classDef sortClass fill:#7c3aed,stroke:#f3e8ff,stroke-width:1.5px,color:#fff;\n"
            mermaid_code += "  classDef projClass fill:#16a34a,stroke:#dcfce7,stroke-width:1.5px,color:#fff;\n"
            mermaid_code += "  classDef otherClass fill:#4b5563,stroke:#e5e7eb,stroke-width:1.5px,color:#fff;\n"
            
            for n in nodes:
                mermaid_code += f"  {n}\n"
            for c in connections:
                mermaid_code += f"  {c}\n"
                
            return mermaid_code
        except Exception as ex:
            print(f"Error parsing JSON explain: {ex}", flush=True)
            return ""

    def run_profiler_query():
        """Execute the EXPLAIN or EXPLAIN ANALYZE statement in DuckDB and parse/render the ASCII visual plan tree."""
        sql = sql_editor.value.strip()
        if not sql:
            ui.notify('Please write a query first!', type='warning')
            return
            
        mode_val = profile_mode_select.value
        is_analyze = "ANALYZE" in mode_val or "Execution Profile" in mode_val
        
        # Format the query, ignoring ending semicolons
        sql_clean = sql.strip().rstrip(';')
        sql_lower = sql_clean.lower()
        
        if sql_lower.startswith('explain'):
            explain_sql = sql
        else:
            if is_analyze:
                explain_sql = f"EXPLAIN ANALYZE {sql_clean};"
            else:
                explain_sql = f"EXPLAIN {sql_clean};"
                
        status_label.text = "Profiling query..."
        
        # 1. First attempt to capture dynamic JSON explain schema for structural flowchart rendering
        json_plan = None
        if not sql_lower.startswith('explain'):
            try:
                json_sql = f"EXPLAIN (ANALYZE, FORMAT JSON) {sql_clean};" if is_analyze else f"EXPLAIN (FORMAT JSON) {sql_clean};"
                json_res = explorer.conn.execute(json_sql).fetchall()
                if json_res and len(json_res) > 0:
                    json_plan = json_res[0][1]
            except Exception as json_err:
                print(f"DEBUG: JSON explain execution failed (falling back to standard text plan): {json_err}", flush=True)

        try:
            res = explorer.conn.execute(explain_sql).fetchall()
        except Exception as e:
            status_label.text = "Profiler execution failed"
            profiler_container.clear()
            with profiler_container:
                with ui.card().classes('w-full p-4 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded'):
                    with ui.row().classes('items-center gap-2 text-red-600 dark:text-red-400'):
                        ui.icon('error', size='md')
                        ui.label(f"Profiler Error: {e}").classes('font-semibold text-sm')
            ui.notify(f"Profiler error: {e}", type='negative')
            return
            
        if not res or len(res) == 0:
            ui.notify("No execution plan returned by database engine.", type='warning')
            return
            
        explain_key = res[0][0]
        explain_value = res[0][1]
        
        # Parse insights from the visual ASCII tree
        import re
        time_match = re.search(r"Total Time:\s*([0-9\.]+[a-zA-Z]+)", explain_value, re.IGNORECASE)
        total_time = time_match.group(1) if time_match else None
        
        seq_scans = explain_value.count("SEQ_SCAN") + explain_value.count("TABLE_SCAN") + explain_value.count("seq_scan")
        hash_joins = explain_value.count("HASH_JOIN") + explain_value.count("hash_join")
        filters = explain_value.count("FILTER") + explain_value.count("filter")
        sorts = explain_value.count("ORDER_BY") + explain_value.count("SORT") + explain_value.count("sort")
        
        suggestions = []
        if seq_scans > 0:
            suggestions.append(("SEQ_SCAN / TABLE_SCAN", "Sequential / Table Scan detected. For larger datasets, consider creating a database index or using partition-pruning to speed up lookups."))
        if hash_joins > 0:
            suggestions.append(("HASH_JOIN", "Hash Join was used. Hashing datasets requires extra memory. Ensure join columns have consistent types for optimal hashing."))
        if sorts > 0:
            suggestions.append(("SORT / ORDER_BY", "Sorting / Order By operation detected. Sorting large datasets can be memory-intensive. Consider limiting rows (LIMIT) or filtering before sorting."))
            
        status_label.text = "Profiling completed successfully"
        profiler_container.clear()
        
        with profiler_container:
            # Metrics Cards Row
            with ui.row().classes('w-full gap-4 flex-nowrap justify-between'):
                # Card 1: Mode
                with ui.card().classes('flex-grow p-4 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                    ui.icon('settings', color='primary').classes('text-2xl')
                    ui.label('Execution Mode').classes('text-xs text-slate-400 font-semibold uppercase')
                    mode_str = "Execution Profile" if is_analyze else "Logical Plan"
                    ui.label(mode_str).classes('text-sm font-bold text-slate-800 dark:text-white text-center')
                
                # Card 2: Duration
                with ui.card().classes('flex-grow p-4 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                    ui.icon('timer', color='secondary').classes('text-2xl')
                    ui.label('Total Time').classes('text-xs text-slate-400 font-semibold uppercase')
                    time_str = total_time if total_time else "N/A"
                    ui.label(time_str).classes('text-sm font-bold text-slate-800 dark:text-white')
                    
                # Card 3: Key Operators Count
                with ui.card().classes('flex-grow p-4 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                    ui.icon('analytics', color='positive').classes('text-2xl')
                    ui.label('Operators Detected').classes('text-xs text-slate-400 font-semibold uppercase')
                    op_list = []
                    if seq_scans: op_list.append(f"Scans: {seq_scans}")
                    if hash_joins: op_list.append(f"Joins: {hash_joins}")
                    if sorts: op_list.append(f"Sorts: {sorts}")
                    op_str = ", ".join(op_list) if op_list else "None"
                    ui.label(op_str).classes('text-sm font-bold text-slate-800 dark:text-white text-center')
            
            # Suggestions Section
            if suggestions:
                with ui.card().classes('w-full p-4 border border-indigo-100 dark:border-indigo-900 bg-indigo-50/50 dark:bg-indigo-950/20 shadow-none gap-2'):
                    with ui.row().classes('items-center gap-2 text-indigo-600 dark:text-indigo-400'):
                        ui.icon('lightbulb', size='sm')
                        ui.label('Optimization Insights & Tips').classes('font-bold text-sm')
                    ui.separator().classes('opacity-50')
                    for op_type, text in suggestions:
                        with ui.row().classes('items-start gap-2 py-1 no-wrap'):
                            ui.icon('chevron_right', size='xs', color='indigo').classes('mt-1')
                            ui.markdown(f"**{op_type}**: {text}").classes('text-xs text-slate-700 dark:text-slate-300')
                            
            # Visual Tree Section Header
            with ui.row().classes('w-full justify-between items-center px-1 pt-2'):
                ui.label('Visual Execution Plan:').classes('text-sm font-bold text-slate-700 dark:text-slate-300')
                ui.button('Copy Plan', icon='content_copy', color='primary',
                          on_click=lambda: copy_plan_to_clipboard(explain_value)).props('flat dense').classes('text-xs')
                          
            # Interactive Graph Tab selection if JSON format resolved successfully
            mermaid_code = json_plan_to_mermaid(json_plan) if json_plan else None
            if mermaid_code:
                with ui.tabs().classes('w-full') as plan_tabs:
                    diag_tab = ui.tab('Interactive Flow', icon='schema')
                    text_tab = ui.tab('Raw Text Plan', icon='notes')
                with ui.tab_panels(plan_tabs, value=diag_tab).classes('w-full bg-transparent flex-none'):
                    with ui.tab_panel(diag_tab).classes('p-0 pt-2 flex-col items-center'):
                        ui.mermaid(mermaid_code).classes('w-full overflow-auto bg-slate-900 border border-slate-800 rounded-lg p-4')
                    with ui.tab_panel(text_tab).classes('p-0 pt-2'):
                        ui.label(explain_value).classes('w-full font-mono text-xs bg-slate-900 text-emerald-400 p-4 rounded-lg border border-slate-800 shadow-inner whitespace-pre overflow-x-auto').style('white-space: pre; line-height: 1.4;')
            else:
                # Monospace visual tree plan using a bulletproof preformatted ui.label to avoid Prism.js theme conflicts
                # Rendered at full height with flex-none to prevent Quasar flex squishing, allowing clean single-scrollbar scrolling in the parent panel
                ui.label(explain_value).classes('w-full font-mono text-xs bg-slate-900 text-emerald-400 p-4 rounded-lg border border-slate-800 shadow-inner whitespace-pre overflow-x-auto flex-none').style('white-space: pre; line-height: 1.4;')
            
        ui.notify("Successfully profiled execution plan!", type='success')

    def confirm_reseed():
        """Prompt user with warning modal before wiping the DuckDB file."""
        with ui.dialog() as dialog, ui.card():
            ui.label('Reset and Reseed Database?').classes('text-lg font-bold text-slate-800 dark:text-white')
            ui.label('This will wipe all custom tables in your connection and re-seed with synthetic sales data.').classes('text-sm text-slate-500 my-2')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Yes, Reseed DB', color='warning', on_click=lambda: perform_reseed(dialog))

    def perform_reseed(dialog):
        dialog.close()
        ui.notify('Seeding mock dataset... Please wait.', type='ongoing')
        
        explorer.close()
        success = seed_database(DB_NAME, force=True)
        explorer.__init__(DB_NAME)
        
        if success:
            ui.notify('Successfully re-seeded database with 6,500 sales records!', type='success')
            refresh_schema_tree()
            populate_builder_tables()
            sql_editor.value = query_history[0]
            run_editor_query()
        else:
            ui.notify('Failed to re-seed database. Check logs.', type='negative')

    # --- DATA INGESTION WIZARD DIALOG ---
    
    async def handle_ingest_upload(e):
        target_db = target_db_select.value
        target_schema = target_schema_select.value
        
        tbl_name = table_name_input.value.strip()
        if not tbl_name:
            tbl_name = os.path.splitext(e.file.name)[0]
            # Slugify table name to make it a valid SQL identifier
            tbl_name = "".join([c if c.isalnum() else "_" for c in tbl_name]).strip("_").lower()
            
        if not tbl_name:
            ui.notify('Please specify a valid table name!', type='warning')
            return
            
        fq_name = f"{target_db}.{target_schema}.{tbl_name}"
        policy = collision_select.value
        delim = delimiter_select.value
        mode = import_mode_select.value
        is_external = (mode == 'external')
        
        if is_external and policy == 'append':
            ui.notify("Append is not supported for external tables (views)!", type='warning')
            return
            
        # Temporary or persistent file path in scratch folder
        temp_dir = 'scratch'
        os.makedirs(temp_dir, exist_ok=True)
        ext = os.path.splitext(e.file.name)[1].lower()
        
        if is_external:
            target_file_path = os.path.join(temp_dir, f"{tbl_name}{ext}")
        else:
            target_file_path = os.path.join(temp_dir, f"temp_upload_{int(time.time())}{ext}")
        
        try:
            content_bytes = await e.file.read()
            
            # Write diagnostic info to scratch/ingest_debug.log
            with open('scratch/ingest_debug.log', 'w') as log_f:
                log_f.write(f"file_name: {e.file.name}\n")
                log_f.write(f"type: {type(content_bytes)}\n")
                log_f.write(f"length: {len(content_bytes)}\n")
                try:
                    log_f.write(f"decoded_start:\n{content_bytes.decode('utf-8')[:500]}\n")
                except Exception as log_ex:
                    log_f.write(f"decode_error: {log_ex}\n")
                    log_f.write(f"raw_start:\n{str(content_bytes[:500])}\n")
            
            # Save uploaded bytes to persistent/temp file
            with open(target_file_path, 'wb') as f:
                f.write(content_bytes)
                
            # Check if table or view exists in target database and schema
            exists = False
            try:
                check_res = explorer.conn.execute(f"SELECT 1 FROM duckdb_tables WHERE database_name = '{target_db}' AND schema_name = '{target_schema}' AND table_name = '{tbl_name}'").fetchall()
                if not check_res:
                    check_res = explorer.conn.execute(f"SELECT 1 FROM duckdb_views WHERE database_name = '{target_db}' AND schema_name = '{target_schema}' AND view_name = '{tbl_name}'").fetchall()
                exists = len(check_res) > 0
            except Exception:
                pass
                
            if exists:
                if policy == 'fail':
                    ui.notify(f"Table or View '{tbl_name}' already exists in {target_db}.{target_schema}! Choose 'Replace' or use a different name.", type='warning')
                    return
                elif policy == 'replace':
                    explorer.conn.execute(f"DROP VIEW IF EXISTS {fq_name};")
                    explorer.conn.execute(f"DROP TABLE IF EXISTS {fq_name};")
            
            # Ingest query building
            delim_opt = ""
            if ext not in ['.parquet', '.json', '.ndjson']:
                extra_opts = []
                if delim != 'auto':
                    extra_opts.append(f"delim='{delim}'")
                if ignore_errors_checkbox.value:
                    extra_opts.append("ignore_errors=True")
                if null_padding_checkbox.value:
                    extra_opts.append("null_padding=True")
                if not strict_mode_checkbox.value:
                    extra_opts.append("strict_mode=False")
                if all_varchar_checkbox.value:
                    extra_opts.append("all_varchar=True")
                if extra_opts:
                    delim_opt = ", " + ", ".join(extra_opts)
 
            # Resolve JSON source representation (supporting wrapped API JSON arrays or custom templates)
            if ext in ['.json', '.ndjson']:
                selected_tmpl = template_select.value
                templated_query = None
                if selected_tmpl and os.path.exists(selected_tmpl):
                    try:
                        import yaml
                        with open(selected_tmpl, 'r') as f_tmpl:
                            tmpl = yaml.safe_load(f_tmpl)
                        if isinstance(tmpl, dict) and 'query_template' in tmpl:
                            templated_query = tmpl['query_template'].replace('{file_path}', target_file_path)
                            print(f"INFO: Applying custom JSON unnesting template from {selected_tmpl}")
                    except Exception as tmpl_ex:
                        print(f"WARNING: Failed to parse selected template: {tmpl_ex}")
                
                if templated_query:
                    json_source = templated_query
                else:
                    try:
                        cols = explorer.conn.execute(f"DESCRIBE SELECT * FROM read_json_auto('{target_file_path}');").fetchall()
                        col_names_types = {row[0]: row[1] for row in cols}
                        target_col = None
                        for candidate in ['data', 'results', 'rows', 'items']:
                            if candidate in col_names_types:
                                t = col_names_types[candidate]
                                if '[]' in t and ('STRUCT' in t or 'MAP' in t):
                                    target_col = candidate
                                    break
                        if not target_col:
                            for c_name, t in col_names_types.items():
                                if '[]' in t and ('STRUCT' in t or 'MAP' in t):
                                    target_col = c_name
                                    break
                        if target_col:
                            json_source = f"SELECT unnest.* FROM (SELECT UNNEST({target_col}) AS unnest FROM read_json_auto('{target_file_path}'))"
                            print(f"INFO: Automatically unnesting nested JSON array from column '{target_col}'.")
                        else:
                            json_source = f"SELECT * FROM read_json_auto('{target_file_path}')"
                    except Exception as ex:
                        print(f"WARNING: Failed to automatically inspect JSON layout: {ex}")
                        json_source = f"SELECT * FROM read_json_auto('{target_file_path}')"

            if is_external:
                # Create an external table view pointing to the persistent file
                if ext == '.parquet':
                    ingest_sql = f"CREATE VIEW {fq_name} AS SELECT * FROM read_parquet('{target_file_path}');"
                elif ext in ['.json', '.ndjson']:
                    ingest_sql = f"CREATE VIEW {fq_name} AS {json_source};"
                else:
                    ingest_sql = f"CREATE VIEW {fq_name} AS SELECT * FROM read_csv_auto('{target_file_path}'{delim_opt});"
            else:
                # Physical table creation/insertion
                if ext == '.parquet':
                    if exists and policy == 'append':
                        ingest_sql = f"INSERT INTO {fq_name} SELECT * FROM read_parquet('{target_file_path}');"
                    else:
                        ingest_sql = f"CREATE TABLE {fq_name} AS SELECT * FROM read_parquet('{target_file_path}');"
                elif ext in ['.json', '.ndjson']:
                    if exists and policy == 'append':
                        ingest_sql = f"INSERT INTO {fq_name} {json_source};"
                    else:
                        ingest_sql = f"CREATE TABLE {fq_name} AS {json_source};"
                else:
                    if exists and policy == 'append':
                        ingest_sql = f"INSERT INTO {fq_name} SELECT * FROM read_csv_auto('{target_file_path}'{delim_opt});"
                    else:
                        ingest_sql = f"CREATE TABLE {fq_name} AS SELECT * FROM read_csv_auto('{target_file_path}'{delim_opt});"
            
            # Run ingestion in DuckDB
            explorer.conn.execute(ingest_sql)
            explorer.conn.commit()
            
            if is_external:
                ui.notify(f"Successfully created external view '{tbl_name}' in {target_db}.{target_schema} pointing to file!", type='success')
            else:
                ui.notify(f"Ingested file into '{tbl_name}' table in {target_db}.{target_schema}!", type='success')
            import_dialog.close()
            
            # Reset form inputs
            table_name_input.value = ''
            import_mode_select.value = 'table'
            collision_select.value = 'fail'
            delimiter_select.value = 'auto'
            ignore_errors_checkbox.value = False
            null_padding_checkbox.value = False
            strict_mode_checkbox.value = True
            all_varchar_checkbox.value = False
            uploader.reset()
            
            # Refresh tree and execute preview query
            refresh_schema_tree()
            populate_builder_tables()
            cols = explorer.list_columns_with_types(tbl_name, database=target_db, schema=target_schema)
            if cols:
                sql_editor.value = format_column_projection_query(cols, fq_name)
            else:
                sql_editor.value = f"SELECT * FROM {fq_name} LIMIT 100;"
            run_editor_query()
            
        except Exception as ex:
            ui.notify(f"Ingestion failed: {str(ex)}", type='negative', duration=7)
        finally:
            if 'target_file_path' in locals() and not is_external:
                if os.path.exists(target_file_path):
                    try:
                        os.remove(target_file_path)
                    except Exception:
                        pass

    # Build Table Inspector Modal Dialog
    with ui.dialog() as inspector_dialog, ui.card().classes('w-[900px] max-w-[95vw] p-6 gap-4 border border-slate-100 dark:border-slate-800 rounded-xl dark-bg-flat'):
        inspector_content = ui.column().classes('w-full gap-4')

    # Build Modal Ingestion Dialog
    with ui.dialog() as import_dialog, ui.card().classes('w-96 p-6 gap-4'):
        ui.label('📥 Data Ingestion Wizard').classes('text-lg font-bold text-slate-800 dark:text-white')
        ui.label('Upload a local CSV, Parquet, or JSON file to parse and ingest into the sandbox.').classes('text-xs text-slate-500 -mt-2')
        
        # Controls
        table_name_input = ui.input('Table Name (Optional)', placeholder='Suggested automatically if blank').props('outlined dense').classes('w-full')
        
        # Target Database & Schema selection dropdowns
        target_db_select = ui.select(
            options={'your_duckdb_file': 'your_duckdb_file'},
            value='your_duckdb_file',
            label='Target Database',
            on_change=lambda e: update_import_schemas(e.value)
        ).props('outlined dense').classes('w-full')
        
        target_schema_select = ui.select(
            options={'main': 'main'},
            value='main',
            label='Target Schema'
        ).props('outlined dense').classes('w-full')
        
        templates_options = {'': 'None (Auto-detect)'}
        
        template_select = ui.select(
            options=templates_options,
            value='',
            label='JSON Unnesting Template'
        ).props('outlined dense').classes('w-full')
        
        async def select_custom_template():
            start_dir = '/templates' if os.path.exists('/templates') else 'templates'
            if not os.path.exists(start_dir):
                start_dir = '.'
            picker = local_file_picker(start_dir)
            res = await picker
            if res:
                custom_path = res[0]
                try:
                    import yaml
                    with open(custom_path, 'r') as f_tmpl:
                        tmpl = yaml.safe_load(f_tmpl)
                    if isinstance(tmpl, dict) and 'name' in tmpl and 'query_template' in tmpl:
                        templates_options[custom_path] = f"📁 {tmpl['name']} ({os.path.basename(custom_path)})"
                        template_select.options = templates_options
                        template_select.value = custom_path
                        template_select.update()
                        ui.notify(f"Loaded template: {tmpl['name']}", type='success')
                    else:
                        ui.notify("Invalid template! Must contain 'name' and 'query_template'.", type='warning')
                except Exception as ex_tmpl:
                    ui.notify(f"Failed to load template: {ex_tmpl}", type='negative')

        ui.button('Browse local template...', icon='folder_open', on_click=select_custom_template).props('flat dense').classes('text-xs self-end text-primary -mt-2 mb-2')

        def update_import_schemas(db_name):
            try:
                schema_rows = explorer.conn.execute(f"SELECT schema_name FROM duckdb_schemas WHERE database_name = '{db_name}' AND schema_name NOT IN ('information_schema', 'pg_catalog') ORDER BY schema_name").fetchall()
                schemas = [row[0] for row in schema_rows]
                if not schemas:
                    schemas = ['main']
                target_schema_select.options = {s: s for s in schemas}
                target_schema_select.value = 'main' if 'main' in schemas else schemas[0]
                target_schema_select.update()
            except Exception as e:
                print(f"Error loading schemas for {db_name}: {e}")
                target_schema_select.options = {'main': 'main'}
                target_schema_select.value = 'main'
                target_schema_select.update()

        def open_import_dialog():
            try:
                db_rows = explorer.conn.execute("SELECT database_name FROM duckdb_databases").fetchall()
                dbs = [row[0] for row in db_rows if row[0] not in ('system', 'temp') and not row[0].startswith('__')]
                target_db_select.options = {db: db for db in dbs}
                if 'your_duckdb_file' in dbs:
                    target_db_select.value = 'your_duckdb_file'
                elif 'main' in dbs:
                    target_db_select.value = 'main'
                else:
                    target_db_select.value = dbs[0] if dbs else 'your_duckdb_file'
                target_db_select.update()
                
                update_import_schemas(target_db_select.value)
                
                # Scan templates directory and populate options
                tmpls = list_json_unnesting_templates()
                new_opts = {'': 'None (Auto-detect)'}
                new_opts.update(tmpls)
                template_select.options = new_opts
                template_select.value = ''
                template_select.update()
                
            except Exception as e:
                print(f"Error loading databases for import dialog: {e}")
            import_dialog.open()

        # Define a placeholder to handle forward references
        collision_select = None

        def handle_import_mode_change(e):
            nonlocal collision_select
            if not collision_select:
                return
            if e.value == 'external':
                collision_select.options = {
                    'fail': 'Fail if exists',
                    'replace': 'Replace (Overwrite)'
                }
                if collision_select.value == 'append':
                    collision_select.value = 'fail'
            else:
                collision_select.options = {
                    'fail': 'Fail if table exists',
                    'replace': 'Replace table (Overwrite)',
                    'append': 'Append rows to existing'
                }
            collision_select.update()

        import_mode_select = ui.select(
            options={
                'table': 'Import as Physical Table',
                'external': 'Import as External Table (View)'
            },
            value='table',
            label='Import Mode',
            on_change=handle_import_mode_change
        ).props('outlined dense').classes('w-full')

        collision_select = ui.select(
            options={
                'fail': 'Fail if table exists',
                'replace': 'Replace table (Overwrite)',
                'append': 'Append rows to existing'
            },
            value='fail',
            label='Collision Policy'
        ).props('outlined dense').classes('w-full')
        
        delimiter_select = ui.select(
            options={
                'auto': 'Auto-detect Delimiter',
                ',': 'Comma ( , )',
                ';': 'Semicolon ( ; )',
                '\t': 'Tab ( \\t )',
                '|': 'Pipe ( | )'
            },
            value='auto',
            label='CSV Delimiter'
        ).props('outlined dense').classes('w-full')
        
        # Advanced CSV Options collapsible section
        with ui.expansion('🔧 Advanced CSV Options', icon='tune').classes('w-full border border-slate-200 dark:border-slate-700 rounded-md text-xs text-slate-700 dark:text-slate-300'):
            with ui.column().classes('p-2 gap-1 w-full'):
                ignore_errors_checkbox = ui.checkbox('Ignore malformed rows (ignore_errors)', value=False).classes('text-xs text-slate-700 dark:text-slate-300')
                null_padding_checkbox = ui.checkbox('Null padding for missing columns (null_padding)', value=False).classes('text-xs text-slate-700 dark:text-slate-300')
                strict_mode_checkbox = ui.checkbox('Strict schema validation (strict_mode)', value=True).classes('text-xs text-slate-700 dark:text-slate-300')
                all_varchar_checkbox = ui.checkbox('Load all columns as text (all_varchar)', value=False).classes('text-xs text-slate-700 dark:text-slate-300')
        
        uploader = ui.upload(
            label='Select CSV, Parquet or JSON',
            auto_upload=False,
            max_files=1,
            on_upload=handle_ingest_upload
        ).props('outlined dense accept=".csv,.tsv,.parquet,.json,.ndjson"').classes('w-full')
        
        with ui.row().classes('w-full justify-end gap-2 pt-2'):
            ui.button('Cancel', on_click=import_dialog.close).props('flat')
            ui.button('Ingest File', icon='bolt', color='primary', on_click=lambda: uploader.run_method('upload')).props('elevated')

    # Build Save Query Dialog
    with ui.dialog() as save_query_dialog, ui.card().classes('w-96 p-6 gap-4'):
        ui.label('💾 Save Query to Library').classes('text-lg font-bold text-slate-800 dark:text-white')
        ui.label('Provide a clear name and short description to catalog this SQL snippet in your persistent database library.').classes('text-xs text-slate-500 -mt-2')
        save_query_name_input = ui.input('Query Name', placeholder='e.g., Q4 Transaction Summary').props('outlined dense').classes('w-full')
        save_query_desc_input = ui.input('Description (Optional)', placeholder='e.g., Filters values exceeding $100').props('outlined dense').classes('w-full')
        
        save_query_category_select = ui.select(
            options=['Analytical', 'Utility', 'DDL/DML'],
            value='Analytical',
            label='Snippet Category'
        ).props('outlined dense').classes('w-full')
        
        with ui.row().classes('w-full justify-end gap-2 pt-2'):
            ui.button('Cancel', on_click=save_query_dialog.close).props('flat')
            ui.button('Save Query', icon='save', color='positive', on_click=handle_save_query).props('elevated')

    # Build Attach Database Dialog
    with ui.dialog() as attach_db_dialog, ui.card().classes('w-96 p-6 gap-4'):
        ui.label('🔌 Attach External Database').classes('text-lg font-bold text-slate-800 dark:text-white')
        ui.label('Attach SQLite, PostgreSQL, MySQL, DuckDB or DuckLake database directly to your session.').classes('text-xs text-slate-500 -mt-2')
        
        # Controls
        attach_alias_input = ui.input('Database Alias', placeholder='e.g., my_sqlite_db').props('outlined dense').classes('w-full')
        
        db_type_select = ui.select(
            options={
                'duckdb': 'DuckDB Database (.duckdb, .db)',
                'sqlite': 'SQLite Database (via extension)',
                'ducklake': 'DuckLake Table Format',
                'postgres': 'PostgreSQL Server Connection',
                'mysql': 'MySQL Server Connection'
            },
            value='duckdb',
            label='Database Type',
            on_change=lambda e: toggle_ducklake_options(e)
        ).props('outlined dense').classes('w-full')
        
        attach_path_input = ui.input(
            label='Path or Connection String',
            placeholder='e.g., path/to/sqlite.db or host=localhost ...'
        ).props('outlined dense').classes('w-full')
        
        # DuckLake options container
        with ui.column().classes('w-full gap-2 border border-slate-200 dark:border-slate-800 rounded p-3 dark-bg-flat') as ducklake_opts_container:
            ui.label('DuckLake Options').classes('text-xs font-bold text-slate-600 dark:text-slate-400')
            ducklake_data_path_input = ui.input('Data Parquet Folder', value='data_parquet/', placeholder='e.g., path/to/data_parquet/').props('outlined dense').classes('w-full')
            
        def toggle_ducklake_options(e):
            ducklake_opts_container.visible = (e.value == 'ducklake')
            
        ducklake_opts_container.visible = False # Initial state is duckdb, so hidden
        
        async def handle_attach_db():
            alias = attach_alias_input.value.strip()
            db_type = db_type_select.value
            conn_path = attach_path_input.value.strip()
            data_path = ducklake_data_path_input.value.strip() if db_type == 'ducklake' else None
            
            if not alias or not conn_path:
                ui.notify('Please fill out all required fields!', type='warning')
                return
                
            # Slugify alias to prevent SQL injections or bad names
            alias = "".join([c if c.isalnum() else "_" for c in alias]).strip("_").lower()
            if not alias:
                ui.notify('Please provide a valid alphanumeric database alias!', type='warning')
                return
                
            status_label.text = "Attaching database..."
            
            try:
                # Pre-install/load required extension
                if db_type == 'sqlite':
                    explorer.conn.execute("INSTALL sqlite; LOAD sqlite;")
                elif db_type == 'postgres':
                    explorer.conn.execute("INSTALL postgres; LOAD postgres;")
                elif db_type == 'mysql':
                    explorer.conn.execute("INSTALL mysql; LOAD mysql;")
                elif db_type == 'ducklake':
                    explorer.conn.execute("INSTALL ducklake; LOAD ducklake;")
                    
                # Construct ATTACH command
                if db_type == 'ducklake':
                    # Create directories if they do not exist
                    meta_dir = os.path.dirname(conn_path)
                    if meta_dir:
                        os.makedirs(meta_dir, exist_ok=True)
                    if data_path:
                        os.makedirs(data_path, exist_ok=True)
                    sql = f"ATTACH 'ducklake:{conn_path}' AS {alias} (DATA_PATH '{data_path}');"
                elif db_type == 'sqlite':
                    # Ensure sqlite file exists or directory is ready
                    sqlite_dir = os.path.dirname(conn_path)
                    if sqlite_dir:
                        os.makedirs(sqlite_dir, exist_ok=True)
                    sql = f"ATTACH '{conn_path}' AS {alias} (TYPE sqlite);"
                elif db_type == 'postgres':
                    sql = f"ATTACH '{conn_path}' AS {alias} (TYPE postgres);"
                elif db_type == 'mysql':
                    sql = f"ATTACH '{conn_path}' AS {alias} (TYPE mysql);"
                else: # duckdb
                    db_dir = os.path.dirname(conn_path)
                    if db_dir:
                        os.makedirs(db_dir, exist_ok=True)
                    sql = f"ATTACH '{conn_path}' AS {alias};"
                    
                # Execute query in DuckDB to check if connection works
                explorer.conn.execute(sql)
                
                # Save to YAML config
                save_attached_database(alias, db_type, conn_path, data_path)
                
                ui.notify(f"Successfully attached database '{alias}'!", type='success')
                attach_db_dialog.close()
                
                # Reset inputs
                attach_alias_input.value = ''
                attach_path_input.value = ''
                db_type_select.value = 'duckdb'
                ducklake_data_path_input.value = 'data_parquet/'
                
                # Refresh UI trees
                refresh_schema_tree()
            except Exception as ex:
                ui.notify(f"Failed to attach database: {str(ex)}", type='negative', duration=7)
                status_label.text = "Error attaching database"
                
        with ui.row().classes('w-full justify-end gap-2 pt-2'):
            ui.button('Cancel', on_click=attach_db_dialog.close).props('flat')
            ui.button('Attach Database', icon='link', color='primary', on_click=handle_attach_db).props('elevated')

    # --- EDIT API ENDPOINT MODAL DIALOG ---
    edit_columns_checkboxes = {}

    def open_edit_api_dialog(ep_id, ep_path, ep_desc, ep_sql, ep_secured=False, ep_rate_limit=None):
        edit_api_id_holder.text = ep_id
        edit_api_path_input.value = ep_path
        edit_api_desc_input.value = ep_desc
        edit_api_sql_input.value = ep_sql
        edit_api_rate_limit_input.value = ep_rate_limit if ep_rate_limit else ''
        try:
            edit_api_security_toggle.value = ep_secured
        except Exception:
            pass
        edit_column_selection_container.style('display: none;')
        edit_api_dialog.open()

    def handle_analyze_edit_columns():
        sql = edit_api_sql_input.value.strip() if edit_api_sql_input.value else ""
        if not sql:
            ui.notify('Please enter a SQL select query first!', type='warning')
            return
        
        full_tbl, tbl_only = parse_table_from_sql(sql)
        if not tbl_only:
            ui.notify('Could not parse a valid table name from the query (looking for FROM <table_name>).', type='warning')
            return
            
        try:
            cols = explorer.list_columns_with_types(tbl_only)
            if not cols:
                cols = explorer.list_columns_with_types(full_tbl)
                
            if not cols:
                ui.notify(f"Could not fetch columns for table '{tbl_only}'.", type='warning')
                return
            
            proj_map = parse_selected_columns_with_aliases(sql)
            
            def generate_edit_dynamic_where():
                selected_cols = [c_name for c_name, cb in edit_columns_checkboxes.items() if cb.value]
                if not selected_cols:
                    ui.notify('No columns selected!', type='warning')
                    return
                    
                sql = edit_api_sql_input.value.strip()
                has_semicolon = sql.endswith(';')
                if has_semicolon:
                    sql = sql[:-1].strip()
                    
                # Split trailing clauses (ORDER BY, LIMIT, OFFSET)
                sql, trailing = split_sql_trailing_clauses(sql)
                
                # Create type mapping and alias-lookup
                col_type_map = {c_name.lower(): c_type for c_name, c_type in cols}
                inv_proj_map = {alias.lower(): orig for orig, alias in proj_map.items()} if proj_map else {}
                
                # Build parameter clauses
                clauses = []
                generate_ranges = edit_range_switch.value
                
                for col in selected_cols:
                    # Find original column name to look up its data type
                    orig_col = inv_proj_map.get(col.lower(), col)
                    c_type = col_type_map.get(orig_col.lower(), "")
                    c_type_upper = c_type.upper()
                    is_numeric_or_date = any(t in c_type_upper for t in ['INT', 'DOUBLE', 'FLOAT', 'DECIMAL', 'REAL', 'NUMERIC', 'DATE', 'TIME', 'TIMESTAMP'])
                    
                    if generate_ranges and is_numeric_or_date:
                        clauses.append(f"  AND (${col}_eq IS NULL  OR \"{col}\" = ${col}_eq)")
                        clauses.append(f"  AND (${col}_gt IS NULL  OR \"{col}\" > ${col}_gt)")
                        clauses.append(f"  AND (${col}_gte IS NULL OR \"{col}\" >= ${col}_gte)")
                        clauses.append(f"  AND (${col}_lt IS NULL  OR \"{col}\" < ${col}_lt)")
                        clauses.append(f"  AND (${col}_lte IS NULL OR \"{col}\" <= ${col}_lte)")
                    else:
                        clauses.append(f"  AND (${col} IS NULL OR \"{col}\" = ${col})")
                    
                import re
                has_where = re.search(r'(?i)\bWHERE\b', sql)
                if has_where:
                    sql += "\n" + "\n".join(clauses)
                else:
                    sql += "\nWHERE 1=1\n" + "\n".join(clauses)
                    
                # Re-append trailing clauses
                if trailing:
                    sql += "\n" + trailing.strip()
                    
                if has_semicolon:
                    sql += ";"
                    
                edit_api_sql_input.value = sql
                edit_column_selection_container.style('display: none;')
                ui.notify('Dynamic WHERE clause injected into your query!', type='success')
            
            edit_column_selection_container.clear()
            edit_column_selection_container.style('display: flex;')
            
            def draw_edit_columns_grid(show_all):
                edit_grid_container.clear()
                edit_columns_checkboxes.clear()
                
                display_cols = []
                if show_all:
                    for c_name, c_type in cols:
                        display_name = proj_map.get(c_name.lower(), c_name) if proj_map else c_name
                        display_cols.append((display_name, c_type))
                else:
                    if proj_map:
                        if '*' in proj_map:
                            display_cols = [(c_name, c_type) for c_name, c_type in cols]
                        else:
                            for c_name, c_type in cols:
                                if c_name.lower() in proj_map:
                                    alias_name = proj_map[c_name.lower()]
                                    display_cols.append((alias_name, c_type))
                    else:
                        display_cols = cols
                        
                with edit_grid_container:
                    for c_name, c_type in display_cols:
                        cb = ui.checkbox(f"{c_name} ({c_type})").classes('text-xs')
                        edit_columns_checkboxes[c_name] = cb
            
            with edit_column_selection_container:
                with ui.row().classes('w-full justify-between items-center no-wrap'):
                    ui.label(f"Parsed Table: {full_tbl}").classes('text-xs font-bold text-slate-700 dark:text-slate-300')
                    ui.switch('Show all columns', value=False, on_change=lambda e: draw_edit_columns_grid(e.value)).classes('text-xs')
                    
                edit_range_switch = ui.switch('Enable range filters (>=, <=, etc.) for numeric & date columns', value=False).classes('text-xs font-medium text-slate-600 my-0.5')
                ui.label("Select columns to add as optional dynamic API parameters:").classes('text-[10px] text-slate-400 -mt-1')
                edit_grid_container = ui.grid(columns=2).classes('w-full gap-1')
                draw_edit_columns_grid(False)
                
                ui.button('Inject Dynamic Parameters', icon='auto_fix_high', color='secondary',
                          on_click=generate_edit_dynamic_where).props('dense unelevated size=sm').classes('mt-2 self-end text-xs')

            ui.notify(f"Analyzed table '{tbl_only}' successfully!", type='info')
        except Exception as ex:
            ui.notify(f"Error analyzing columns: {ex}", type='negative')

    def handle_update_api_endpoint():
        endpoint_id = edit_api_id_holder.text
        path = edit_api_path_input.value.strip() if edit_api_path_input.value else ""
        description = edit_api_desc_input.value.strip() if edit_api_desc_input.value else ""
        sql = edit_api_sql_input.value.strip() if edit_api_sql_input.value else ""
        rate_limit = edit_api_rate_limit_input.value.strip() if edit_api_rate_limit_input.value else None
        try:
            security_enabled = edit_api_security_toggle.value
        except Exception:
            security_enabled = False
        
        if not path:
            ui.notify('Please specify a valid endpoint path!', type='warning')
            return
        path = path.strip('/')
        if not sql:
            ui.notify('Please specify the SQL query source!', type='warning')
            return
            
        import re
        if not re.match(r'^[a-zA-Z0-9_\-\/]+$', path):
            ui.notify('Path can only contain alphanumeric characters, hyphens, underscores, and slashes.', type='warning')
            return
            
        try:
            # Check duplicate path excluding current endpoint
            dup = explorer.conn.execute("SELECT 1 FROM _duckdb_studio_api_endpoints WHERE path = ? AND id != ?", [path, endpoint_id]).fetchone()
            if dup:
                ui.notify(f"Endpoint path '/api/{path}' already exists! Please use a unique path.", type='negative')
                return
                
            rl_value = rate_limit.strip() if rate_limit and rate_limit.strip() else None
            explorer.conn.execute("""
                UPDATE _duckdb_studio_api_endpoints 
                SET path = ?, description = ?, sql_code = ?, security_enabled = ?, rate_limit = ? 
                WHERE id = ?;
            """, [path, description, sql, security_enabled, rl_value, endpoint_id])
            
            ui.notify(f"API Endpoint '/api/{path}' updated successfully!", type='success')
            edit_api_dialog.close()
            refresh_api_endpoints_grid()
        except Exception as ex:
            ui.notify(f"Failed to update endpoint: {ex}", type='negative')

    with ui.dialog() as edit_api_dialog, ui.card().classes('w-[500px] p-6 gap-4 border border-slate-100 dark:border-slate-800 rounded-xl dark-bg-flat'):
        ui.label('✏️ Edit API Endpoint').classes('text-lg font-bold text-slate-800 dark:text-white')
        ui.label('Modify path, description, rate limit, or the SQL query. You can also re-analyze columns to add new parameters.').classes('text-xs text-slate-500 -mt-2')
        
        edit_api_id_holder = ui.label('').classes('hidden') # Hidden holder
        edit_api_path_input = ui.input('Endpoint Path', placeholder='e.g., recent-sales').props('outlined dense').classes('w-full')
        edit_api_desc_input = ui.input('Description', placeholder='e.g., Returns active inventory list').props('outlined dense').classes('w-full')
        
        with ui.row().classes('w-full items-center justify-between no-wrap gap-4'):
            edit_api_security_toggle = ui.switch('Require JWT Token Authorization').classes('text-xs font-semibold text-slate-700 dark:text-slate-300')
            edit_api_rate_limit_input = ui.input(placeholder='Rate Limit (e.g., 10/minute)').props('outlined dense size=sm').classes('w-48 text-xs font-mono').tooltip('Optional custom limit per IP. Leave empty to use default limit.')

        with ui.column().classes('w-full gap-1'):
            ui.label('SQL Query Source').classes('text-xs font-semibold text-slate-400')
            edit_api_sql_input = ui.textarea(placeholder='SELECT * FROM product_inventory;').props('dense outlined autogrow').classes('w-full font-mono text-xs').style('min-height: 120px;')
            with ui.row().classes('w-full justify-between items-center gap-2 no-wrap'):
                ui.label('Support parameters via $parameter_name.').classes('text-[10px] text-slate-400 max-w-[60%]')
                ui.button('Analyze Columns', icon='analytics', on_click=handle_analyze_edit_columns).props('dense outline size=sm color=secondary').classes('text-xs')
                
        edit_column_selection_container = ui.column().classes('w-full gap-2 border border-dashed border-slate-300 dark:border-slate-700 rounded-lg p-3 my-1').style('display: none;')
        
        with ui.row().classes('w-full justify-end gap-2 pt-2'):
            ui.button('Cancel', on_click=edit_api_dialog.close).props('flat')
            ui.button('Save Changes', icon='save', color='positive', on_click=handle_update_api_endpoint).props('elevated')

    # --- KEYBOARD SHORTCUTS ---
    def handle_keyboard(e):
        if e.action.keydown and e.key == 'Enter' and (e.modifiers.ctrl or e.modifiers.meta):
            run_editor_query()
            
    ui.keyboard(on_key=handle_keyboard)

    # --- INITIAL RUN ON CLIENT BROWSER CONNECT ---
    refresh_schema_tree()
    populate_builder_tables()
    update_query_history_list()
    refresh_saved_queries_list()
    run_editor_query()


@app.get("/dbt_orange.svg")
def dbt_orange_svg():
    from fastapi import Response
    svg_content = '''<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>dbt</title><path fill="#FF6B4A" d="M17.9004 9.3763a8.1488 8.1488 0 0 0-3.0421-3.1206l1.7708.8385a10.2874 10.2874 0 0 1 3.74 3.0007l3.234-5.9295a2.8546 2.8546 0 0 0-.0611-2.9604C22.7566.0371 21.2112-.3409 19.9754.3327l-5.8749 3.2101a4.3612 4.3612 0 0 1-4.1761 0L4.1769.408a2.8545 2.8545 0 0 0-2.9592.0632c-1.1673.7853-1.5452 2.33-.8723 3.5655L3.55 9.9106a4.3612 4.3612 0 0 1 0 4.1772l-3.1272 5.743a2.86 2.86 0 0 0 .085 2.9974c.794 1.1438 2.3225 1.5054 3.5448.8385l6.0581-3.3049a10.2877 10.2877 0 0 1-3.0051-3.7454l-.8374-1.7708a8.148 8.148 0 0 0 3.1206 3.0421l10.5832 5.779c1.22 13.666 2.7481.3055 3.5426-.8363a2.8699 2.8699 0 0 0 .0796-3.0018L17.9004 9.3763zm3.3801-7.7351c.6022 0 1.0904.4882 1.0904 1.0904s-.4882 1.0904-1.0904 1.0904-1.0904-.4882-1.0904-1.0904.4882-1.0904 1.0904-1.0904zM2.7442 3.822c-.6022 0-1.0904-.4882-1.0904-1.0904s.4882-1.0904 1.0904-1.0904 1.0904.4882 1.0904 1.0904S3.3464 3.822 2.7442 3.822zm0 18.5363c-.6022 0-1.0904-.4882-1.0904-1.0904 0-.6022.4882-1.0904 1.0904-1.0904s1.0904.4882 1.0904 1.0904c0 .6022-.4882 1.0904-1.0904 1.0904zm10.3585-11.4489c-1.2008-.0035-2.177.9672-2.1805 2.1679a2.1738 2.1738 0 0 0 .7052 1.6091c-1.4872-.2091-2.5234-1.5843-2.3142-3.0716.2091-1.4872 1.5843-2.5234 3.0716-2.3142a2.7194 2.7194 0 0 1 2.3142 2.3142 2.1623 2.1623 0 0 0-1.5963-.7054zm8.1778 11.4489c-.6022 0-1.0904-.4882-1.0904-1.0904 0-.6022.4882-1.0904 1.0904-1.0904s1.0904.4882 1.0904 1.0904c0 .6022-.4882 1.0904-1.0904 1.0904z"/></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/jupyter_orange.svg")
def jupyter_orange_svg():
    from fastapi import Response
    svg_content = '''<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Jupyter</title><path fill="#F37626" d="M7.157 22.201A1.784 1.799 0 0 1 5.374 24a1.784 1.799 0 0 1-1.784-1.799 1.784 1.799 0 0 1 1.784-1.799 1.784 1.799 0 0 1 1.783 1.799zM20.582 1.427a1.415 1.427 0 0 1-1.415 1.428 1.415 1.427 0 0 1-1.416-1.428A1.415 1.427 0 0 1 19.167 0a1.415 1.427 0 0 1 1.415 1.427zM4.992 3.336A1.047 1.056 0 0 1 3.946 4.39a1.047 1.056 0 0 1-1.047-1.055A1.047 1.056 0 0 1 3.946 2.28a1.047 1.056 0 0 1 1.046 1.056zm7.336 1.517c3.769 0 7.06 1.38 8.768 3.424a9.363 9.363 0 0 0-3.393-4.547 9.238 9.238 0 0 0-5.377-1.728A9.238 9.238 0 0 0 6.95 3.73a9.363 9.363 0 0 0-3.394 4.547c1.713-2.04 5.004-3.424 8.772-3.424zm.001 13.295c-3.768 0-7.06-1.381-8.768-3.425a9.363 9.363 0 0 0 3.394 4.547A9.238 9.238 0 0 0 12.33 21a9.238 9.238 0 0 0 5.377-1.729 9.363 9.363 0 0 0 3.393-4.547c-1.712 2.044-5.003 3.425-8.772 3.425Z"/></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/vscode_blue.svg")
def vscode_blue_svg():
    from fastapi import Response
    svg_content = '''<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Visual Studio Code</title><path fill="#007ACC" d="M23.15 2.587L18.21.21a1.494 1.494 0 0 0-1.705.29l-9.46 8.63-4.12-3.128a.999.999 0 0 0-1.276.057L.327 7.261A1 1 0 0 0 .326 8.74L3.899 12 .326 15.26a1 1 0 0 0 .001 1.479L1.65 17.94a.999.999 0 0 0 1.276.057l4.12-3.128 9.46 8.63a1.492 1.492 0 0 0 1.704.29l4.942-2.377A1.5 1.5 0 0 0 24 20.06V3.939a1.5 1.5 0 0 0-.85-1.352zm-5.146 14.861L10.826 12l7.178-5.448v10.896z"/></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/swagger_green.svg")
def swagger_green_svg():
    from fastapi import Response
    svg_content = '''<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Swagger</title><path fill="#85EA2D" d="M12 0C5.383 0 0 5.383 0 12s5.383 12 12 12c6.616 0 12-5.383 12-12S18.616 0 12 0zm0 1.144c5.995 0 10.856 4.86 10.856 10.856 0 5.995-4.86 10.856-10.856 10.856-5.996 0-10.856-4.86-10.856-10.856C1.144 6.004 6.004 1.144 12 1.144zM8.37 5.868a6.707 6.707 0 0 0-.423.005c-.983.056-1.573.517-1.735 1.472-.115.665-.096 1.348-.143 2.017-.013.35-.05.697-.115 1.038-.134.609-.397.798-1.016.83a2.65 2.65 0 0 0-.244.042v1.463c1.126.055 1.278.452 1.37 1.629.033.429-.013.858.015 1.287.018.406.073.808.156 1.2.259 1.075 1.307 1.435 2.575 1.218v-1.283c-.203 0-.383.005-.558 0-.43-.013-.591-.12-.632-.535-.056-.535-.042-1.08-.075-1.62-.064-1.001-.175-1.988-1.153-2.625.503-.37.868-.812.983-1.398.083-.41.134-.821.166-1.237.028-.415-.023-.84.014-1.25.06-.665.102-.937.9-.91.12 0 .235-.017.369-.027v-1.31c-.16 0-.31-.004-.454-.006zm7.593.009a4.247 4.247 0 0 0-.813.06v1.274c.245 0 .434 0 .623.005.328.004.577.13.61.494.032.332.031.669.064 1.006.065.669.101 1.347.217 2.007.102.544.475.95.941 1.283-.817.549-1.057 1.333-1.098 2.215-.023.604-.037 1.213-.069 1.822-.028.554-.222.734-.78.748-.157.004-.31.018-.484.028v1.305c.327 0 .627.019.927 0 .932-.055 1.495-.507 1.68-1.412.078-.498.124-1 .138-1.504.032-.461.028-.927.074-1.384.069-.715.397-1.01 1.112-1.057a.972.972 0 0 0 .199-.046v-1.463c-.12-.014-.204-.027-.291-.032-.536-.023-.804-.203-.937-.71a5.146 5.146 0 0 1-.152-.993c-.037-.618-.033-1.241-.074-1.86-.08-1.192-.794-1.753-1.887-1.786zm-6.89 5.28a.844.844 0 0 0-.083 1.684h.055a.83.83 0 0 0 .877-.78v-.046a.845.845 0 0 0-.83-.858zm2.911 0a.808.808 0 0 0-.834.78c0 .027 0 .05.004.078 0 .503.342.826.859.826.507 0 .826-.332.826-.853-.005-.503-.342-.836-.855-.831zm2.963 0a.861.861 0 0 0-.876.835c0 .47.378.849.849.849h.009c.425.074.853-.337.881-.83.023-.457-.392-.854-.863-.854z"/></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/explorer_colored.svg")
def explorer_colored_svg():
    from fastapi import Response
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect x="2" y="3" width="20" height="18" rx="2" fill="none" stroke="#38BDF8" stroke-width="2" /><path d="M2 8 h20" stroke="#38BDF8" stroke-width="2" /><line x1="2" y1="13" x2="22" y2="13" stroke="#38BDF8" stroke-width="1.2" opacity="0.6" /><line x1="2" y1="17" x2="22" y2="17" stroke="#38BDF8" stroke-width="1.2" opacity="0.6" /><line x1="8" y1="3" x2="8" y2="21" stroke="#38BDF8" stroke-width="1.2" opacity="0.6" /><line x1="15" y1="3" x2="15" y2="21" stroke="#38BDF8" stroke-width="1.2" opacity="0.6" /><path d="M4 17 l5 -6 l5 4 l6 -9" fill="none" stroke="#F59E0B" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" /><circle cx="4" cy="17" r="2.2" fill="#F59E0B" /><circle cx="9" cy="11" r="2.2" fill="#F59E0B" /><circle cx="14" cy="15" r="2.2" fill="#F59E0B" /><circle cx="20" cy="6" r="2.5" fill="#F59E0B" /></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/extensions_teal.svg")
def extensions_teal_svg():
    from fastapi import Response
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="#14B8A6" d="M20.5 11H19V7c0-1.1-.9-2-2-2h-4V3.5a2.5 2.5 0 0 0-5 0V5H4c-1.1 0-1.99.9-1.99 2v3.8h1.5c1.49 0 2.7 1.21 2.7 2.7s-1.21 2.7-2.7 2.7H2V20c0 1.1.9 2 2 2h3.8v-1.5c0-1.49 1.21-2.7 2.7-2.7s2.7 1.21 2.7 2.7V22H17c1.1 0 2-.9 2-2v-4h1.5c1.49 0 2.7-1.21 2.7-2.7s-1.21-2.7-2.7-2.7z"/></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/settings_colored.svg")
def settings_colored_svg():
    from fastapi import Response
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="#818CF8" d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/scheduler_colored.svg")
def scheduler_colored_svg():
    from fastapi import Response
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="#F43F5E" d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/api_endpoint_colored.svg")
def api_endpoint_colored_svg():
    from fastapi import Response
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="1.5 2.5 21 19" width="24" height="24"><path fill="#06B6D4" d="M12 8a4 4 0 0 0-4 4 4 4 0 0 0 4 4 4 4 0 0 0 4-4 4 4 0 0 0-4-4zm0 2a2 2 0 0 1 2 2 2 2 0 0 1-2 2 2 2 0 0 1-2-2 2 2 0 0 1 2-2z"/><path fill="#06B6D4" d="M19.4 13c0-.3.1-.6.1-.9s0-.6-.1-.9l2.1-1.6c.2-.2.2-.5.1-.7l-2-3.5c-.1-.2-.4-.3-.6-.2l-2.5 1c-.5-.4-1.1-.7-1.7-.9L14 3.7c0-.3-.3-.5-.5-.5h-4c-.3 0-.5.2-.5.5L8.7 6.2c-.6.2-1.2.6-1.7.9l-2.5-1c-.2-.1-.5 0-.6.2l-2 3.5c-.1.2-.1.5.1.7L4.1 11c0 .3-.1.6-.1.9s0 .6.1.9l-2.1 1.6c-.2.2-.2.5-.1.7l2 3.5c.1.2.4.3.6.2l2.5-1c.5.4 1.1.7 1.7.9l.3 2.5c0 .3.3.5.5.5h4c.3 0 .5-.2.5-.5l.3-2.5c.6-.2 1.2-.6 1.7-.9l2.5 1c.2.1.5 0 .6-.2l2-3.5c.1-.2.1-.5-.1-.7L19.4 13zm-7.4 3c-2.2 0-4-1.8-4-4s1.8-4 4-4 4 1.8 4 4-1.8 4-4 4z"/><path fill="#10B981" d="M2 12h5m-5-3v6M4 9h2"/><path fill="#6366F1" d="M22 12h-5m5-3v6m-2-6h-2"/><text x="12" y="16.2" font-family="system-ui, sans-serif" font-size="11.5" font-weight="900" fill="#FFFFFF" stroke="#000000" stroke-width="0.6" text-anchor="middle" letter-spacing="0.1">API</text></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/db_tools_colored.svg")
def db_tools_colored_svg():
    from fastapi import Response
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="1 1.5 18.5 20.5" width="24" height="24"><g transform="translate(3, 0)"><path d="M5 13 v3.5 c0 1.1 2.2 2 5 2 s5 -.9 5 -2 v-3.5 Z" fill="#93C5FD" stroke="#1D4ED8" stroke-width="1.5" stroke-linejoin="round" /><ellipse cx="10" cy="13" rx="5" ry="1.8" fill="#DBEAFE" stroke="#1D4ED8" stroke-width="1.5" /><path d="M5 8.5 v3.5 c0 1.1 2.2 2 5 2 s5 -.9 5 -2 v-3.5 Z" fill="#60A5FA" stroke="#1D4ED8" stroke-width="1.5" stroke-linejoin="round" /><ellipse cx="10" cy="8.5" rx="5" ry="1.8" fill="#DBEAFE" stroke="#1D4ED8" stroke-width="1.5" /><path d="M5 4 v3.5 c0 1.1 2.2 2 5 2 s5 -.9 5 -2 v-3.5 Z" fill="#3B82F6" stroke="#1D4ED8" stroke-width="1.5" stroke-linejoin="round" /><ellipse cx="10" cy="4" rx="5" ry="1.8" fill="#DBEAFE" stroke="#1D4ED8" stroke-width="1.5" /></g><g transform="translate(-1.5, 9.5) scale(0.6)"><path fill="#F59E0B" stroke="#B45309" stroke-width="1.2" d="M12 8a4 4 0 0 0-4 4 4 4 0 0 0 4 4 4 4 0 0 0 4-4 4 4 0 0 0-4-4zm0 2a2 2 0 0 1 2 2 2 2 0 0 1-2 2 2 2 0 0 1-2-2 2 2 0 0 1 2-2z"/><path fill="#F59E0B" stroke="#B45309" stroke-width="1.2" d="M19.4 13c0-.3.1-.6.1-.9s0-.6-.1-.9l2.1-1.6c.2-.2.2-.5.1-.7l-2-3.5c-.1-.2-.4-.3-.6-.2l-2.5 1c-.5-.4-1.1-.7-1.7-.9L14 3.7c0-.3-.3-.5-.5-.5h-4c-.3 0-.5.2-.5.5L8.7 6.2c-.6.2-1.2.6-1.7.9l-2.5-1c-.2-.1-.5 0-.6.2l-2 3.5c-.1.2-.1.5.1.7L4.1 11c0 .3-.1.6-.1.9s0 .6.1.9l-2.1 1.6c-.2.2-.2.5-.1.7l2 3.5c.1.2.4.3.6.2l2.5-1c.5.4 1.1.7 1.7.9l.3 2.5c0 .3.3.5.5.5h4c.3 0 .5-.2.5-.5l.3-2.5c.6-.2 1.2-.6 1.7-.9l2.5 1c.2.1.5 0 .6-.2l2-3.5c.1-.2.1-.5-.1-.7L19.4 13zm-7.4 3c-2.2 0-4-1.8-4-4s1.8-4 4-4 4 1.8 4 4-1.8 4-4 4z"/></g></svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/api_endpoint_icon.png")
def api_endpoint_icon():
    import os
    from fastapi.responses import FileResponse
    icon_path = os.path.join(os.path.dirname(__file__), 'api_icon_transparent.png')
    if os.path.exists(icon_path):
        return FileResponse(icon_path)
    from fastapi import Response
    return Response(status_code=404)


# --- DYNAMIC API ENDPOINTS ROUTER ---
@app.get("/api/list-endpoints")
def list_endpoints():
    db_path = DB_NAME
    conn = duckdb.connect(db_path)
    try:
        # Check if table exists first
        table_exists = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = '_duckdb_studio_api_endpoints';").fetchone()[0]
        if not table_exists:
            return {"message": "Table _duckdb_studio_api_endpoints does not exist yet. Please load the main web interface once to initialize it."}
        rows = conn.execute("SELECT path, description, sql_code, COALESCE(security_enabled, FALSE) FROM _duckdb_studio_api_endpoints;").fetchall()
        return [{"path": r[0], "description": r[1], "sql_code": r[2], "security_enabled": r[3]} for r in rows]
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()



@app.get("/api/{endpoint_path:path}/stream")
@limiter.limit(get_dynamic_rate_limit)
def handle_streaming_endpoint(endpoint_path: str, request: Request):
    from fastapi.responses import StreamingResponse
    import time, datetime, json
    start_time = time.time()
    status_code = 200
    error_message = None
    
    db_path = DB_NAME
    conn = None
    try:
        conn = duckdb.connect(db_path)
        load_attached_databases_for_connection(conn)
        
        # Load the endpoint query from database, including the security flag
        res = conn.execute(
            "SELECT sql_code, COALESCE(security_enabled, FALSE) FROM _duckdb_studio_api_endpoints WHERE path = ?;",
            [endpoint_path]
        ).fetchone()
        
        if not res:
            from fastapi import HTTPException
            status_code = 404
            error_message = f"API Endpoint '/api/{endpoint_path}' not found"
            raise HTTPException(status_code=404, detail=error_message)
            
        sql_code, security_enabled = res
        
        # Enforce JWT Authorization if enabled for this endpoint
        if security_enabled:
            auth_header = request.headers.get("Authorization")
            verify_jwt_token(auth_header)
            
        # Parse query parameters from request query params to bind them if the query has placeholders
        import re
        placeholders = re.findall(r'\$([a-zA-Z0-9_]+)', sql_code)
        placeholders = list(dict.fromkeys(placeholders))
        
        bind_params = {}
        for param in placeholders:
            val = request.query_params.get(param)
            if val is not None:
                if val.lower() == 'true':
                    bind_params[param] = True
                elif val.lower() == 'false':
                    bind_params[param] = False
                else:
                    try:
                        if '.' in val:
                            bind_params[param] = float(val)
                        else:
                            bind_params[param] = int(val)
                    except ValueError:
                        bind_params[param] = val
            else:
                bind_params[param] = None

        # Clean trailing semicolon
        sql_clean = sql_code.strip()
        if sql_clean.endswith(';'):
            sql_clean = sql_clean[:-1].strip()

        # Define row generator
        def row_generator():
            # Open direct cursor stream
            cursor = conn.execute(sql_clean, bind_params)
            columns = [desc[0] for desc in cursor.description]
            
            while True:
                row = cursor.fetchone()
                if not row:
                    break
                # Yield single row as serialized JSON line
                record = dict(zip(columns, row))
                yield json.dumps(record, default=str) + "\n"
                
            conn.close()

        # Return streaming response in Newline Delimited JSON format
        return StreamingResponse(row_generator(), media_type="application/x-ndjson")

    except Exception as e:
        from fastapi import HTTPException
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if isinstance(e, HTTPException):
            status_code = e.status_code
            error_message = e.detail
            raise e
        status_code = 500
        error_message = str(e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Log telemetry metrics
        try:
            conn_metric = duckdb.connect(db_path)
            latency_ms = (time.time() - start_time) * 1000.0
            conn_metric.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_api_metrics (
                    endpoint_path VARCHAR,
                    timestamp TIMESTAMP,
                    latency_ms DOUBLE,
                    status_code INTEGER,
                    error_message VARCHAR
                );
            """)
            conn_metric.execute("""
                INSERT INTO _duckdb_studio_api_metrics (endpoint_path, timestamp, latency_ms, status_code, error_message)
                VALUES (?, ?, ?, ?, ?);
            """, [endpoint_path + "/stream", datetime.datetime.now(), latency_ms, status_code, error_message])
            conn_metric.close()
        except Exception as log_err:
            print(f"ERROR logging API streaming telemetry metrics: {log_err}", flush=True)


@app.get("/api/{endpoint_path:path}")
@limiter.limit(get_dynamic_rate_limit)
def handle_dynamic_endpoint(endpoint_path: str, request: Request):
    import time, datetime
    start_time = time.time()
    status_code = 200
    error_message = None
    
    db_path = DB_NAME
    conn = None
    try:
        conn = duckdb.connect(db_path)
        # Load and attach configured databases so the query can access them if needed
        load_attached_databases_for_connection(conn)
        
        # Load the endpoint query from database
        res = conn.execute(
            "SELECT sql_code, COALESCE(security_enabled, FALSE) FROM _duckdb_studio_api_endpoints WHERE path = ?;",
            [endpoint_path]
        ).fetchone()
        
        if not res:
            from fastapi import HTTPException
            status_code = 404
            error_message = f"API Endpoint '/api/{endpoint_path}' not found"
            raise HTTPException(status_code=404, detail=error_message)
            
        sql_code, security_enabled = res
        
        # Enforce JWT Authorization if enabled for this endpoint
        if security_enabled:
            auth_header = request.headers.get("Authorization")
            verify_jwt_token(auth_header)
        
        # Get pagination parameters from query params (defaults: limit=100, offset=0, max safety limit=10000)
        try:
            limit = min(int(request.query_params.get('limit', 100)), 10000)
        except ValueError:
            limit = 100
            
        try:
            offset = max(int(request.query_params.get('offset', 0)), 0)
        except ValueError:
            offset = 0
 
        # Extract placeholders starting with $ (e.g. $min_stock)
        import re
        placeholders = re.findall(r'\$([a-zA-Z0-9_]+)', sql_code)
        placeholders = list(dict.fromkeys(placeholders))
        
        # Build parameter dictionary from request query params
        bind_params = {}
        for param in placeholders:
            # Bind paging parameters explicitly if the query expects them
            if param.lower() == 'limit':
                # Request limit + 1 internally to see if there is more data without doing an extra COUNT query
                bind_params[param] = limit + 1
                continue
            if param.lower() == 'offset':
                bind_params[param] = offset
                continue
                
            val = request.query_params.get(param)
            # Convert values to correct types if possible
            if val is not None:
                if val.lower() == 'true':
                    bind_params[param] = True
                elif val.lower() == 'false':
                    bind_params[param] = False
                else:
                    try:
                        if '.' in val:
                            bind_params[param] = float(val)
                        else:
                            bind_params[param] = int(val)
                    except ValueError:
                        bind_params[param] = val
            else:
                bind_params[param] = None
 
        # Ensure limit/offset parameters are set in the dictionary if they were detected as placeholders
        lower_placeholders = [p.lower() for p in placeholders]
        if 'limit' in lower_placeholders:
            bind_params['limit'] = limit + 1
        if 'offset' in lower_placeholders:
            bind_params['offset'] = offset
 
        # Parse potential hard-coded trailing LIMIT clauses using split_sql_trailing_clauses
        sql_clean = sql_code.strip()
        if sql_clean.endswith(';'):
            sql_clean = sql_clean[:-1].strip()
            
        sql_clean, trailing = split_sql_trailing_clauses(sql_clean)
        
        has_hard_limit = False
        if trailing:
            import re
            limit_match = re.search(r'(?i)\bLIMIT\s+(\d+)\b', trailing)
            if limit_match:
                has_hard_limit = True
                hard_limit = int(limit_match.group(1))
                if hard_limit > 10000:
                    # Cap the hard-coded limit in the query at 10000
                    trailing = re.sub(r'(?i)\bLIMIT\s+\d+\b', 'LIMIT 10000', trailing)
 
        has_limit_placeholder = 'limit' in lower_placeholders
        
        # If the base query does not have a limit (neither placeholder nor hard-coded limit), add one of 10000 (using request limit)
        if not has_limit_placeholder and not has_hard_limit:
            # Wrap query to enforce pagination safely: Limit + 1 for has_more check
            sql_to_run = f"SELECT * FROM ({sql_clean}) LIMIT {limit + 1} OFFSET {offset};"
        else:
            # If the query had a limit, run it with the capped hardcoded limit or bound limit
            if trailing:
                sql_to_run = sql_clean + "\n" + trailing.strip()
            else:
                sql_to_run = sql_code
            
        # Execute query
        df = conn.execute(sql_to_run, bind_params).df()
        
        # High-performance paging calculation: check if there's more data using the limit + 1 row
        if len(df) > limit:
            has_more = True
            df = df.iloc[:limit]  # Slice to actual requested limit
        else:
            has_more = False
            
        records = df.to_dict(orient="records")
        
        # Return a beautifully structured metered response
        return {
            "meta": {
                "limit": limit,
                "offset": offset,
                "count": len(records),
                "has_more": has_more
            },
            "results": records
        }
    except Exception as e:
        from fastapi import HTTPException
        if isinstance(e, HTTPException):
            status_code = e.status_code
            error_message = e.detail
            raise e
        status_code = 500
        error_message = str(e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn is not None:
            try:
                # Log telemetry metrics
                latency_ms = (time.time() - start_time) * 1000.0
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS _duckdb_studio_api_metrics (
                        endpoint_path VARCHAR,
                        timestamp TIMESTAMP,
                        latency_ms DOUBLE,
                        status_code INTEGER,
                        error_message VARCHAR
                    );
                """)
                conn.execute("""
                    INSERT INTO _duckdb_studio_api_metrics (endpoint_path, timestamp, latency_ms, status_code, error_message)
                    VALUES (?, ?, ?, ?, ?);
                """, [endpoint_path, datetime.datetime.now(), latency_ms, status_code, error_message])
            except Exception as log_err:
                print(f"ERROR logging API telemetry metrics: {log_err}", flush=True)
            finally:
                conn.close()


# --- INITIALIZE AND SEED DATABASE ON STARTUP ---
print("INFO: Initializing and seeding database on startup...", flush=True)
seed_database(DB_NAME)
init_saved_queries_table(DB_NAME)


# Start Background Scheduler Thread
import threading
scheduler_thread = threading.Thread(target=run_background_scheduler, daemon=True)
scheduler_thread.start()


# Start application server
ui.run(title='DuckDB Studio Explorer', port=8085, show=False, storage_secret='duckdb_studio_secret_key_1337', reload=False)
