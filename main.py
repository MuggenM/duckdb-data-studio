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


from slowapi.errors import RateLimitExceeded

from theme import apply_theme
from utils import (
    sanitize_table_name,
    split_sql_trailing_clauses,
    has_top_level_where,
    format_column_projection_query,
    detect_parameters,
    substitute_sql_parameters,
    verify_jwt_token
)
from db_explorer import (
    DB_CONFIG,
    DuckDBExplorer,
    load_attached_databases_for_connection,
    save_attached_database,
    remove_attached_database
)
from config_manager import (
    SQLiteConfigManager,
    get_config_db_path,
    get_main_db_path,
    get_studio_config_path,
    load_app_settings,
    save_app_settings,
    APP_SETTINGS
)
from api_generator import (
    limiter,
    get_dynamic_rate_limit,
    handle_streaming_endpoint,
    handle_dynamic_endpoint,
    sync_fastapi_dynamic_routes
)
from scheduler import run_background_scheduler, calculate_next_run

app.state.limiter = limiter
# Import exception handler
from slowapi import _rate_limit_exceeded_handler
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)



def init_saved_queries_table(db_file):
    # Initialized automatically by SQLiteConfigManager
    pass

def seed_database(db_file, force=False, num_customers=400, num_transactions=6500):
    """Seed the database with realistic synthetic data using Faker if empty or forced."""
    conn = duckdb.connect(db_file, config=DB_CONFIG)
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

def detect_parameters(sql: str) -> list:
    import re
    if not sql:
        return []
    matches = re.findall(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}', sql)
    seen = set()
    unique_params = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            unique_params.append(m)
    return unique_params

def substitute_sql_parameters(sql: str, param_values: dict) -> str:
    import re
    if not sql:
        return sql
    
    def replacer(match):
        param_name = match.group(1)
        val = param_values.get(param_name, "")
        if val is None:
            val = ""
        val_str = str(val)
        if re.match(r'^-?\d+(\.\d+)?$', val_str):
            return val_str
        escaped_val = val_str.replace("'", "''")
        return f"'{escaped_val}'"
        
    return re.sub(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}', replacer, sql)

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

def list_seeding_templates():
    """
    Scans /templates and templates folders for SQL files starting with 'seed_'.
    Returns a dictionary of {file_path: display_name}.
    """
    templates = {}
    dirs = ['/templates', 'templates']
    for d in dirs:
        if os.path.exists(d) and os.path.isdir(d):
            try:
                for file_name in os.listdir(d):
                    if file_name.startswith('seed_') and file_name.endswith('.sql'):
                        full_path = os.path.join(d, file_name)
                        # Pretty name: e.g. seed_CarRental.sql -> Car Rental
                        display = file_name[5:-4].replace('_', ' ').replace('-', ' ')
                        # CamelCase to spaces
                        import re
                        display = re.sub(r'(?<!^)(?=[A-Z])', ' ', display)
                        display = ' '.join(display.split())
                        templates[full_path] = f"🌱 {display} ({file_name})"
            except Exception:
                pass
    return templates

# --- APPLICATION PAGE DEFINITION ---
def get_main_db_path():
    """Get the path to the main DuckDB file, using /databases if running in Docker, otherwise databases/main.duckdb."""
    if os.path.exists('/databases') and os.path.isdir('/databases'):
        return '/databases/main.duckdb'
    if not os.path.exists('databases'):
        os.makedirs('databases', exist_ok=True)
    return 'databases/main.duckdb'

DB_NAME = get_main_db_path()

def ensure_ssl_certs():
    cert_dir = 'config/certs'
    os.makedirs(cert_dir, exist_ok=True)
    key_path = os.path.join(cert_dir, 'server.key')
    cert_path = os.path.join(cert_dir, 'server.crt')
    
    if not os.path.exists(key_path) or not os.path.exists(cert_path):
        import subprocess
        print("Generating self-signed SSL certificate for Duckgres PGWire...")
        cmd = [
            "openssl", "req", "-newkey", "rsa:2048", "-new", "-nodes", "-x509",
            "-days", "365",
            "-subj", "/CN=localhost",
            "-keyout", key_path,
            "-out", cert_path
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.chmod(key_path, 0o600)
            os.chmod(cert_path, 0o644)
        except Exception as e:
            print(f"Failed to generate self-signed certificate: {e}")

ensure_ssl_certs()

import sqlite3

config_db = SQLiteConfigManager()



@ui.page('/')
def index():
    global DB_NAME
    ui.query('.nicegui-content').classes('p-0 gap-0')
    
    # Scoping variables for sidebar explorers
    schema_filter_input = None
    current_snippet_category = 'All'
    save_query_category_select = None
    export_db_select = None
    import_db_select = None
    import_schema_select = None
    rename_target_old_name = ''
    rename_target_path = ''
    
    # Scoping variables for Schema Diff Tool
    source_db_select = None
    source_table_select = None
    target_db_select = None
    target_table_select = None
    diff_results_container = None
    
    # State tracking for expanded tree nodes
    tree_state = {'expanded': []}
    
    # State tracking for dropping tables/views
    drop_target_db = ''
    drop_target_schema = ''
    drop_target_table = ''
    drop_title_label = None
    drop_table_dialog = None

    # Right slide-out Query History Drawer Panel
    with ui.right_drawer(value=False, fixed=True, elevated=True).classes('w-96 dark:bg-slate-900 border-l border-slate-200 dark:border-slate-800 p-4 gap-4 flex-col').style('width: 400px;') as history_drawer:
        with ui.row().classes('w-full justify-between items-center no-wrap pb-2 border-b border-slate-100 dark:border-slate-800'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('history', color='primary').classes('text-xl')
                ui.label('Query History Timeline').classes('font-bold text-slate-800 dark:text-slate-100 text-sm')
            with ui.row().classes('items-center gap-1'):
                ui.button(icon='cleaning_services', on_click=lambda: clear_all_history()).props('flat dense size=sm round color=slate').tooltip('Clear all history')
                ui.button(icon='close', on_click=lambda: history_drawer.hide()).props('flat dense size=sm round color=slate')
        
        # History container inside drawer
        history_container = ui.column().classes('w-full gap-3 overflow-auto flex-grow')

    
    # Enable Tailwind glassmorphism and general layout styling
    ui.add_head_html("""
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
        <style>
            .q-tab[name="Apache Superset"] .q-tab__icon img,
            .q-tab[name="Telemetry"] .q-tab__icon img {
                width: 30px !important;
                height: 30px !important;
            }
            
            /* --- TYPOGRAPHY & SMOOTH BG GRADIENTS --- */
            body, html {
                margin: 0 !important;
                padding: 0 !important;
                height: 100vh !important;
                width: 100vw !important;
                overflow: hidden !important;
                font-family: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif !important;
                background-color: #f8fafc;
                background-image: 
                    radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.05) 0px, transparent 50%),
                    radial-gradient(at 50% 0%, rgba(139, 92, 246, 0.04) 0px, transparent 50%),
                    radial-gradient(at 100% 0%, rgba(236, 72, 153, 0.04) 0px, transparent 50%);
                background-attachment: fixed;
                transition: background-color 0.3s ease;
            }
            .body--dark {
                background-color: #030712 !important;
                background-image: 
                    radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.12) 0px, transparent 50%),
                    radial-gradient(at 50% 0%, rgba(139, 92, 246, 0.08) 0px, transparent 50%),
                    radial-gradient(at 100% 0%, rgba(236, 72, 153, 0.08) 0px, transparent 50%) !important;
                background-attachment: fixed;
            }
            
            /* --- GLASSMORPHIC COMPONENT CARD CLASSES --- */
            .glass-card {
                background: rgba(255, 255, 255, 0.45) !important;
                backdrop-filter: blur(16px) saturate(120%) !important;
                -webkit-backdrop-filter: blur(16px) saturate(120%) !important;
                border: 1px solid rgba(255, 255, 255, 0.5) !important;
                border-radius: 12px;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.03) !important;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
            }
            .body--dark .glass-card {
                background: rgba(17, 24, 39, 0.45) !important;
                border: 1px solid rgba(255, 255, 255, 0.06) !important;
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.24) !important;
            }
            .glass-card:hover {
                box-shadow: 0 12px 40px 0 rgba(31, 38, 135, 0.06) !important;
                border-color: rgba(99, 102, 241, 0.3) !important;
            }
            .body--dark .glass-card:hover {
                box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.35) !important;
                border-color: rgba(129, 140, 248, 0.15) !important;
            }

            .custom-header {
                background: rgba(255, 255, 255, 0.6) !important;
                backdrop-filter: blur(20px) !important;
                -webkit-backdrop-filter: blur(20px) !important;
                border-bottom: 1px solid rgba(226, 232, 240, 0.8) !important;
            }
            .body--dark .custom-header {
                background: rgba(15, 23, 42, 0.65) !important;
                border-bottom: 1px solid rgba(255, 255, 255, 0.06) !important;
            }
            
            .sidebar-card {
                background: rgba(255, 255, 255, 0.3) !important;
                backdrop-filter: blur(12px) !important;
                border-right: 1px solid rgba(226, 232, 240, 0.8) !important;
            }
            .body--dark .sidebar-card {
                background: rgba(15, 23, 42, 0.3) !important;
                border-right: 1px solid rgba(255, 255, 255, 0.06) !important;
            }
            
            .dark-bg-panel {
                background-color: rgba(255, 255, 255, 0.5) !important;
                backdrop-filter: blur(8px) !important;
                border: 1px solid rgba(226, 232, 240, 0.7) !important;
                transition: all 0.3s ease;
            }
            .body--dark .dark-bg-panel {
                background-color: rgba(17, 24, 39, 0.5) !important;
                border: 1px solid rgba(255, 255, 255, 0.06) !important;
            }
            .dark-bg-flat {
                background-color: rgba(248, 250, 252, 0.3) !important;
                transition: all 0.3s ease;
            }
            .body--dark .dark-bg-flat {
                background-color: rgba(15, 23, 42, 0.3) !important;
            }

            /* --- CUSTOM SMOOTH SCROLLBARS --- */
            ::-webkit-scrollbar {
                width: 6px;
                height: 6px;
            }
            ::-webkit-scrollbar-track {
                background: transparent;
            }
            ::-webkit-scrollbar-thumb {
                background: rgba(156, 163, 175, 0.25);
                border-radius: 10px;
                transition: background 0.3s ease;
            }
            .body--dark ::-webkit-scrollbar-thumb {
                background: rgba(156, 163, 175, 0.12);
            }
            ::-webkit-scrollbar-thumb:hover {
                background: rgba(99, 102, 241, 0.4);
            }

            /* --- Q-TREE CUSTOM STYLING --- */
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
            
            /* --- CodeMirror Light/Dark Theme Sync --- */
            .cm-editor {
                background-color: rgba(255, 255, 255, 0.5) !important;
                color: #0f172a !important;
                border: 1px solid rgba(203, 213, 225, 0.7) !important;
                border-radius: 8px;
                backdrop-filter: blur(8px) !important;
            }
            .cm-editor .cm-scroller {
                background-color: transparent !important;
            }
            .cm-editor .cm-content {
                color: #0f172a !important;
                font-family: 'JetBrains Mono', monospace !important;
            }
            .cm-editor .cm-gutters {
                background-color: rgba(241, 245, 249, 0.6) !important;
                color: #64748b !important;
                border-right: 1px solid rgba(203, 213, 225, 0.6) !important;
            }
            .body--dark .cm-editor {
                background-color: rgba(15, 23, 42, 0.5) !important;
                color: #f8fafc !important;
                border: 1px solid rgba(255, 255, 255, 0.06) !important;
            }
            .body--dark .cm-editor .cm-content {
                color: #f8fafc !important;
            }
            .body--dark .cm-editor .cm-gutters {
                background-color: rgba(30, 41, 59, 0.5) !important;
                color: #94a3b8 !important;
                border-right: 1px solid rgba(255, 255, 255, 0.04) !important;
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
                 border-radius: 8px !important;
                 background: transparent !important;
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
                 background-color: rgba(255, 255, 255, 0.8) !important;
                 backdrop-filter: blur(10px) !important;
             }
             .body--dark .q-table thead tr th {
                 background-color: rgba(15, 23, 42, 0.8) !important;
                 color: #cbd5e1 !important;
             }

             /* --- Super Dense Inputs for API Docs --- */
             .super-dense-input .q-field__control,
             .super-dense-input .q-field__marginal {
                 height: 26px !important;
                 min-height: 26px !important;
             }
             .super-dense-input .q-field__native,
             .super-dense-input .q-field__input {
                 font-size: 10px !important;
                 height: 26px !important;
                 min-height: 26px !important;
                 padding: 0 4px !important;
             }
        </style>
        <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
        <script>
            mermaid.initialize({ startOnLoad: false, securityLevel: 'loose', theme: 'dark' });
        </script>
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
    query_latency_history = []

    def sanitize_table_name(name):
        import re
        cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        if not cleaned:
            cleaned = "imported_table"
        elif cleaned[0].isdigit():
            cleaned = "t_" + cleaned
        return cleaned.lower()

    def update_import_schemas_for_wizard(db_name):
        try:
            schema_rows = explorer.conn.execute(f"SELECT schema_name FROM duckdb_schemas WHERE database_name = '{db_name}' AND schema_name NOT IN ('information_schema', 'pg_catalog') ORDER BY schema_name").fetchall()
            schemas = [row[0] for row in schema_rows]
            if not schemas:
                schemas = ['main']
            import_schema_select.options = {s: s for s in schemas}
            import_schema_select.value = 'main' if 'main' in schemas else schemas[0]
            import_schema_select.update()
        except Exception as e:
            print(f"Error loading schemas for wizard: {e}")
            import_schema_select.options = {'main': 'main'}
            import_schema_select.value = 'main'
            import_schema_select.update()

    def populate_wizard_databases():
        try:
            db_rows = explorer.conn.execute("SELECT database_name FROM duckdb_databases").fetchall()
            dbs = [row[0] for row in db_rows if row[0] not in ('system', 'temp') and not row[0].startswith('__')]
            options = {db: db for db in dbs}
            import_db_select.options = options
            
            current_active = explorer.conn.execute("SELECT current_database()").fetchone()[0]
            if import_db_select.value not in options:
                if current_active in options:
                    import_db_select.value = current_active
                elif dbs:
                    import_db_select.value = dbs[0]
            import_db_select.update()
            update_import_schemas_for_wizard(import_db_select.value)
            
            # Populates the dropdown options for Schema Diff tool too
            populate_diff_databases()
        except Exception as e:
            print(f"Error loading databases for wizard: {e}")

    def update_diff_tables(db_name, select_widget):
        try:
            if not db_name:
                select_widget.options = {}
                select_widget.value = None
                select_widget.update()
                return
            table_rows = explorer.conn.execute(f"""
                SELECT table_name FROM duckdb_tables WHERE database_name = '{db_name}' AND schema_name = 'main'
                UNION
                SELECT view_name AS table_name FROM duckdb_views WHERE database_name = '{db_name}' AND schema_name = 'main'
                ORDER BY table_name;
            """).fetchall()
            tables = [row[0] for row in table_rows]
            options = {t: t for t in tables}
            select_widget.options = options
            select_widget.value = tables[0] if tables else None
            select_widget.update()
        except Exception as e:
            print(f"Error loading tables for diff: {e}")

    def populate_diff_databases():
        try:
            if not source_db_select or not target_db_select:
                return
            db_rows = explorer.conn.execute("SELECT database_name FROM duckdb_databases").fetchall()
            dbs = [row[0] for row in db_rows if row[0] not in ('system', 'temp') and not row[0].startswith('__')]
            options = {db: db for db in dbs}
            
            source_db_select.options = options
            target_db_select.options = options
            
            current_active = explorer.conn.execute("SELECT current_database()").fetchone()[0]
            if source_db_select.value not in options:
                if current_active in options:
                    source_db_select.value = current_active
                    target_db_select.value = current_active
                elif dbs:
                    source_db_select.value = dbs[0]
                    target_db_select.value = dbs[0]
                    
            source_db_select.update()
            target_db_select.update()
            
            update_diff_tables(source_db_select.value, source_table_select)
            update_diff_tables(target_db_select.value, target_table_select)
        except Exception as e:
            print(f"Error populating diff databases: {e}")

    def run_schema_diff():
        src_db = source_db_select.value
        src_tbl = source_table_select.value
        tgt_db = target_db_select.value
        tgt_tbl = target_table_select.value
        
        if not src_db or not src_tbl or not tgt_db or not tgt_tbl:
            ui.notify("Please select both source and target databases and tables.", type='warning')
            return
            
        try:
            # Query source columns
            src_cols = {row[0]: row[1] for row in explorer.conn.execute("""
                SELECT column_name, data_type 
                FROM duckdb_columns 
                WHERE database_name = ? AND schema_name = 'main' AND table_name = ?
                ORDER BY column_index;
            """, [src_db, src_tbl]).fetchall()}
            
            # Query target columns
            tgt_cols = {row[0]: row[1] for row in explorer.conn.execute("""
                SELECT column_name, data_type 
                FROM duckdb_columns 
                WHERE database_name = ? AND schema_name = 'main' AND table_name = ?
                ORDER BY column_index;
            """, [tgt_db, tgt_tbl]).fetchall()}
            
            if not src_cols and not tgt_cols:
                ui.notify("No column metadata found for the selected tables.", type='warning')
                return
                
            diff_results_container.clear()
            
            all_cols = sorted(list(set(src_cols.keys()) | set(tgt_cols.keys())))
            
            with diff_results_container:
                # Header row
                with ui.row().classes('w-full items-center justify-between no-wrap bg-indigo-500/10 p-3 rounded-lg border border-indigo-500/20 text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2'):
                    ui.label("Column Name").classes('w-1/4')
                    ui.label("Source Type").classes('w-1/5 text-center')
                    ui.label("Status").classes('w-1/5 text-center')
                    ui.label("Target Type").classes('w-1/5 text-center')
                    ui.label("Action Recommendation").classes('w-1/5 text-right')
                
                has_drift = False
                for col in all_cols:
                    src_type = src_cols.get(col)
                    tgt_type = tgt_cols.get(col)
                    
                    if src_type and tgt_type:
                        if src_type.lower() == tgt_type.lower():
                            status = "MATCH"
                            badge_color = "emerald"
                            badge_icon = "check_circle"
                            recommendation = "No drift detected."
                        else:
                            status = "MISMATCH"
                            badge_color = "amber"
                            badge_icon = "warning"
                            recommendation = f"Alter Target to {src_type}"
                            has_drift = True
                    elif src_type:
                        status = "MISSING IN TARGET"
                        badge_color = "rose"
                        badge_icon = "remove_circle"
                        recommendation = f"Add column to Target"
                        has_drift = True
                    else:
                        status = "MISSING IN SOURCE"
                        badge_color = "rose"
                        badge_icon = "add_circle"
                        recommendation = "Drop target column or add to source"
                        has_drift = True
                        
                    with ui.row().classes('w-full items-center justify-between no-wrap p-2.5 rounded-lg border border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-900 transition mb-1 text-xs'):
                        ui.label(col).classes('w-1/4 font-mono font-bold text-slate-800 dark:text-slate-200')
                        ui.label(src_type if src_type else "—").classes('w-1/5 text-center font-mono text-slate-500')
                        
                        with ui.row().classes('w-1/5 justify-center items-center gap-1'):
                            ui.icon(badge_icon, color=badge_color)
                            ui.label(status).classes(f'text-[10px] font-bold text-{badge_color}-600 dark:text-{badge_color}-400')
                            
                        ui.label(tgt_type if tgt_type else "—").classes('w-1/5 text-center font-mono text-slate-500')
                        ui.label(recommendation).classes(f'w-1/5 text-right font-medium text-[11px] ' + ('text-slate-400' if status == "MATCH" else 'text-amber-500 font-bold' if status == "MISMATCH" else 'text-rose-500 font-bold'))
                
                if not has_drift:
                    ui.notify("Schemas are fully synchronized! No drift detected.", type='success')
                else:
                    ui.notify("Schema drift detected between selected tables.", type='warning')
        except Exception as ex:
            ui.notify(f"Failed to compare schemas: {ex}", type='negative')

    async def handle_local_file_upload(e):
        filename = e.file.name
        target_dir = '/shared'
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, filename)
        
        try:
            await e.file.save(target_path)
            
            ext = os.path.splitext(filename)[1].lower()
            table_name = sanitize_table_name(os.path.splitext(filename)[0])
            
            target_db = import_db_select.value or 'main'
            target_schema = import_schema_select.value or 'main'
            fq_name = f'"{target_db}"."{target_schema}"."{table_name}"'
            
            if ext == '.parquet':
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_parquet('{target_path}');"
            elif ext == '.json':
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_json('{target_path}', format='auto');"
            else:
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_csv('{target_path}', header=true, auto_detect=true);"
                
            explorer.conn.execute(sql)
            ui.notify(f"Successfully uploaded and imported local file to table '{table_name}' in {target_db}.{target_schema}", type='success')
            
            try:
                col_rows = explorer.conn.execute(f"PRAGMA table_info('{fq_name}')").fetchall()
                cols = [(r[1],) for r in col_rows]
                sql_editor.value = format_column_projection_query(cols, fq_name)
            except Exception:
                sql_editor.value = f"SELECT * FROM {fq_name} LIMIT 100;"
            refresh_schema_tree()
            tabs.value = 'Explorer'
            run_editor_query()
        except Exception as ex:
            ui.notify(f"Failed to import local file: {ex}", type='negative', duration=7)

    async def trigger_url_import(url, format_type, custom_table):
        if not url or not url.strip():
            ui.notify("Please specify a remote HTTP/HTTPS URL", type='warning')
            return
        
        url = url.strip()
        tbl_name = sanitize_table_name(custom_table.strip() if custom_table and custom_table.strip() else "remote_dataset")
        
        target_db = import_db_select.value or 'main'
        target_schema = import_schema_select.value or 'main'
        fq_name = f'"{target_db}"."{target_schema}"."{tbl_name}"'
        
        ui.notify("Connecting and importing remote dataset...", type='info')
        try:
            explorer.conn.execute("INSTALL httpfs; LOAD httpfs;")
            fmt = format_type.upper()
            if fmt == 'PARQUET':
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_parquet('{url}');"
            elif fmt == 'JSON':
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_json('{url}', format='auto');"
            elif fmt == 'CSV':
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_csv('{url}', header=true, auto_detect=true);"
            else:
                ext = url.split('?')[0].split('.')[-1].lower()
                if ext == 'parquet':
                    sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_parquet('{url}');"
                elif ext == 'json':
                    sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_json('{url}', format='auto');"
                else:
                    sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_csv('{url}', header=true, auto_detect=true);"
                    
            explorer.conn.execute(sql)
            ui.notify(f"Successfully imported dataset to table '{tbl_name}' in {target_db}.{target_schema}", type='success')
            
            try:
                col_rows = explorer.conn.execute(f"PRAGMA table_info('{fq_name}')").fetchall()
                cols = [(r[1],) for r in col_rows]
                sql_editor.value = format_column_projection_query(cols, fq_name)
            except Exception:
                sql_editor.value = f"SELECT * FROM {fq_name} LIMIT 100;"
            refresh_schema_tree()
            tabs.value = 'Explorer'
            run_editor_query()
        except Exception as ex:
            ui.notify(f"Failed to import remote dataset: {ex}", type='negative', duration=7)

    async def trigger_s3_import(s3_uri, format_type, access_key, secret_key, session_token, region, custom_table):
        if not s3_uri or not s3_uri.strip():
            ui.notify("Please specify a valid S3 URI (s3://...)", type='warning')
            return
            
        s3_uri = s3_uri.strip()
        tbl_name = sanitize_table_name(custom_table.strip() if custom_table and custom_table.strip() else "s3_dataset")
        
        target_db = import_db_select.value or 'main'
        target_schema = import_schema_select.value or 'main'
        fq_name = f'"{target_db}"."{target_schema}"."{tbl_name}"'
        
        ui.notify("Connecting and importing S3 dataset...", type='info')
        try:
            explorer.conn.execute("INSTALL httpfs; LOAD httpfs;")
            explorer.conn.execute("RESET s3_region; RESET s3_access_key_id; RESET s3_secret_access_key; RESET s3_session_token;")
            if region and region.strip():
                explorer.conn.execute(f"SET s3_region='{region.strip()}';")
            if access_key and access_key.strip():
                explorer.conn.execute(f"SET s3_access_key_id='{access_key.strip()}';")
            if secret_key and secret_key.strip():
                explorer.conn.execute(f"SET s3_secret_access_key='{secret_key.strip()}';")
            if session_token and session_token.strip():
                explorer.conn.execute(f"SET s3_session_token='{session_token.strip()}';")
                
            fmt = format_type.upper()
            if fmt == 'PARQUET':
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_parquet('{s3_uri}');"
            elif fmt == 'JSON':
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_json('{s3_uri}', format='auto');"
            elif fmt == 'CSV':
                sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_csv('{s3_uri}', header=true, auto_detect=true);"
            else:
                ext = s3_uri.split('?')[0].split('.')[-1].lower()
                if ext == 'parquet':
                    sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_parquet('{s3_uri}');"
                elif ext == 'json':
                    sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_json('{s3_uri}', format='auto');"
                else:
                    sql = f"CREATE OR REPLACE TABLE {fq_name} AS SELECT * FROM read_csv('{s3_uri}', header=true, auto_detect=true);"
                    
            explorer.conn.execute(sql)
            ui.notify(f"Successfully imported S3 dataset to table '{tbl_name}' in {target_db}.{target_schema}", type='success')
            
            try:
                col_rows = explorer.conn.execute(f"PRAGMA table_info('{fq_name}')").fetchall()
                cols = [(r[1],) for r in col_rows]
                sql_editor.value = format_column_projection_query(cols, fq_name)
            except Exception:
                sql_editor.value = f"SELECT * FROM {fq_name} LIMIT 100;"
            refresh_schema_tree()
            tabs.value = 'Explorer'
            run_editor_query()
        except Exception as ex:
            ui.notify(f"Failed to import S3 dataset: {ex}", type='negative', duration=7)

    def get_system_memory():
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_total = 0
            mem_free = 0
            mem_available = 0
            for line in lines:
                if line.startswith('MemTotal:'):
                    mem_total = int(line.split()[1]) * 1024
                elif line.startswith('MemFree:'):
                    mem_free = int(line.split()[1]) * 1024
                elif line.startswith('MemAvailable:'):
                    mem_available = int(line.split()[1]) * 1024
            mem_used = mem_total - mem_available if mem_available else mem_total - mem_free
            return mem_used, mem_total
        except Exception:
            return 0, 0

    def get_cpu_load():
        try:
            with open('/proc/loadavg', 'r') as f:
                load = float(f.read().split()[0])
            import os
            num_cpus = os.cpu_count() or 1
            cpu_pct = min(100.0, (load / num_cpus) * 100.0)
            return cpu_pct
        except Exception:
            return 0.0

    def parse_dbt_manifest():
        import json
        import os
        manifest_path = '/app/dbt_project/target/manifest.json'
        if not os.path.exists(manifest_path):
            return None, "manifest.json not found. Compile the dbt project first."
            
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
                
            nodes = manifest.get('nodes', {})
            sources = manifest.get('sources', {})
            
            dag = {}
            node_details = {}
            
            for node_id, node in nodes.items():
                if node.get('resource_type') == 'model':
                    name = node.get('name')
                    depends_on_nodes = node.get('depends_on', {}).get('nodes', [])
                    
                    parents = []
                    for parent_id in depends_on_nodes:
                        if parent_id.startswith('model.') or parent_id.startswith('source.'):
                            parent_name = parent_id.split('.')[-1]
                            parents.append(parent_name)
                    
                    dag[name] = parents
                    node_details[name] = {
                        'id': node_id,
                        'resource_type': 'model',
                        'materialized': node.get('config', {}).get('materialized', 'table'),
                        'path': node.get('original_file_path', '')
                    }
                    
            for source_id, source in sources.items():
                name = source.get('identifier') or source.get('name')
                dag[name] = []
                node_details[name] = {
                    'id': source_id,
                    'resource_type': 'source',
                    'materialized': 'source',
                    'path': source.get('original_file_path', '')
                }
                
            return dag, node_details
        except Exception as e:
            return None, f"Error parsing manifest: {str(e)}"

    def generate_dbt_mermaid(dag, node_details):
        mermaid_lines = ["graph TD"]
        for node_name, details in node_details.items():
            res_type = details['resource_type']
            mat = details['materialized']
            
            if res_type == 'source':
                mermaid_lines.append(f'  {node_name}[("💾 {node_name} (Source)")]')
                mermaid_lines.append(f'  style {node_name} fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0369a1')
            elif mat == 'view':
                mermaid_lines.append(f'  {node_name}["👁️ {node_name} (View)"]')
                mermaid_lines.append(f'  style {node_name} fill:#f0fdf4,stroke:#16a34a,stroke-width:2px,color:#15803d')
            else:
                mermaid_lines.append(f'  {node_name}["📊 {node_name} (Table)"]')
                mermaid_lines.append(f'  style {node_name} fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#b45309')
                
        for child, parents in dag.items():
            for parent in parents:
                if parent in node_details:
                    mermaid_lines.append(f"  {parent} --> {child}")
                    
        return "\n".join(mermaid_lines)

    def run_dbt_model_locally(model_name, details):
        import os
        proj_name = 'test_project'
        compiled_path = f"/app/dbt_project/target/compiled/{proj_name}/{details['path']}"
        
        if not os.path.exists(compiled_path):
            return False, f"Compiled SQL not found at {compiled_path}. Run dbt compile first."
            
        try:
            with open(compiled_path, 'r') as f:
                sql_code = f.read().strip()
                
            try:
                attached = explorer.conn.execute("SELECT database_name FROM duckdb_databases()").fetchall()
                attached_names = [a[0] for a in attached]
                if 'test_project_db' not in attached_names:
                    explorer.conn.execute("ATTACH '/app/dbt_data/test_project.duckdb' AS test_project_db;")
            except Exception as attach_err:
                print(f"Error attaching test_project_db: {attach_err}")
                
            mat = details.get('materialized', 'table')
            fq_name = f"test_project_db.main.{model_name}"
            
            if mat == 'view':
                exec_sql = f"CREATE OR REPLACE VIEW {fq_name} AS {sql_code}"
            else:
                exec_sql = f"CREATE OR REPLACE TABLE {fq_name} AS {sql_code}"
                
            explorer.conn.execute(exec_sql)
            return True, f"Successfully materialised model {model_name} as {mat.upper()} in test_project_db!"
        except Exception as e:
            return False, f"Failed to run model: {str(e)}"

    def open_dbt_lineage_dialog():
        dag, node_details = parse_dbt_manifest()
        if dag is None:
            ui.notify(f"Cannot load lineage: {node_details}", type='negative')
            return
            
        mermaid_code = generate_dbt_mermaid(dag, node_details)
        
        with ui.dialog().classes('w-11/12 max-w-5xl') as dialog, ui.card().classes('w-full p-6 dark-bg-panel'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('lan', color='primary').classes('text-2xl')
                    ui.label('dbt Model Lineage Dependency Graph').classes('text-xl font-bold text-slate-800 dark:text-white')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense')
                
            ui.separator().classes('mb-4')
            
            with ui.row().classes('w-full gap-6 no-wrap items-stretch'):
                with ui.card().classes('p-4 border rounded-xl flex-grow items-center justify-center overflow-auto bg-slate-50 dark:bg-slate-900').style('min-height: 400px; max-height: 600px;'):
                    lineage_mermaid = ui.mermaid(mermaid_code).classes('w-full')
                    
                with ui.card().classes('w-80 p-4 border rounded-xl flex-col gap-4 flex-none'):
                    ui.label('Model Information').classes('text-sm font-semibold uppercase text-slate-400')
                    
                    model_options = sorted(list(dag.keys()))
                    info_area = ui.column().classes('w-full gap-3')
                    
                    def on_model_select(e):
                        info_area.clear()
                        model_name = e.value
                        if not model_name:
                            return
                            
                        details = node_details[model_name]
                        with info_area:
                            ui.label(f"Name: {model_name}").classes('font-bold text-lg text-slate-800 dark:text-white')
                            ui.label(f"Type: {details['resource_type'].upper()}").classes('text-xs text-slate-400')
                            ui.label(f"Materialized: {details['materialized'].upper()}").classes('text-xs text-slate-400')
                            ui.label(f"Path: {details['path']}").classes('text-xs text-slate-500 font-mono break-all')
                            
                            if details['resource_type'] == 'model':
                                proj_name = 'test_project'
                                compiled_path = f"/app/dbt_project/target/compiled/{proj_name}/{details['path']}"
                                sql_preview = ""
                                if os.path.exists(compiled_path):
                                    with open(compiled_path, 'r') as sf:
                                        sql_preview = sf.read()
                                        
                                if sql_preview:
                                    with ui.expansion('Compiled SQL Preview', icon='code').classes('w-full border rounded'):
                                        ui.code(sql_preview, language='sql').classes('text-xs w-full max-h-40 overflow-auto')
                                        
                                async def handle_run_model():
                                    ui.notify(f"Materializing model {model_name} locally...", type='info')
                                    success, msg = run_dbt_model_locally(model_name, details)
                                    if success:
                                        ui.notify(msg, type='success')
                                    else:
                                        ui.notify(msg, type='negative')
                                        
                                ui.button('Run & Materialize Model', icon='play_arrow', on_click=handle_run_model).props('elevated dense color=primary').classes('w-full py-2')
                                
                    model_dropdown = ui.select(options=model_options, label='Select a node to inspect', on_change=on_model_select).props('outlined dense').classes('w-full')
                    
            dialog.open()

    # Helper function for bytes export
    def get_csv_bytes(columns, rows):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(col, '') for col in columns])
        return output.getvalue().encode('utf-8')

    # --- DYNAMIC PARAMETER INPUTS FOR SAVED QUERIES ---
    parameter_input_fields = {}

    def refresh_parameter_inputs(sql_text):
        params = detect_parameters(sql_text)
        if not params:
            parameter_inputs_card.style('display: none;')
            parameter_inputs_card.clear()
            parameter_input_fields.clear()
            return
            
        if set(params) == set(parameter_input_fields.keys()):
            return
            
        parameter_inputs_card.style('display: block;')
        parameter_inputs_card.clear()
        parameter_input_fields.clear()
        
        with parameter_inputs_card:
            with ui.row().classes('items-center gap-1.5 pb-1 text-slate-700 dark:text-slate-300'):
                ui.icon('tune', size='xs')
                ui.label('Query Parameters').classes('font-bold text-[11px] uppercase tracking-wider')
                
            with ui.row().classes('w-full gap-3 flex-wrap items-center'):
                for p in params:
                    p_lower = p.lower()
                    label = p.replace('_', ' ').title()
                    
                    if 'date' in p_lower:
                        inp = ui.input(label, placeholder='YYYY-MM-DD').props('outlined dense').style('width: 160px;')
                    elif 'id' in p_lower or 'limit' in p_lower or 'count' in p_lower or 'num' in p_lower:
                        inp = ui.number(label, placeholder='e.g. 10').props('outlined dense').style('width: 140px;')
                    else:
                        inp = ui.input(label, placeholder='Value...').props('outlined dense').classes('flex-grow')
                        
                    parameter_input_fields[p] = inp
        parameter_inputs_card.update()

    # --- LIVE LINTER BACKGROUND DEBOUNCED VALIDATOR ---
    validation_task = None

    async def validate_sql_on_change(e):
        app.storage.user['last_query'] = e.value
        refresh_parameter_inputs(e.value)
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
            
        # Resolve parameters for background validation using placeholder dummy values
        linter_params = detect_parameters(sql)
        linter_param_values = {}
        for p in linter_params:
            p_lower = p.lower()
            if 'date' in p_lower:
                linter_param_values[p] = '2026-06-15'
            elif any(k in p_lower for k in ('id', 'limit', 'count', 'num', 'dist', 'lower', 'upper', 'amount', 'val')):
                linter_param_values[p] = 1
            else:
                linter_param_values[p] = 'value'
        
        sql_lint = substitute_sql_parameters(sql, linter_param_values)
            
        # Get active database context and list of attached databases from explorer connection
        active_db = 'main'
        db_list = []
        try:
            active_db = explorer.conn.execute("SELECT current_database();").fetchone()[0]
            db_list = explorer.conn.execute("SELECT database_name, path FROM duckdb_databases").fetchall()
        except Exception as e:
            print(f"Error fetching active context/databases for linter: {e}")

        # Build known db configuration lookup
        db_configs = {}
        try:
            config_path = get_config_path()
            if os.path.exists(config_path):
                import yaml
                with open(config_path, 'r') as f:
                    cfg = yaml.safe_load(f)
                if cfg and 'databases' in cfg:
                    for db in cfg['databases']:
                        db_configs[db.get('name')] = db
        except Exception:
            pass

        # Build attach queries to replicate context in validation connection
        attach_sqls = []
        for db_name, db_path in db_list:
            if db_name in ('system', 'temp', 'main') or db_name.startswith('__'):
                continue
            
            if db_name in db_configs:
                db_cfg = db_configs[db_name]
                db_type = db_cfg.get('type')
                path = db_cfg.get('path')
                options = db_cfg.get('options', {})
                
                ext_load = ""
                if db_type in ('ducklake', 'sqlite', 'postgres', 'mysql'):
                    ext_load = f"INSTALL {db_type}; LOAD {db_type}; "
                    
                if db_type == 'ducklake':
                    data_path = options.get('data_path', 'data_parquet/')
                    sql_cmd = f"{ext_load}ATTACH 'ducklake:{path}' AS {db_name} (DATA_PATH '{data_path}');"
                elif db_type == 'sqlite':
                    sql_cmd = f"{ext_load}ATTACH '{path}' AS {db_name} (TYPE sqlite);"
                elif db_type == 'postgres':
                    sql_cmd = f"{ext_load}ATTACH '{path}' AS {db_name} (TYPE postgres);"
                elif db_type == 'mysql':
                    sql_cmd = f"{ext_load}ATTACH '{path}' AS {db_name} (TYPE mysql);"
                else:
                    sql_cmd = f"ATTACH '{path}' AS {db_name};"
                attach_sqls.append(sql_cmd)
            else:
                if db_path:
                    if db_path.startswith('ducklake:'):
                        sql_cmd = f"INSTALL ducklake; LOAD ducklake; ATTACH '{db_path}' AS {db_name};"
                    elif db_path.endswith('.db') or db_path.endswith('.sqlite') or db_path.endswith('.sqlite3'):
                        sql_cmd = f"INSTALL sqlite; LOAD sqlite; ATTACH '{db_path}' AS {db_name} (TYPE sqlite);"
                    else:
                        sql_cmd = f"ATTACH '{db_path}' AS {db_name};"
                    attach_sqls.append(sql_cmd)

        loop = asyncio.get_event_loop()
        def run_explain():
            first_word = sql.split()[0].lower() if sql.split() else ""
            if first_word in ('use', 'install', 'load', 'attach', 'detach'):
                return None

            try:
                chk_conn = duckdb.connect(explorer.db_file, config=DB_CONFIG)
                try:
                    for attach_cmd in attach_sqls:
                        try:
                            chk_conn.execute(attach_cmd)
                        except Exception as ex:
                            print(f"Linter failed to auto-attach database {attach_cmd}: {ex}")
                    
                    if active_db:
                        try:
                            chk_conn.execute(f"USE {active_db};")
                        except Exception as ex:
                            print(f"Linter failed to switch to active database {active_db}: {ex}")

                    chk_conn.execute(f"EXPLAIN {sql_lint}")
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
        def show_snippet_dialog(q_id, q_name, q_desc, q_sql, q_cat, cat_color):
            with ui.context.client:
                with ui.dialog() as dialog, ui.card().classes('w-[50vw] max-w-2xl p-4 gap-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-xl dark-bg-panel'):
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        with ui.row().classes('items-center gap-2 no-wrap'):
                            ui.badge(q_cat, color=cat_color).classes('text-xs py-0.5 px-1.5')
                            ui.label(q_name).classes('text-base font-bold text-slate-800 dark:text-slate-100 truncate')
                        ui.button(icon='close', on_click=dialog.close).props('flat round dense size=sm color=slate').classes('text-slate-400')
                        
                    if q_desc:
                        ui.label(q_desc).classes('text-xs text-slate-500 dark:text-slate-400 leading-relaxed font-normal whitespace-pre-wrap w-full')
                        
                    # Code block (Editable)
                    code_textarea = ui.textarea(value=q_sql).classes('text-xs w-full p-2 rounded-lg bg-slate-900 text-slate-100 font-mono border border-slate-800').props('input-class="font-mono text-slate-100" filled autogrow shadow-none dense').style('font-family: monospace; color: #f1f5f9; background: #0f172a;')
                    
                    # Action row
                    with ui.row().classes('w-full justify-between items-center mt-2 pt-2 border-t border-slate-100 dark:border-slate-800'):
                        with ui.row().classes('items-center gap-2'):
                            ui.button('Execute Query', icon='play_arrow', on_click=lambda: [run_snippet_immediately(code_textarea.value), dialog.close()]).props('elevated color=positive').classes('text-xs font-bold')
                            ui.button('Load to Editor', icon='arrow_forward', on_click=lambda: [load_history_query(code_textarea.value), dialog.close()]).props('outline color=primary').classes('text-xs font-bold')
                            ui.button('Copy SQL', icon='content_copy', on_click=lambda: copy_snippet_to_clipboard(code_textarea.value)).props('flat color=secondary').classes('text-xs')
                            
                            def make_save_handler(qid=q_id, ta=code_textarea):
                                def save():
                                    if explorer.update_saved_query(qid, ta.value):
                                        ui.notify("Snippet updated successfully!", type='positive')
                                        refresh_saved_queries_list()
                                    else:
                                        ui.notify("Failed to update snippet.", type='negative')
                                return save
                            ui.button('Save Changes', icon='save', on_click=make_save_handler()).props('elevated color=primary').classes('text-xs font-bold')
                            
                        ui.button('Delete', icon='delete', on_click=lambda: [confirm_delete_query(q_id, q_name), dialog.close()]).props('flat color=negative').classes('text-xs')
                    dialog.open()

        saved_queries_container.clear()
        with saved_queries_container:
            queries = explorer.list_saved_queries()
            if not queries:
                ui.label('No saved queries yet. Click "Save Query" to create one.').classes('text-xs text-slate-400 text-center py-4 w-full font-normal')
                return
                
            # Filter the queries based on the user search keyword and category toggle
            filter_text = saved_queries_filter.value.strip().lower() if saved_queries_filter and saved_queries_filter.value else ""
            selected_cat = current_snippet_category
            
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
                
                with ui.card().classes('w-full p-2 border rounded shadow-none dark-bg-panel overflow-hidden cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-850 transition flex-none').style('border-color: var(--q-slate-200);') as snippet_card:
                    if q_desc:
                        snippet_card.tooltip(q_desc)
                    
                    def make_click_handler(qid=q_id, qname=q_name, qdesc=q_desc, qsql=q_sql, qcat=q_cat, ccol=cat_color):
                        return lambda _: show_snippet_dialog(qid, qname, qdesc, qsql, qcat, ccol)
                    snippet_card.on('click', make_click_handler())
                    
                    with ui.row().classes('w-full items-center justify-between no-wrap'):
                        with ui.row().classes('items-center gap-1.5 no-wrap'):
                            ui.badge(q_cat, color=cat_color).classes('text-[8px] py-0.5 px-1 flex-none')
                            ui.label(q_name).classes('text-xs font-bold text-slate-800 dark:text-slate-200 truncate')
                        ui.icon('open_in_new', size='xs').classes('text-slate-400')

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
                ui.label('DuckDB Data Studio').classes('text-lg font-bold text-white')
            
            # Retrieve last selected tab or default to 'Explorer'
            try:
                last_tab = app.storage.user.get('active_tab', 'Explorer')
            except Exception:
                last_tab = 'Explorer'
            if last_tab not in ['Explorer', 'JupyterLab', 'Code Editor', 'Extensions', 'Database Tools', 'API Endpoints', 'API Docs & Explorer', 'Scheduler', 'Garage S3', 'Telemetry', 'Apache Superset', 'Settings']:
                last_tab = 'Explorer'
                
            with ui.tabs(value=last_tab, on_change=lambda e: handle_tab_change_global(e.value)).props('inline-label dense align=right').classes('text-white flex-grow') as tabs:
                studio_tab = ui.tab(name='Explorer', label='', icon='img:/explorer_colored.svg').tooltip('Explorer (SQL & Schema)')
                jupyter_tab = ui.tab(name='JupyterLab', label='', icon='img:/jupyter_orange.svg').tooltip('JupyterLab Notebooks')
                # dbt_tab = ui.tab(name='dbt Workbench', label='', icon='img:/dbt_orange.svg').tooltip('dbt Workbench')
                editor_tab = ui.tab(name='Code Editor', label='', icon='img:/vscode_blue.svg').tooltip('Code Editor (VS Code)')
                extensions_tab = ui.tab(name='Extensions', label='', icon='img:/extensions_teal.svg').tooltip('Extensions Manager')
                db_tools_tab = ui.tab(name='Database Tools', label='', icon='img:/db_tools_colored.svg').tooltip('Database Tools & Seeding')
                api_creator_tab = ui.tab(name='API Endpoints', label='', icon='img:/api_endpoint_colored.svg').tooltip('API Endpoints Creator')
                api_docs_tab = ui.tab(name='API Docs & Explorer', label='', icon='img:/swagger_green.svg').tooltip('API Docs & Swagger UI')
                scheduler_tab = ui.tab(name='Scheduler', label='', icon='img:/scheduler_colored.svg').tooltip('Background Query Scheduler')
                garage_tab = ui.tab(name='Garage S3', label='', icon='img:/garage_orange.svg').tooltip('Garage S3 Console')
                telemetry_tab = ui.tab(name='Telemetry', label='', icon='img:/telemetry_colored.svg').tooltip('Telemetry & Observability')
                superset_tab = ui.tab(name='Apache Superset', label='', icon='img:/superset_logo.svg').tooltip('Apache Superset BI Reporting')
                settings_tab = ui.tab(name='Settings', label='', icon='img:/settings_colored.svg').tooltip('Studio Settings')
            
        studio_container = ui.row().classes('w-full h-full no-wrap min-h-0 flex-grow').style('margin: 0; padding: 0;')
        jupyter_container = ui.column().classes('w-full h-full min-h-0 flex-grow').style('margin: 0; padding: 0;')
        dbt_workbench_container = ui.column().classes('w-full h-full min-h-0 flex-grow').style('margin: 0; padding: 0;')
        code_editor_container = ui.column().classes('w-full h-full min-h-0 flex-grow').style('margin: 0; padding: 0;')
        extensions_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-4 flex-nowrap').style('margin: 0; padding: 0;')
        db_tools_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        api_creator_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        api_docs_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        scheduler_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        garage_container = ui.column().classes('w-full h-full min-h-0 flex-grow').style('margin: 0; padding: 0;')
        telemetry_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        superset_container = ui.column().classes('w-full h-full min-h-0 flex-grow').style('margin: 0; padding: 0;')
        settings_container = ui.column().classes('w-full min-h-0 flex-grow p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap').style('margin: 0; padding: 0;')
        
        # Build Database Tools Container Content
        with db_tools_container:
            # Header Card
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('items-center gap-3'):
                    ui.icon('construction', color='primary').classes('text-3xl')
                    ui.label('Database Utilities & Tools').classes('text-2xl font-black text-slate-800 dark:text-white')
                ui.label('Perform high-performance local data backups, restore catalog structures, and re-seed the core database tables with customizable record densities.').classes('text-sm text-slate-500 dark:text-slate-400')
            
            # Sub Tabs for database tools
            with ui.tabs().classes('w-full border-b flex-none') as db_tools_subtabs:
                backup_restore_tab = ui.tab('Backup & Restore', icon='settings_backup_restore')
                ingestion_seeding_tab = ui.tab('Ingestion & Seeding', icon='science')
                schema_diff_tab = ui.tab('Schema Diff Tool', icon='difference')
                
            with ui.tab_panels(db_tools_subtabs, value=backup_restore_tab).classes('w-full bg-transparent min-h-0 flex-grow p-0').style('padding: 0;'):
                # TAB 1: Backup & Restore
                with ui.tab_panel(backup_restore_tab).classes('gap-6 p-0 flex-col'):
                    with ui.grid().classes('grid grid-cols-1 md:grid-cols-2 gap-6 w-full flex-none'):
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

                # TAB 2: Ingestion & Seeding
                with ui.tab_panel(ingestion_seeding_tab).classes('gap-6 p-0 flex-col'):
                    # CARD 3: Synthetic Seeding Engine
                    with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4 flex-none'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('science', color='warning').classes('text-2xl')
                            ui.label('Synthetic Seeding Engine').classes('text-lg font-bold text-slate-800 dark:text-white')
                        ui.separator().classes('opacity-50')
                        ui.label('Select a predefined schema template to generate a new attached DuckDB database, populated with synthetic or real-world datasets.').classes('text-xs text-slate-400 leading-relaxed max-w-3xl')
                        
                        # Controls row
                        with ui.row().classes('w-full items-center gap-4 flex-wrap'):
                            seed_templates = list_seeding_templates()
                            if seed_templates:
                                # Define template select dropdown
                                # 1. Define density select dropdown first so the handler can reference it
                                density_select = ui.select(
                                    options={
                                        '1000': '1,000 Rows (Light)',
                                        '6500': '6,500 Rows (Standard)',
                                        '15000': '15,000 Rows (Dense)',
                                        '300000': '300,000 Rows (Super Dense)',
                                        '1500000': '1,500,000 Rows (Ultra Dense)'
                                    },
                                    value='6500',
                                    label='Mock Data Density'
                                ).props('dense outlined').style('width: 220px;')
                                
                                # 2. Define handler referencing density_select
                                def handle_template_select_change(e):
                                    val = e.value.lower() if e.value else ''
                                    is_live = any(k in val for k in ['railway', 'taxi', 'github', 'openaq', 'open_aq', 'weather'])
                                    if is_live:
                                        density_select.disable()
                                        ui.notify("Mock data density disabled (loads real-world dataset directly).", type='info')
                                    else:
                                        density_select.enable()
                                
                                # 3. Define template select dropdown passing handler to on_change
                                seed_select = ui.select(
                                    options=seed_templates,
                                    value=next(iter(seed_templates.keys())),
                                    label='Select Schema / Model',
                                    on_change=handle_template_select_change
                                ).props('dense outlined').classes('flex-grow').style('min-width: 250px;')
                                
                                # Initial check on creation
                                val_init = seed_select.value.lower() if seed_select.value else ''
                                if any(k in val_init for k in ['railway', 'taxi', 'github', 'openaq', 'open_aq', 'weather']):
                                    density_select.disable()
                                
                                ui.button('Create & Seed Database', icon='play_circle_filled', color='primary',
                                          on_click=lambda: trigger_template_seed(seed_select.value, density_select.value)).props('elevated dense').classes('px-4 py-2')
                            else:
                                ui.label('No seed_*.sql templates found in /templates directory.').classes('text-xs text-amber-500 italic')

                    # CARD 4: Dynamic File Import Wizard
                    with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4 flex-none'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('cloud_upload', color='primary').classes('text-2xl')
                            ui.label('Dynamic File Import Wizard').classes('text-lg font-bold text-slate-800 dark:text-white')
                        ui.separator().classes('opacity-50')
                        
                        # Added target database and schema selectors for Dynamic File Import Wizard
                        with ui.row().classes('w-full gap-4 items-center flex-wrap'):
                            import_db_select = ui.select(
                                options={},
                                value=None,
                                label='Target Database',
                                on_change=lambda e: update_import_schemas_for_wizard(e.value)
                            ).props('outlined dense').style('width: 240px;')
                            
                            import_schema_select = ui.select(
                                options={'main': 'main'},
                                value='main',
                                label='Target Schema'
                            ).props('outlined dense').style('width: 240px;')
                        
                        with ui.tabs().classes('w-full border-b') as wizard_tabs:
                            local_import_tab = ui.tab('Local File Upload', icon='upload_file')
                            url_import_tab = ui.tab('Remote HTTPS URL', icon='link')
                            s3_import_tab = ui.tab('Amazon S3 Bucket', icon='cloud')
                            
                        with ui.tab_panels(wizard_tabs, value=local_import_tab).classes('w-full bg-transparent min-h-0 flex-grow'):
                            # Tab 1: Local
                            with ui.tab_panel(local_import_tab).classes('gap-4 p-4 flex-col'):
                                ui.label('Upload a Parquet, CSV, or JSON file. The file will be uploaded to /shared and registered as a dynamic table.').classes('text-xs text-slate-400')
                                ui.upload(label='Drag & Drop or click to upload', on_upload=handle_local_file_upload, auto_upload=True).classes('w-full').props('outlined dense accept=".parquet,.csv,.json"')
                                
                            # Tab 2: URL
                            with ui.tab_panel(url_import_tab).classes('gap-4 p-4 flex-col'):
                                ui.label('Directly query and register dynamic tables from public HTTP/HTTPS URLs.').classes('text-xs text-slate-400')
                                url_input = ui.input('HTTPS Endpoint URL', placeholder='https://raw.githubusercontent.com/.../data.csv').props('outlined dense').classes('w-full')
                                with ui.row().classes('w-full gap-3 flex-wrap items-center'):
                                    url_fmt = ui.select(['Auto-Detect', 'CSV', 'Parquet', 'JSON'], value='Auto-Detect', label='Format').props('outlined dense').style('width: 160px;')
                                    url_table = ui.input('Target Table Name', placeholder='e.g. gdp_data').props('outlined dense').classes('flex-grow')
                                    ui.button('Load remote URL', icon='cloud_download',
                                              on_click=lambda: trigger_url_import(url_input.value, url_fmt.value, url_table.value)).props('elevated dense color=primary').classes('px-4 py-2')
                                              
                            # Tab 3: S3
                            with ui.tab_panel(s3_import_tab).classes('gap-4 p-4 flex-col'):
                                ui.label('Query tables directly from Amazon S3 buckets.').classes('text-xs text-slate-400')
                                s3_uri_input = ui.input('S3 URI', placeholder='s3://my-bucket/path/to/data.parquet').props('outlined dense').classes('w-full')
                                with ui.row().classes('w-full gap-3 flex-wrap items-center'):
                                    s3_fmt = ui.select(['Auto-Detect', 'CSV', 'Parquet', 'JSON'], value='Auto-Detect', label='Format').props('outlined dense').style('width: 160px;')
                                    s3_table = ui.input('Target Table Name', placeholder='e.g. s3_data').props('outlined dense').classes('flex-grow')
                                with ui.row().classes('w-full gap-3 flex-wrap items-center'):
                                    s3_key = ui.input('AWS Access Key ID').props('outlined dense password password-toggle-button').classes('flex-grow')
                                    s3_secret = ui.input('AWS Secret Access Key').props('outlined dense password password-toggle-button').classes('flex-grow')
                                with ui.row().classes('w-full gap-3 flex-wrap items-center'):
                                    s3_token = ui.input('AWS Session Token (Optional)').props('outlined dense').classes('flex-grow')
                                    s3_region_input = ui.input('AWS Region', value='us-east-1').props('outlined dense').style('width: 160px;')
                                ui.button('Load S3 Dataset', icon='play_arrow',
                                          on_click=lambda: trigger_s3_import(s3_uri_input.value, s3_fmt.value, s3_key.value, s3_secret.value, s3_token.value, s3_region_input.value, s3_table.value)).props('elevated dense color=primary').classes('self-end px-4 py-2 mt-2')

                # TAB 3: Schema Diff Tool
                with ui.tab_panel(schema_diff_tab).classes('gap-6 p-0 flex-col'):
                    with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4 flex-none'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('difference', color='primary').classes('text-2xl')
                            ui.label('Database Schema Drift & Diff Tool').classes('text-lg font-bold text-slate-800 dark:text-white')
                        ui.separator().classes('opacity-50')
                        ui.label('Compare column definitions, indexes, and constraints between two attached databases or tables to identify schemas mismatches or drift.').classes('text-xs text-slate-400 leading-relaxed max-w-3xl')
                        
                        # Selection columns
                        with ui.grid(columns=2).classes('w-full gap-6'):
                            # Left Column: Source Table
                            with ui.card().classes('p-4 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel flex-col gap-3'):
                                ui.label('Source / Reference Table').classes('text-xs font-bold text-slate-700 dark:text-slate-300 uppercase')
                                source_db_select = ui.select(
                                    options={},
                                    value=None,
                                    label='Database',
                                    on_change=lambda e: update_diff_tables(e.value, source_table_select)
                                ).props('dense outlined').classes('w-full')
                                source_table_select = ui.select(
                                    options={},
                                    value=None,
                                    label='Table / View'
                                ).props('dense outlined').classes('w-full')
                                
                            # Right Column: Target Table
                            with ui.card().classes('p-4 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel flex-col gap-3'):
                                ui.label('Target / Comparison Table').classes('text-xs font-bold text-slate-700 dark:text-slate-300 uppercase')
                                target_db_select = ui.select(
                                    options={},
                                    value=None,
                                    label='Database',
                                    on_change=lambda e: update_diff_tables(e.value, target_table_select)
                                ).props('dense outlined').classes('w-full')
                                target_table_select = ui.select(
                                    options={},
                                    value=None,
                                    label='Table / View'
                                ).props('dense outlined').classes('w-full')
                                
                        ui.button('Compare Schemas', icon='compare_arrows', color='primary', on_click=run_schema_diff).props('elevated dense').classes('self-end px-4 py-2 mt-2')
                        
                    # Diff Results display container
                    diff_results_card = ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-3 flex-grow overflow-auto')
                    with diff_results_card:
                        ui.label('Comparison Results').classes('font-bold text-slate-800 dark:text-white text-sm')
                        diff_results_container = ui.column().classes('w-full gap-1 flex-grow overflow-auto')
                        with diff_results_container:
                            ui.label('Select source and target tables, then click "Compare Schemas" to analyze differences.').classes('text-xs text-slate-400 italic')
        
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
                extensions_grid = ui.grid().classes('grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 w-full gap-4 mt-2 pb-6')
        
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
            with ui.row().classes('w-full items-center justify-between bg-slate-100 dark:bg-slate-800 p-2 border-b border-slate-200 dark:border-slate-700'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('lan', color='primary').classes('text-xl')
                    ui.label('dbt Project Workbench').classes('font-bold text-slate-700 dark:text-white')
                ui.button('Show Lineage DAG', icon='lan', on_click=open_dbt_lineage_dialog).props('elevated dense color=primary')
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
            with ui.grid().classes('grid grid-cols-1 md:grid-cols-2 gap-6 w-full flex-none'):
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
                        
                    # Split trailing clauses (GROUP BY, HAVING, ORDER BY, LIMIT, OFFSET)
                    sql, trailing = split_sql_trailing_clauses(sql, keywords=['GROUP BY', 'HAVING', 'ORDER BY', 'LIMIT', 'OFFSET'])
                    
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
                        
                    # Check if WHERE exists at the top level
                    has_where = has_top_level_where(sql)
                    
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
                dup = config_db.query_one("SELECT 1 FROM _duckdb_studio_api_endpoints WHERE path = ?", [endpoint_path])
                if dup:
                    ui.notify(f"Endpoint path '/api/{endpoint_path}' already exists. Please use a unique path.", type='negative')
                    return
                    
                rl_value = rate_limit.strip() if rate_limit and rate_limit.strip() else None
                
                config_db.execute("""
                    INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at, security_enabled, rate_limit)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """, [
                    str(uuid.uuid4()),
                    endpoint_path,
                    description.strip() if description else '',
                    sql_code.strip(),
                    datetime.datetime.now().isoformat(),
                    1 if security_enabled else 0,
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
                config_db.execute("DELETE FROM _duckdb_studio_api_endpoints WHERE id = ?", [endpoint_id])
                ui.notify(f"API Endpoint '/api/{endpoint_path}' deleted successfully.", type='success')
                refresh_api_endpoints_grid()
            except Exception as err:
                ui.notify(f"Failed to delete endpoint: {err}", type='negative')

        def refresh_api_endpoints_grid():
            sync_fastapi_dynamic_routes()
            api_endpoints_list_container.clear()
            
            # Query aggregate metrics for endpoints
            metrics_map = {}
            try:
                m_rows = config_db.query_all("""
                    SELECT 
                        endpoint_path,
                        COUNT(*) as total_calls,
                        AVG(latency_ms) as avg_latency,
                        MIN(latency_ms) as min_latency,
                        MAX(latency_ms) as max_latency,
                        SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as total_errors
                    FROM _duckdb_studio_api_metrics
                    GROUP BY endpoint_path;
                """)
                for row in m_rows:
                    m_path = row['endpoint_path']
                    m_calls = row['total_calls']
                    m_avg = row['avg_latency']
                    m_min = row['min_latency']
                    m_max = row['max_latency']
                    m_errs = row['total_errors']
                    metrics_map[m_path] = {
                        'calls': m_calls,
                        'avg': m_avg if m_avg else 0.0,
                        'min': m_min if m_min else 0.0,
                        'max': m_max if m_max else 0.0,
                        'errors': m_errs if m_errs else 0,
                        'error_rate': (m_errs * 100.0 / m_calls) if m_calls and m_calls > 0 else 0.0
                    }
            except Exception as e:
                print(f"DEBUG: Failed to query API metrics: {e}", flush=True)
            
            try:
                rows = [(r['id'], r['path'], r['description'], r['sql_code'], bool(r['security_enabled']), r['rate_limit']) for r in config_db.query_all("SELECT id, path, description, sql_code, security_enabled, rate_limit FROM _duckdb_studio_api_endpoints ORDER BY created_at DESC;")]
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
                        
                    # Split trailing clauses (GROUP BY, HAVING, ORDER BY, LIMIT, OFFSET)
                    sql, trailing = split_sql_trailing_clauses(sql, keywords=['GROUP BY', 'HAVING', 'ORDER BY', 'LIMIT', 'OFFSET'])
                    
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
                        
                    # Check if WHERE exists at the top level
                    has_where = has_top_level_where(sql)
                    
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
                dup = config_db.query_one("SELECT 1 FROM _duckdb_studio_api_endpoints WHERE path = ?", [endpoint_path])
                if dup:
                    ui.notify(f"Endpoint path '/api/{endpoint_path}' already exists. Please use a unique path.", type='negative')
                    return
                    
                rl_value = rate_limit.strip() if rate_limit and rate_limit.strip() else None
                
                config_db.execute("""
                    INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at, security_enabled, rate_limit)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """, [
                    str(uuid.uuid4()),
                    endpoint_path,
                    description.strip() if description else '',
                    sql_code.strip(),
                    datetime.datetime.now().isoformat(),
                    1 if security_enabled else 0,
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
                config_db.execute("DELETE FROM _duckdb_studio_api_endpoints WHERE id = ?", [endpoint_id])
                ui.notify(f"API Endpoint '/api/{endpoint_path}' deleted successfully.", type='success')
                refresh_api_endpoints_grid()
            except Exception as err:
                ui.notify(f"Failed to delete endpoint: {err}", type='negative')

        def refresh_api_endpoints_grid():
            sync_fastapi_dynamic_routes()
            api_endpoints_list_container.clear()
            
            # Query aggregate metrics for endpoints
            metrics_map = {}
            try:
                m_rows = config_db.query_all("""
                    SELECT 
                        endpoint_path,
                        COUNT(*) as total_calls,
                        AVG(latency_ms) as avg_latency,
                        MIN(latency_ms) as min_latency,
                        MAX(latency_ms) as max_latency,
                        SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as total_errors
                    FROM _duckdb_studio_api_metrics
                    GROUP BY endpoint_path;
                """)
                for row in m_rows:
                    m_path = row['endpoint_path']
                    m_calls = row['total_calls']
                    m_avg = row['avg_latency']
                    m_min = row['min_latency']
                    m_max = row['max_latency']
                    m_errs = row['total_errors']
                    metrics_map[m_path] = {
                        'calls': m_calls,
                        'avg': m_avg if m_avg else 0.0,
                        'min': m_min if m_min else 0.0,
                        'max': m_max if m_max else 0.0,
                        'errors': m_errs if m_errs else 0,
                        'error_rate': (m_errs * 100.0 / m_calls) if m_calls and m_calls > 0 else 0.0
                    }
            except Exception as e:
                print(f"DEBUG: Failed to query API metrics: {e}", flush=True)
            
            try:
                rows = [(r['id'], r['path'], r['description'], r['sql_code'], bool(r['security_enabled']), r['rate_limit']) for r in config_db.query_all("SELECT id, path, description, sql_code, security_enabled, rate_limit FROM _duckdb_studio_api_endpoints ORDER BY created_at DESC;")]
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
                    with ui.card().classes('w-full p-0 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl overflow-hidden'):
                        with ui.expansion().classes('w-full').props('header-class="q-py-sm q-px-md" expand-icon-class="text-slate-500"') as exp:
                            with exp.add_slot('header'):
                                with ui.row().classes('w-full items-center justify-between no-wrap pr-4'):
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
                            
                            with ui.column().classes('w-full p-4 gap-3 border-t border-slate-100 dark:border-slate-800'):
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
                    rows_val = config_db.query_all("SELECT id, path, description, sql_code, security_enabled FROM _duckdb_studio_api_endpoints ORDER BY created_at DESC;")
                    rows = [(r['id'], r['path'], r['description'], r['sql_code'], bool(r['security_enabled'])) for r in rows_val]
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
                        with ui.card().classes('w-full p-0 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl overflow-hidden'):
                            with ui.expansion().classes('w-full').props('header-class="q-py-xs q-px-md min-h-[36px]" expand-icon-class="text-slate-500"') as exp:
                                with exp.add_slot('header'):
                                    with ui.row().classes('w-full items-center justify-between no-wrap pr-4'):
                                        with ui.row().classes('items-center gap-2 no-wrap'):
                                            ui.badge('GET', color='positive').classes('text-xs font-bold px-2 py-0.5')
                                            if ep_secured:
                                                ui.icon('lock', color='amber').classes('text-sm').tooltip('Requires JWT Authorization')
                                            ui.label(f"/api/{ep_path}").classes('text-base font-bold text-slate-800 dark:text-white')
                                            if ep_secured:
                                                ui.badge('JWT SECURED', color='amber').classes('text-[9px] font-bold px-1.5 py-0.5')
                                        ui.label(ep_desc if ep_desc else 'No description provided').classes('text-xs text-slate-400 font-normal italic truncate max-w-[400px]')
                                
                                # Compact body with minimal padding and gap to remove unused space
                                with ui.column().classes('w-full pt-1 pb-3 px-4 gap-2 border-t border-slate-100 dark:border-slate-800'):
                                    import re
                                    placeholders = re.findall(r'\$([a-zA-Z0-9_]+)', ep_sql)
                                    placeholders = list(dict.fromkeys(placeholders))
                                    placeholders = [p for p in placeholders if p.lower() not in ['limit', 'offset']]
                                    
                                    input_fields = {}
                                    
                                    # Parameter container limited to exactly 2 rows of height (~95px), scrolls beyond
                                    with ui.column().classes('w-full max-h-[95px] overflow-y-auto pr-1 gap-1.5 flex-nowrap'):
                                        with ui.row().classes('w-full gap-x-3 gap-y-1.5 items-start flex-wrap'):
                                            # Authorization Token for Secured APIs
                                            if ep_secured:
                                                with ui.column().classes('gap-0.5').style('width: 180px;'):
                                                    ui.label('Authorization Token').classes('text-[10px] font-bold text-amber-500 dark:text-amber-400')
                                                    input_fields['__jwt__'] = ui.input(placeholder='Bearer <token>').props('outlined dense').classes('w-full super-dense-input font-mono text-[10px]')
                                                    
                                            # Standard Paging parameters
                                            with ui.column().classes('gap-0.5').style('width: 90px;'):
                                                ui.label('limit').classes('text-[10px] font-bold text-slate-700 dark:text-slate-300')
                                                input_fields['limit'] = ui.input(placeholder='e.g., 100').props('outlined dense type=number').classes('w-full super-dense-input text-[10px]')
                                                
                                            with ui.column().classes('gap-0.5').style('width: 90px;'):
                                                ui.label('offset').classes('text-[10px] font-bold text-slate-700 dark:text-slate-300')
                                                input_fields['offset'] = ui.input(placeholder='e.g., 0').props('outlined dense type=number').classes('w-full super-dense-input text-[10px]')
                                                
                                            # Custom placeholders
                                            for p in placeholders:
                                                with ui.column().classes('gap-0.5').style('width: 130px;'):
                                                    ui.label(p).classes('text-[10px] font-bold text-indigo-500 dark:text-indigo-400')
                                                    input_fields[p] = ui.input(placeholder=f'Value for ${p}').props('outlined dense').classes('w-full super-dense-input text-[10px]')
                                                    
                                    # Action Row
                                    with ui.row().classes('w-full justify-end gap-2 pt-1'):
                                        clear_btn = ui.button('Clear', color='grey').props('flat size=sm')
                                        execute_btn = ui.button('Execute Request', icon='bolt', color='primary').props('elevated size=sm').classes('px-4')
                                        
                                    # Response Container
                                    response_panel = ui.column().classes('w-full gap-3 mt-2 border border-slate-100 dark:border-slate-800 rounded-xl p-3 bg-slate-50 dark:bg-slate-950').style('display: none;')
                                    
                                    with response_panel:
                                        with ui.row().classes('w-full justify-between items-center no-wrap'):
                                            with ui.row().classes('items-center gap-2'):
                                                status_badge = ui.badge('', color='positive').classes('text-xs font-bold px-2 py-0.5')
                                                latency_label = ui.label('').classes('text-xs font-semibold text-slate-500')
                                            url_label = ui.label('').classes('text-[10px] font-mono text-slate-400 truncate max-w-[400px] cursor-pointer').tooltip('Click to copy relative API URL')
                                            
                                        ui.separator().classes('opacity-30')
                                        response_code_block_wrapper = ui.column().classes('w-full overflow-auto max-h-[200px]')
                                        
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
                                            target_url = f"/api/{path}"
                                            query_str = "&".join([f"{k}={v}" for k, v in params.items()])
                                            full_url_display = f"{target_url}?{query_str}" if query_str else target_url
                                            
                                            try:
                                                async with httpx.AsyncClient() as client:
                                                    response = await client.get(f"http://127.0.0.1:8085/api/{path}", params=params, headers=headers, timeout=5.0)
                                                latency = int((time.perf_counter() - start_time) * 1000)
                                                status = response.status_code
                                                
                                                s_badge.text = f"HTTP {status}"
                                                if status == 200:
                                                    s_badge.color = "positive"
                                                else:
                                                    s_badge.color = "negative"
                                                    
                                                lat_lbl.text = f"Latency: {latency} ms"
                                                u_lbl.text = f"GET {full_url_display}"
                                                
                                                def make_copy_url_callback(u=full_url_display):
                                                    return lambda _: [
                                                        ui.run_javascript(f"navigator.clipboard.writeText(window.location.origin + '{u}')"),
                                                        ui.notify('Full API URL copied to clipboard!', type='positive')
                                                    ]
                                                u_lbl.on('click', make_copy_url_callback())
                                                
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
            pass
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
            with ui.grid().classes('grid grid-cols-1 md:grid-cols-2 gap-6 w-full flex-none'):
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
                config_db.execute("DELETE FROM _duckdb_studio_scheduler_logs;")
                ui.notify('Scheduled query logs cleared successfully!', type='positive')
                refresh_scheduler_logs_table()
            except Exception as ex:
                ui.notify(f"Failed to clear logs: {ex}", type='negative')

        def refresh_scheduler_jobs_list():
            scheduler_jobs_list_container.clear()
            try:
                rows = [(
                    r['id'], r['name'], r['sql_code'], r['interval_str'], r['export_format'],
                    r['partition_column'], r['export_filename'], 
                    datetime.fromisoformat(r['last_run']) if r['last_run'] else None,
                    datetime.fromisoformat(r['next_run']) if r['next_run'] else None,
                    r['status'], r['error_message']
                ) for r in config_db.query_all("SELECT id, name, sql_code, interval_str, export_format, partition_column, export_filename, last_run, next_run, status, error_message FROM _duckdb_studio_scheduled_jobs ORDER BY name ASC;")]
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
                                            config_db.execute("UPDATE _duckdb_studio_scheduled_jobs SET status = ? WHERE id = ?;", [new_status, job_id])
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
                                            config_db.execute("UPDATE _duckdb_studio_scheduled_jobs SET last_run = ? WHERE id = ?;", [now.isoformat(), job_id])
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
                                            config_db.execute("DELETE FROM _duckdb_studio_scheduled_jobs WHERE id = ?;", [job_id])
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
                logs = [(
                    r['job_name'],
                    datetime.fromisoformat(r['executed_at']) if r['executed_at'] else datetime.now(),
                    r['duration_ms'], r['row_count'], r['file_size_bytes'], r['status'], r['error_message']
                ) for r in config_db.query_all("SELECT job_name, executed_at, duration_ms, row_count, file_size_bytes, status, error_message FROM _duckdb_studio_scheduler_logs ORDER BY executed_at DESC LIMIT 50;")]
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
                with ui.element('div').classes('w-full overflow-x-auto border border-slate-100 dark:border-slate-800 rounded-lg bg-white dark:bg-slate-950'):
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

        # Build Garage S3 Container Content
        with garage_container:
            ui.element('iframe').props('id="garage-webui-frame" sandbox="allow-scripts allow-same-origin allow-popups allow-forms allow-downloads allow-modals"').classes('w-full h-full border-none')
            ui.run_javascript('''
                (function() {
                    var host = window.location.hostname;
                    var port = window.location.port;
                    var proto = window.location.protocol;
                    var targetUrl;
                    if (host.endsWith('.localhost')) {
                        var baseDomain = host.substring(host.indexOf('.'));
                        targetUrl = proto + '//garage' + baseDomain + (port ? ':' + port : '');
                    } else {
                        targetUrl = proto + '//' + host + ':3909';
                    }
                    document.getElementById("garage-webui-frame").src = targetUrl;
                })();
            ''')

        # Build Apache Superset Container Content
        with superset_container.classes('p-6 overflow-auto bg-slate-50 dark:bg-slate-900 gap-6 flex-nowrap'):
            # Header Card
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('items-center gap-4'):
                    # Large Superset logo (white and green)
                    ui.html('''<svg viewBox="31 35 150 77" version="1.1" xmlns="http://www.w3.org/2000/svg" width="48" height="48" class="flex-shrink-0">
                        <path d="M141.56,37.83C129.1,37.83 117.56,44.83 106.56,57.08C95.62,44.64 83.94,37.83 70.89,37.83C49.29,37.83 33.52,53.19 33.52,74C33.52,94.81 49.29,110 70.89,110C84.13,110 94.45,103.77 105.89,91.33C117,103.74 128.32,110 141.56,110C163.17,110 178.93,94.83 178.93,74C178.93,53.17 163.17,37.83 141.56,37.83ZM71,88.19C61.85,88.19 56.4,82.19 56.4,74.19C56.4,66.19 61.89,60 71,60C78.78,60 85,66.22 91.82,74.58C85.44,82.36 78.63,88.19 71,88.19ZM140.88,88.19C133.29,88.19 126.88,82.19 120.05,74.19C127.05,65.83 133.05,60 140.88,60C150.03,60 155.48,66.22 155.48,74.19C155.48,82.16 150.07,88.19 140.92,88.19L140.88,88.19Z" fill="#10B981"/>
                        <path d="M122.21,104.88L136.74,87.57C130.9,85.85 125.61,80.64 120.09,74.19L105.93,91.3C110.555,96.709 116.059,101.301 122.21,104.88Z" fill="#20A7C9" class="dark:fill-white"/>
                        <path d="M106.52,57.08C101.915,51.629 96.45,46.967 90.34,43.28L75.8,60.81C81.33,62.69 86.23,67.71 91.43,74.05L92,74.45C92,74.45 106.7,56.88 106.52,57.08Z" fill="#20A7C9" class="dark:fill-white"/>
                    </svg>''', sanitize=False)
                    with ui.column().classes('gap-0.5'):
                        ui.label('Apache Superset BI Reporting').classes('text-2xl font-black text-slate-800 dark:text-white')
                        ui.label('Enterprise-ready business intelligence web application for data exploration and visualization.').classes('text-sm text-slate-500 dark:text-slate-400')

            # Launch Card
            with ui.card().classes('w-full p-8 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col items-center justify-center gap-6 text-center'):
                ui.icon('bar_chart', size='64px', color='primary')
                with ui.column().classes('gap-2 items-center'):
                    ui.label('Launch Apache Superset Workspace').classes('text-xl font-bold text-slate-800 dark:text-white')
                    ui.label('Open Apache Superset in a secure, first-party browser tab. This bypasses all sandbox restrictions and resolves cookie blocking.').classes('text-sm text-slate-500 dark:text-slate-400 max-w-lg')
                
                # Active DB connection & Credentials info cards
                with ui.row().classes('gap-6 justify-center w-full max-w-2xl py-4'):
                    with ui.card().classes('p-4 border border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 rounded-lg flex-1 text-left'):
                        ui.label('🔑 Default Credentials').classes('text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2')
                        ui.label('Username: admin').classes('text-sm font-mono font-bold text-slate-700 dark:text-slate-300')
                        ui.label('Password: admin').classes('text-sm font-mono font-bold text-slate-700 dark:text-slate-300')
                    with ui.card().classes('p-4 border border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 rounded-lg flex-1 text-left'):
                        ui.label('🔌 Seeded Connection').classes('text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2')
                        ui.label('Database Name: DuckDB_PGWire').classes('text-sm font-bold text-slate-700 dark:text-slate-300')
                        ui.label('Ready for immediate SQL querying inside SQL Lab.').classes('text-xs text-slate-500 mt-1')

                def open_superset_new_window():
                    ui.run_javascript('''
                        (function() {
                            var host = window.location.hostname;
                            var port = window.location.port;
                            var proto = window.location.protocol;
                            var targetUrl;
                            if (host.endsWith('.localhost')) {
                                var baseDomain = host.substring(host.indexOf('.'));
                                targetUrl = proto + '//superset' + baseDomain + (port ? ':' + port : '') + '/login/?auto_login=true';
                            } else {
                                targetUrl = proto + '//' + host + ':8088/login/?auto_login=true';
                            }
                            window.open(targetUrl, '_blank');
                        })();
                    ''')
                ui.button('Open Superset Workspace', icon='launch', on_click=open_superset_new_window).props('size=lg elevated color=primary').classes('px-8 py-3 text-base font-bold rounded-xl shadow-lg hover:scale-105 transition-transform')

        # Build Telemetry Container Content
        with telemetry_container:
            # Header Card
            with ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-none'):
                with ui.row().classes('items-center justify-between w-full'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('insights', color='primary').classes('text-3xl')
                        ui.label('Telemetry & Observability Hub').classes('text-2xl font-black text-slate-800 dark:text-white')
                    
                    # Reset / Clear metrics button
                    def handle_clear_telemetry():
                        try:
                            config_db.execute("DELETE FROM _duckdb_studio_api_metrics;")
                            ui.notify('Telemetry logs cleared successfully!', type='positive')
                            update_telemetry_dashboard()
                        except Exception as ex:
                            ui.notify(f"Failed to clear telemetry: {ex}", type='negative')
                    
                    ui.button('Clear Telemetry Logs', icon='cleaning_services', on_click=handle_clear_telemetry).props('outline dense color=negative size=sm').classes('px-3 text-xs')
                ui.label('Real-time diagnostics of DuckDB database engine execution, memory allocation profiles, and compiled FastAPI endpoint traffic.').classes('text-sm text-slate-500 dark:text-slate-400')
            
            # Grid layout for Metrics (3 columns, room for growth)
            with ui.grid().classes('grid grid-cols-1 md:grid-cols-3 gap-4 w-full flex-none'):
                # Card 1: Active Routes
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('dns', color='primary').classes('text-xl p-1.5 bg-indigo-50 dark:bg-indigo-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('Active API Routes').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        api_routes_lbl = ui.label('0').classes('text-base font-black text-slate-700 dark:text-slate-200')

                # Card 2: Total Calls
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('call', color='secondary').classes('text-xl p-1.5 bg-purple-50 dark:bg-purple-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('Total API Requests').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        api_calls_lbl = ui.label('0').classes('text-base font-black text-slate-700 dark:text-slate-200')

                # Card 3: Avg Latency
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('speed', color='warning').classes('text-xl p-1.5 bg-amber-50 dark:bg-amber-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('Avg API Latency').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        api_latency_lbl = ui.label('0.0 ms').classes('text-base font-black text-slate-700 dark:text-slate-200')

                # Card 4: Success Rate
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('health_and_safety', color='positive').classes('text-xl p-1.5 bg-emerald-50 dark:bg-emerald-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('API Success Rate').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        api_success_lbl = ui.label('100.0%').classes('text-base font-black text-slate-700 dark:text-slate-200')

                # Card 5: System CPU
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('insights', color='primary').classes('text-xl p-1.5 bg-blue-50 dark:bg-blue-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('System CPU Load').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        cpu_lbl = ui.label('0.0%').classes('text-base font-black text-slate-700 dark:text-slate-200')

                # Card 6: System Memory
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('memory', color='secondary').classes('text-xl p-1.5 bg-pink-50 dark:bg-pink-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('System RAM Usage').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        sys_ram_lbl = ui.label('0.0 / 0.0 GB').classes('text-base font-black text-slate-700 dark:text-slate-200')

                # Card 7: DuckDB Memory Load
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('analytics', color='emerald').classes('text-xl p-1.5 bg-teal-50 dark:bg-teal-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('DuckDB Memory Load').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        db_mem_pct_lbl = ui.label('0.0%').classes('text-base font-black text-slate-700 dark:text-slate-200')

                # Card 8: DuckDB Memory Usage
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('save', color='warning').classes('text-xl p-1.5 bg-amber-50 dark:bg-amber-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('DuckDB Memory Usage').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        db_mem_lbl = ui.label('0.0 MB / 0.0 GB').classes('text-base font-black text-slate-700 dark:text-slate-200')

                # Card 9: Active Threads
                with ui.card().classes('p-2.5 h-16 border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel flex-row items-center gap-3'):
                    ui.icon('lan', color='primary').classes('text-xl p-1.5 bg-sky-50 dark:bg-sky-950/50 rounded-lg')
                    with ui.column().classes('gap-0'):
                        ui.label('Active Engine Threads').classes('text-[10px] text-slate-400 font-bold uppercase tracking-wider')
                        threads_lbl = ui.label('0').classes('text-base font-black text-slate-700 dark:text-slate-200')
            
            # Interactive Charts Grid
            with ui.grid().classes('grid grid-cols-1 md:grid-cols-2 gap-6 mt-2 w-full flex-none'):
                import collections
                mem_history_list = collections.deque(maxlen=15)
                mem_time_list = collections.deque(maxlen=15)
                
                mem_chart_options = {
                    'title': {'text': 'DuckDB Memory Footprint (MB)', 'left': 'center', 'textStyle': {'fontSize': 12}},
                    'grid': {'top': 40, 'bottom': 25, 'left': 45, 'right': 15},
                    'tooltip': {'trigger': 'axis'},
                    'xAxis': {'type': 'category', 'data': []},
                    'yAxis': {'type': 'value'},
                    'series': [{'name': 'Active Usage', 'type': 'line', 'data': [], 'smooth': True, 'areaStyle': {}, 'color': '#6366f1'}]
                }
                latency_chart_options = {
                    'title': {'text': 'Execution Latency History (ms)', 'left': 'center', 'textStyle': {'fontSize': 12}},
                    'grid': {'top': 40, 'bottom': 25, 'left': 45, 'right': 15},
                    'tooltip': {'trigger': 'axis'},
                    'xAxis': {'type': 'category', 'data': []},
                    'yAxis': {'type': 'value'},
                    'series': [{'name': 'Query Time', 'type': 'bar', 'data': [], 'color': '#10b981'}]
                }
                
                mem_chart = ui.echart(mem_chart_options).classes('w-full border rounded-xl p-2 bg-white dark:bg-slate-950').style('height: 180px;')
                latency_chart = ui.echart(latency_chart_options).classes('w-full border rounded-xl p-2 bg-white dark:bg-slate-950').style('height: 180px;')
            
            # Performance details analytics card
            analytics_card = ui.card().classes('w-full p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4 flex-none')
            with analytics_card:
                ui.label('Endpoint Performance Analytics').classes('text-lg font-bold text-slate-800 dark:text-white')
                ui.separator().classes('opacity-50')
                table_container = ui.column().classes('w-full')
                
            last_detail_rows = None
            def update_telemetry_dashboard():
                nonlocal last_detail_rows
                if tabs.value != 'Telemetry':
                    return
                
                # Update Engine metrics
                cpu_val = get_cpu_load()
                cpu_lbl.text = f"{cpu_val:.1f}%"
                
                used_ram, total_ram = get_system_memory()
                sys_ram_lbl.text = f"{used_ram / (1024**3):.1f} / {total_ram / (1024**3):.1f} GB"
                
                try:
                    mem_rows = explorer.conn.execute("SELECT SUM(memory_usage_bytes) FROM duckdb_memory()").fetchone()
                    mem_bytes = mem_rows[0] or 0
                    mem_mb = round(mem_bytes / (1024 * 1024), 2)
                    
                    limit_rows = explorer.conn.execute("SELECT value FROM duckdb_settings() WHERE name = 'max_memory'").fetchone()
                    limit_val = limit_rows[0] if limit_rows else None
                    
                    import re
                    limit_bytes = 0
                    if limit_val:
                        num = re.findall(r"[-+]?\d*\.\d+|\d+", limit_val)
                        if num:
                            val_num = float(num[0])
                            if 'gib' in limit_val.lower():
                                limit_bytes = val_num * 1024 * 1024 * 1024
                            elif 'mib' in limit_val.lower():
                                limit_bytes = val_num * 1024 * 1024
                            elif 'kb' in limit_val.lower():
                                limit_bytes = val_num * 1024
                            else:
                                limit_bytes = val_num
                    
                    if limit_bytes == 0:
                        limit_bytes = total_ram
                        
                    mem_pct = round((mem_bytes / limit_bytes) * 100, 1) if limit_bytes else 0.0
                    
                    db_mem_lbl.text = f"{mem_mb:.1f} MB / {limit_bytes / (1024**3):.1f} GB"
                    db_mem_pct_lbl.text = f"{mem_pct}%"
                    
                    threads_rows = explorer.conn.execute("SELECT value FROM duckdb_settings() WHERE name = 'threads'").fetchone()
                    threads_lbl.text = str(threads_rows[0]) if threads_rows else 'N/A'
                    
                    # Log active memory usage history
                    import datetime
                    mem_history_list.append(mem_mb)
                    mem_time_list.append(datetime.datetime.now().strftime('%H:%M:%S'))
                    
                    mem_chart.options['xAxis']['data'] = list(mem_time_list)
                    mem_chart.options['series'][0]['data'] = list(mem_history_list)
                    mem_chart.update()
                except Exception as ex:
                    print(f"Telemetry engine query error: {ex}")
                
                # Update latency chart
                try:
                    lat_data = [x['latency'] for x in query_latency_history]
                    lat_labels = [x['timestamp'] for x in query_latency_history]
                    
                    latency_chart.options['xAxis']['data'] = list(lat_labels)
                    latency_chart.options['series'][0]['data'] = list(lat_data)
                    latency_chart.update()
                except Exception as ex:
                    print(f"Telemetry engine latency chart error: {ex}")
                
                # Update API metrics
                try:
                    res_val = config_db.query_one("""
                        SELECT 
                            COUNT(*) as total_calls,
                            AVG(m.latency_ms) as avg_lat,
                            SUM(CASE WHEN m.status_code < 400 THEN 1 ELSE 0 END) as success_calls
                        FROM _duckdb_studio_api_metrics m
                        INNER JOIN _duckdb_studio_api_endpoints e ON m.endpoint_path = e.path;
                    """)
                    
                    total_calls = res_val['total_calls'] if res_val and res_val['total_calls'] is not None else 0
                    avg_latency = res_val['avg_lat'] if res_val and res_val['avg_lat'] is not None else 0.0
                    success_count = res_val['success_calls'] if res_val and res_val['success_calls'] is not None else 0
                    success_rate = (success_count * 100.0 / total_calls) if total_calls > 0 else 100.0
                    
                    res_routes = config_db.query_one("SELECT COUNT(*) as cnt FROM _duckdb_studio_api_endpoints;")
                    active_routes = res_routes['cnt'] if res_routes else 0
                    
                    api_calls_lbl.text = str(total_calls)
                    api_latency_lbl.text = f"{avg_latency:.1f} ms"
                    api_success_lbl.text = f"{success_rate:.1f}%"
                    api_routes_lbl.text = str(active_routes)
                except Exception as ex:
                    print(f"Telemetry API metrics query error: {ex}")
                    
                # Update performance table
                try:
                    rows_val = config_db.query_all("""
                        SELECT 
                            m.endpoint_path,
                            COUNT(*) as calls,
                            AVG(m.latency_ms) as avg_lat,
                            MIN(m.latency_ms) as min_lat,
                            MAX(m.latency_ms) as max_lat,
                            SUM(CASE WHEN m.status_code < 400 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate,
                            MAX(m.timestamp) as last_called
                        FROM _duckdb_studio_api_metrics m
                        INNER JOIN _duckdb_studio_api_endpoints e ON m.endpoint_path = e.path
                        GROUP BY m.endpoint_path
                        ORDER BY calls DESC;
                    """)
                    detail_rows = [(r['endpoint_path'], r['calls'], r['avg_lat'], r['min_lat'], r['max_lat'], r['success_rate'], r['last_called']) for r in rows_val]
                except Exception as ex:
                    print(f"Failed to query detail api rows: {ex}")
                    detail_rows = []
                    
                if detail_rows != last_detail_rows:
                    last_detail_rows = detail_rows
                    table_container.clear()
                    with table_container:
                        if not detail_rows:
                            with ui.column().classes('w-full items-center justify-center py-8 border border-dashed border-slate-200 dark:border-slate-800 rounded-xl bg-slate-50/50 dark:bg-slate-900/20'):
                                ui.icon('hourglass_empty', color='grey').classes('text-3xl')
                                ui.label('No performance data logged yet.').classes('text-xs text-slate-400 font-medium mt-1')
                                ui.label('Hit your exposed API endpoints to see live stats populate here in real-time.').classes('text-[10px] text-slate-500')
                        else:
                            with ui.element('div').classes('w-full max-h-80 overflow-auto border border-slate-100 dark:border-slate-800 rounded-lg bg-white dark:bg-slate-950'):
                                with ui.element('table').classes('w-full text-left border-collapse text-xs'):
                                    with ui.element('thead').classes('sticky top-0 bg-slate-100 dark:bg-slate-900 text-slate-500 font-bold uppercase tracking-wider text-[10px] z-10'):
                                        with ui.element('tr'):
                                            ui.element('th').classes('p-3').text('Endpoint Route')
                                            ui.element('th').classes('p-3 text-center').text('Invocations')
                                            ui.element('th').classes('p-3 text-center').text('Avg Latency')
                                            ui.element('th').classes('p-3 text-center').text('Min / Max')
                                            ui.element('th').classes('p-3 text-center').text('Success Ratio')
                                            ui.element('th').classes('p-3 text-right').text('Last Triggered')
                                            
                                    with ui.element('tbody').classes('divide-y divide-slate-100 dark:divide-slate-800'):
                                        for path, calls, avg_lat, min_lat, max_lat, success_rate, last_called in detail_rows:
                                            with ui.element('tr').classes('hover:bg-slate-50/50 dark:hover:bg-slate-900/10'):
                                                ui.element('td').classes('p-3 font-semibold text-slate-700 dark:text-slate-350').text(f"/api/{path}")
                                                ui.element('td').classes('p-3 text-center').text(str(calls))
                                                ui.element('td').classes('p-3 text-center').text(f"{avg_lat:.1f}ms")
                                                ui.element('td').classes('p-3 text-center text-slate-400').text(f"{min_lat:.1f} / {max_lat:.1f}ms")
                                                color_ind = 'text-emerald-500' if success_rate >= 95 else ('text-amber-500' if success_rate >= 80 else 'text-rose-500')
                                                ui.element('td').classes(f'p-3 text-center font-bold {color_ind}').text(f"{success_rate:.1f}%")
                                                ui.element('td').classes('p-3 text-right text-slate-400').text(str(last_called)[:19] if last_called else 'N/A')
            
            ui.timer(2.0, update_telemetry_dashboard)

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
            with ui.grid().classes('grid grid-cols-1 md:grid-cols-2 gap-6 w-full flex-none'):
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

                # Card 5: AI Copilot Configuration
                with ui.card().classes('p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark-bg-panel rounded-xl flex-col gap-4'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('auto_awesome', color='primary').classes('text-2xl')
                        ui.label('AI Copilot Configuration').classes('text-lg font-bold text-slate-800 dark:text-white')
                    ui.separator().classes('opacity-50')
                    
                    settings_ai_provider = ui.select(
                        options={'none': 'Disabled / None', 'openai': 'OpenAI', 'anthropic': 'Anthropic', 'ollama': 'Ollama (Local)', 'custom': 'Custom OpenAI-Compatible'}, 
                        value=APP_SETTINGS.get('ai_provider', 'none'), 
                        label='AI Provider'
                    ).props('outlined dense').classes('w-full')
                    
                    settings_ai_api_key = ui.input(
                        'API Key', 
                        value=APP_SETTINGS.get('ai_api_key', ''), 
                        password=True, 
                        password_toggle_button=True
                    ).props('outlined dense').classes('w-full').tooltip('API key for OpenAI, Anthropic, or custom providers.')
                    
                    settings_ai_model = ui.input(
                        'Model Name', 
                        value=APP_SETTINGS.get('ai_model', 'gpt-4o')
                    ).props('outlined dense').classes('w-full').tooltip('The model ID (e.g. gpt-4o, claude-3-5-sonnet, or local ollama model name).')
                    
                    settings_ai_base_url = ui.input(
                        'Base URL', 
                        value=APP_SETTINGS.get('ai_base_url', '')
                    ).props('outlined dense').classes('w-full').tooltip('Custom API gateway Base URL (e.g., http://localhost:11434/v1 for Ollama).')

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
                        "jwt_audience": settings_jwt_audience.value.strip() if settings_jwt_audience.value else "duckdb_studio_clients",
                        "ai_provider": settings_ai_provider.value,
                        "ai_api_key": settings_ai_api_key.value.strip() if settings_ai_api_key.value else "",
                        "ai_model": settings_ai_model.value.strip() if settings_ai_model.value else "gpt-4o",
                        "ai_base_url": settings_ai_base_url.value.strip() if settings_ai_base_url.value else ""
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
        # dbt_workbench_container.bind_visibility_from(tabs, 'value', value='dbt Workbench')
        code_editor_container.bind_visibility_from(tabs, 'value', value='Code Editor')
        extensions_container.bind_visibility_from(tabs, 'value', value='Extensions')
        db_tools_container.bind_visibility_from(tabs, 'value', value='Database Tools')
        api_creator_container.bind_visibility_from(tabs, 'value', value='API Endpoints')
        api_docs_container.bind_visibility_from(tabs, 'value', value='API Docs & Explorer')
        scheduler_container.bind_visibility_from(tabs, 'value', value='Scheduler')
        garage_container.bind_visibility_from(tabs, 'value', value='Garage S3')
        telemetry_container.bind_visibility_from(tabs, 'value', value='Telemetry')
        superset_container.bind_visibility_from(tabs, 'value', value='Apache Superset')
        settings_container.bind_visibility_from(tabs, 'value', value='Settings')
        
    # Main split layout container
    with studio_container:
        
        # Configure Splitter to partition Left Sidebar and Right Content
        with ui.splitter(value=20).classes('w-full h-full') as main_splitter:
            
            # --- LEFT SIDEBAR (DATABASE METADATA & HISTORY) ---
            with main_splitter.before:
                with ui.column().classes('w-full h-full p-4 sidebar-card q-pa-md gap-4 flex-nowrap overflow-hidden').style('background-color: var(--q-slate-50);'):
                    
                    # Branding Header & Connection Info grouped with smaller gap
                    with ui.column().classes('w-full gap-2 flex-nowrap'):
                        # Branding Header
                        with ui.row().classes('items-center w-full justify-between no-wrap'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('schema', color='primary').classes('text-xl')
                                ui.label('Schema Explorer').classes('text-sm font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400')
                            
                            # Refresh schema button
                            ui.button(icon='refresh', on_click=lambda: refresh_schema_tree()).props('flat dense round size=sm').classes('text-slate-600')
                        
                        ui.separator()
                        
                        # Active DB File Indicator
                        with ui.card().classes('w-full p-2.5 glass-card border-none shadow-none dark-bg-flat'):
                            with ui.column().classes('w-full gap-1.5'):
                                with ui.row().classes('items-center justify-between w-full no-wrap'):
                                    with ui.row().classes('items-center gap-2 no-wrap'):
                                        ui.icon('folder_open', color='secondary').classes('text-lg')
                                        ui.label('Database Connection').classes('text-xs text-slate-500 font-semibold uppercase')
                                    ui.button(icon='add', on_click=lambda: attach_db_dialog.open()).props('flat dense round size=sm').classes('text-slate-600').tooltip('Attach external database')
                                
                                ui.separator().classes('my-0.5 opacity-50')
                                
                                # Container for attached databases
                                databases_container = ui.column().classes('w-full gap-1 pl-1')
                    
                    # Scrollable container for the expansion panels to prevent pushing actions out of frame
                    with ui.column().classes('w-full flex-grow overflow-y-auto gap-4 pr-1 flex-nowrap'):
                        # Database Schema Explorer & Saved Queries Library
                        with ui.expansion('🌳 Schema Browser', icon='account_tree', value=True).classes('w-full border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel text-xs text-slate-700 dark:text-slate-300 font-bold').props('header-class="q-py-xs q-px-sm min-h-[28px]"'):
                            with ui.column().classes('w-full gap-1.5 pt-1 px-2 pb-2'):
                                schema_filter_input = ui.input(placeholder='Filter tables, views, columns...', on_change=lambda _: refresh_schema_tree()).props('outlined dense clearable').classes('w-full font-normal text-xs').style('font-size: 11px;')
                                schema_container = ui.column().classes('w-full overflow-auto gap-0 text-slate-800 dark:text-slate-100').style('max-height: 340px;')

                        with ui.expansion('💾 SQL Snippets Library', icon='bookmark', value=True).classes('w-full border border-slate-200 dark:border-slate-800 rounded-lg dark-bg-panel text-xs text-slate-700 dark:text-slate-300 font-bold').props('header-class="q-py-xs q-px-sm min-h-[28px]"'):
                            with ui.column().classes('w-full gap-1.5 pt-1 px-2 pb-2'):
                                saved_queries_filter = ui.input(placeholder='Filter snippets...', on_change=lambda _: refresh_saved_queries_list()).props('outlined dense clearable').classes('w-full font-normal text-xs').style('font-size: 11px;')
                                def select_category(cat):
                                    nonlocal current_snippet_category
                                    current_snippet_category = cat
                                    all_btn.props('unelevated', remove='flat') if cat == 'All' else all_btn.props('flat', remove='unelevated')
                                    analytics_btn.props('unelevated', remove='flat') if cat == 'Analytical' else analytics_btn.props('flat', remove='unelevated')
                                    utility_btn.props('unelevated', remove='flat') if cat == 'Utility' else utility_btn.props('flat', remove='unelevated')
                                    ddl_btn.props('unelevated', remove='flat') if cat == 'DDL/DML' else ddl_btn.props('flat', remove='unelevated')
                                    all_btn.update()
                                    analytics_btn.update()
                                    utility_btn.update()
                                    ddl_btn.update()
                                    asyncio.get_event_loop().call_soon(refresh_saved_queries_list)

                                with ui.row().classes('w-full gap-1 justify-between flex-wrap'):
                                    all_btn = ui.button('ALL', on_click=lambda: select_category('All')).props('unelevated dense size=xs color=primary').classes('font-bold px-1 flex-grow').style('font-size: 12px !important;')
                                    analytics_btn = ui.button('ANALYTICS', on_click=lambda: select_category('Analytical')).props('flat dense size=xs color=primary').classes('font-bold px-1 flex-grow').style('font-size: 12px !important;')
                                    utility_btn = ui.button('UTILITY', on_click=lambda: select_category('Utility')).props('flat dense size=xs color=primary').classes('font-bold px-1 flex-grow').style('font-size: 12px !important;')
                                    ddl_btn = ui.button('DDL/DML', on_click=lambda: select_category('DDL/DML')).props('flat dense size=xs color=primary').classes('font-bold px-1 flex-grow').style('font-size: 12px !important;')
                                saved_queries_container = ui.column().classes('w-full overflow-auto gap-2 text-slate-800 dark:text-slate-100').style('max-height: 260px;')
                    


            # --- RIGHT WORKSPACE (SQL EDITOR, ACTIONS, GRAPHICS, GRID) ---
            with main_splitter.after:
                with ui.column().classes('w-full h-full p-3 gap-2 flex-nowrap overflow-hidden'):
                    
                    # Top Workspace Bar
                    with ui.row().classes('w-full items-center justify-between no-wrap'):
                        with ui.column().classes('gap-0'):
                            ui.label('SQL Workspace').classes('text-lg font-extrabold text-slate-800 dark:text-white')
                            ui.label('Write queries, inspect results, and plot live analytics dashboards').classes('text-xs text-slate-500')
                        
                        # Mode switches
                        with ui.row().classes('items-center gap-3'):
                            # AI Assistant toggle button
                            ai_toggle_btn = ui.button('AI Assistant', icon='auto_awesome', on_click=lambda: toggle_ai_panel()).props('flat dense size=sm color=primary').classes('font-bold px-2')
                            
                            ui.dark_mode().bind_value(app.storage.user, 'dark_mode')
                            ui.icon('light_mode', color='amber').classes('text-lg')
                            
                            def toggle_theme(e):
                                sql_editor.theme = 'monokai' if e.value else 'basicLight'
                                sql_editor.update()
                                
                            ui.switch(value=app.storage.user.get('dark_mode', False), on_change=toggle_theme).bind_value(app.storage.user, 'dark_mode').props('color=indigo')
                            ui.icon('dark_mode', color='indigo').classes('text-lg')
                    
                    # Explorer Sub-Tabs to switch between SQL Workspace and ER Diagram
                    with ui.tabs(value='SQL Workspace', on_change=lambda e: refresh_er_diagram() if e.value == 'ER Diagram' else None).classes('w-full border-b text-indigo-500 mb-1') as workspace_sub_tabs:
                        editor_sub_tab = ui.tab('SQL Workspace', icon='code')
                        er_sub_tab = ui.tab('ER Diagram', icon='schema')

                    # Create the split row layout using programmatic slots to avoid re-indenting the rest of layout
                    workspace_split_row = ui.row().classes('w-full flex-grow no-wrap min-h-0 gap-3 p-0')
                    workspace_split_row.__enter__()
                    
                    left_workspace_col = ui.column().classes('h-full flex-grow min-h-0 gap-2 flex-nowrap overflow-hidden p-0')
                    left_workspace_col.__enter__()

                    # SQL Editor Card Container
                    sql_editor_card = ui.card().classes('w-full p-2.5 shadow-sm border-slate-200 dark:border-slate-800')
                    sql_editor_card.bind_visibility_from(workspace_sub_tabs, 'value', value='SQL Workspace')
                    with sql_editor_card:
                        
                        # 🧱 INTERACTIVE VISUAL QUERY BUILDER
                        with ui.expansion('🧱 Interactive Visual Query Builder', icon='auto_awesome', value=False).classes('w-full border border-dashed border-indigo-200 dark:border-indigo-900 rounded-lg p-1.5 dark-bg-panel mb-1.5 text-xs text-indigo-600 dark:text-indigo-400 font-bold') as query_builder_expansion:
                            with ui.column().classes('w-full gap-2 p-1.5'):
                                ui.label('Select table and fields to construct standard SQL queries automatically:').classes('text-xs text-slate-500 font-normal')
                                
                                # Grid for Dropdowns
                                with ui.row().classes('w-full items-center gap-4 flex-wrap'):
                                    qb_db_select = ui.select(options=[], value=None, label='Select Database', on_change=lambda e: update_builder_tables_for_db(e.value)).props('dense outlined clearable').style('width: 180px;')
                                    qb_table_select = ui.select(options=[], value=None, label='1. Select Table', on_change=lambda e: handle_builder_table_change(e.value)).props('dense outlined clearable').style('width: 220px;')
                                    qb_order_select = ui.select(options=[], label='3. Order By (Optional)').props('dense outlined').style('width: 200px;')
                                    qb_dir_select = ui.select(options={'ASC': 'Ascending', 'DESC': 'Descending'}, value='ASC', label='Direction').props('dense outlined').style('width: 120px;')
                                    qb_limit_input = ui.number(value=100, label='4. Limit Rows', min=1).props('dense outlined').style('width: 100px;')
 
                                # Columns multi-select checkbox list
                                with ui.column().classes('w-full gap-1 border border-slate-200 dark:border-slate-800 rounded p-2 dark-bg-flat'):
                                    ui.label('2. Select Columns').classes('text-xs font-bold text-slate-600 dark:text-slate-400')
                                    qb_columns_container = ui.row().classes('w-full gap-2 flex-wrap items-center max-h-24 overflow-y-auto pr-1')
                                
                                # Filter (WHERE) Conditions
                                with ui.row().classes('w-full items-center gap-2 flex-wrap border border-slate-200 dark:border-slate-800 rounded p-2 dark-bg-flat'):
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
                                with ui.row().classes('w-full justify-end gap-2 mt-1'):
                                    ui.button('Reset Builder', icon='restart_alt', color='warning', on_click=lambda: reset_query_builder()).props('outline dense')
                                    ui.button('Generate SQL', icon='code', color='secondary', on_click=lambda: generate_builder_sql(run_query=False)).props('dense')
                                    ui.button('Generate & Execute', icon='flash_on', color='primary', on_click=lambda: generate_builder_sql(run_query=True)).props('dense')
 
                        # SQL Quick actions toolbar
                        with ui.row().classes('w-full justify-between items-center no-wrap gap-2 pb-1'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('code', color='primary').classes('text-lg')
                                ui.label('SQL Query Editor').classes('font-semibold text-slate-700 dark:text-slate-300')
                        
                        # Dynamic parameter inputs card for Parameterized Saved Queries
                        parameter_inputs_card = ui.card().classes('w-full p-3 border rounded shadow-none dark-bg-panel gap-2').style('display: none;')
                        
                        # SQL Code Editor itself (using NiceGUI CodeMirror component with linter bound)
                        initial_query = app.storage.user.get('last_query', query_history[0])
                        initial_dark = app.storage.user.get('dark_mode', False)
                        initial_theme = 'monokai' if initial_dark else 'basicLight'
                        sql_editor = ui.codemirror(
                            value=initial_query, 
                            language='sql', 
                            theme=initial_theme,
                            on_change=validate_sql_on_change
                        ).classes('w-full border rounded shadow-inner').style('height: 110px; font-size: 13px;')
                        
                        # Live Linter Status Strip
                        with ui.row().classes('w-full items-center no-wrap gap-2 px-3 py-0.5 -mt-1 rounded bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800') as linter_strip:
                            linter_icon = ui.icon('check_circle', color='emerald').classes('text-sm')
                            linter_label = ui.label('SQL Syntax Valid').classes('text-[11px] font-mono text-emerald-600 dark:text-emerald-400')
 
                        # Control Buttons
                        with ui.row().classes('w-full justify-between items-center pt-1.5 no-wrap'):
                            with ui.row().classes('gap-2'):
                                ui.button('Execute Query', icon='play_arrow', color='primary', 
                                          on_click=lambda: run_editor_query()).props('elevated dense').classes('px-3 text-xs')
                                ui.button('Explain Query', icon='troubleshoot', color='secondary', 
                                          on_click=lambda: trigger_explain_query()).props('elevated dense').classes('px-2.5 text-xs')
                                ui.button('Save Query', icon='bookmark_add', color='positive',
                                          on_click=lambda: open_save_query_dialog()).props('elevated dense').classes('text-xs')
                                ui.button('Format SQL', icon='format_align_left', color='secondary',
                                          on_click=lambda: format_sql_query()).props('outline dense').classes('text-xs')
                                ui.button('Clear', icon='delete_sweep', color='negative',
                                          on_click=lambda: sql_editor.set_value('')).props('flat dense').classes('text-xs')
                                ui.button('History', icon='history', color='secondary',
                                          on_click=lambda: history_drawer.toggle()).props('elevated dense').classes('text-xs')
                            
                            ui.label('Press Ctrl+Enter inside workspace to run').classes('text-[10px] text-slate-400 font-mono hidden md:block')
                    
                    # Output Results Card Container
                    output_results_card = ui.card().classes('w-full flex-grow p-2.5 shadow-sm border-slate-200 dark:border-slate-800 overflow-hidden min-h-0 flex-nowrap')
                    output_results_card.bind_visibility_from(workspace_sub_tabs, 'value', value='SQL Workspace')
                    with output_results_card:
                        
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
                            with ui.tab_panel(profile_tab).classes('p-0 pt-2 gap-2 flex-col h-full min-h-0 overflow-auto'):
                                # Profiler Controls
                                with ui.card().classes('w-full p-2 border-none shadow-none dark-bg-flat'):
                                    with ui.row().classes('w-full items-center gap-2 flex-wrap justify-between'):
                                        with ui.row().classes('items-center gap-2 flex-wrap'):
                                            profile_mode_select = ui.select(
                                                options=['Logical Plan (EXPLAIN)', 'Execution Profile (EXPLAIN ANALYZE)'],
                                                value='Logical Plan (EXPLAIN)',
                                                label='Profiler Mode'
                                            ).props('dense outlined').style('width: 280px;')
                                        
                                        ui.button('Profile Query', icon='speed', color='secondary',
                                                  on_click=lambda: run_profiler_query()).props('dense elevated').classes('px-3')
                                
                                # Dynamic Profiler Container
                                profiler_container = ui.column().classes('w-full gap-2 flex-nowrap flex-grow min-h-0 h-full overflow-auto')
                                with profiler_container:
                                    ui.label('Click "Profile Query" or use "Explain Query" to analyze execution plan.').classes('text-slate-400')

                            # SESSION HISTORY TAB
                            with ui.tab_panel(history_tab).classes('p-0 pt-4 gap-4 flex-col items-center justify-center h-full min-h-0'):
                                ui.icon('history', size='xl', color='slate').classes('text-slate-400')
                                ui.label('Access Execution Query History Timeline').classes('text-sm font-semibold text-slate-700 dark:text-slate-300')
                                ui.label('Review metrics, recover, or compare the last 50 executed queries in the side panel.').classes('text-xs text-slate-400 text-center max-w-xs')
                                ui.button('Open History Drawer', icon='menu_open', on_click=lambda: history_drawer.show()).props('elevated color=primary').classes('px-4 py-2 mt-2')

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

                    # ER Diagram Card Container
                    er_diagram_card = ui.card().classes('w-full flex-grow p-4 shadow-sm border border-slate-200 dark:border-slate-800 overflow-hidden min-h-0 flex-nowrap dark-bg-panel')
                    er_diagram_card.bind_visibility_from(workspace_sub_tabs, 'value', value='ER Diagram')
                    with er_diagram_card:
                        with ui.row().classes('w-full items-center justify-between no-wrap gap-4 pb-2 border-b'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('schema', color='primary').classes('text-xl')
                                ui.label('Interactive ER Schema Diagram').classes('font-semibold text-slate-700 dark:text-slate-300')
                                
                            with ui.row().classes('items-center gap-2 no-wrap'):
                                # Table filter input
                                er_filter_input = ui.input(placeholder='Filter tables (e.g. users)...', on_change=lambda: refresh_er_diagram()).props('outlined dense clearable size=sm').classes('w-64 text-xs')
                                
                                # Refresh button
                                ui.button(icon='refresh', on_click=lambda: refresh_er_diagram()).props('flat dense round size=sm').classes('text-slate-600').tooltip('Reload schema diagram')
                        
                        # Mermaid Diagram HTML Container
                        er_diagram_container = ui.column().classes('w-full flex-grow overflow-auto items-center justify-center p-4 bg-slate-50/50 dark:bg-slate-900/50 rounded-lg border border-dashed border-slate-200 dark:border-slate-800 min-h-0')
                        with er_diagram_container:
                            er_diagram_html = ui.html('<div class="text-slate-400 text-sm">Loading ER Diagram...</div>', sanitize=False).classes('mermaid w-full text-center flex justify-center')

                    # Helper function to refresh and render the ER diagram
                    def refresh_er_diagram():
                        try:
                            active_db = 'main'
                            try:
                                active_db = explorer.conn.execute("SELECT current_database();").fetchone()[0]
                            except Exception:
                                pass
                                
                            filter_text = er_filter_input.value if er_filter_input.value else ""
                            mermaid_code = generate_schema_mermaid(explorer, active_db, filter_text)
                            
                            import uuid
                            unique_id = f"mermaid-graph-{uuid.uuid4().hex[:8]}"
                            er_diagram_html.content = f'<pre id="{unique_id}" class="mermaid text-center" style="display: block; width: 100%; margin: 0 auto;">{mermaid_code}</pre>'
                            
                            ui.run_javascript("""
                                try {
                                    mermaid.run({
                                        nodes: [document.getElementById('%s')]
                                    });
                                } catch(e) {
                                    console.error("Mermaid render error:", e);
                                }
                            """ % unique_id)
                        except Exception as refresh_ex:
                            ui.notify(f"Failed to refresh ER diagram: {refresh_ex}", type='negative')

                    # Exit the left column slot context
                    left_workspace_col.__exit__(None, None, None)

                    # Right AI Assistant Panel Column (collapsible)
                    ai_panel_col = ui.column().classes('h-full w-80 flex-none gap-2 flex-nowrap overflow-hidden p-3 border border-slate-200 dark:border-slate-800 rounded-xl dark-bg-panel shadow-sm min-h-0')
                    ai_panel_col.visible = app.storage.user.get('ai_panel_visible', False)
                    with ai_panel_col:
                        with ui.row().classes('w-full items-center justify-between border-b pb-2 flex-none'):
                            with ui.row().classes('items-center gap-1.5'):
                                ui.icon('auto_awesome', color='primary').classes('text-lg')
                                ui.label('AI SQL Copilot').classes('font-bold text-slate-800 dark:text-white text-sm')
                            ui.button(icon='close', on_click=lambda: toggle_ai_panel()).props('flat round dense size=sm').classes('text-slate-500')
                            
                        # Chat history scroll area
                        chat_history_area = ui.scroll_area().classes('w-full flex-grow min-h-0 pr-1')
                        with chat_history_area:
                            chat_history_container = ui.column().classes('w-full gap-2.5 p-1')
                            with chat_history_container:
                                ui.chat_message(
                                    text='Hello! I am your AI SQL Copilot. I can write queries, explain them, or fix errors. Configure your API key in Settings to get started!',
                                    name='Copilot',
                                    avatar='https://api.dicebear.com/7.x/bottts/svg?seed=copilot',
                                    sent=False
                                )
                                
                        # Explainer & Action buttons
                        with ui.row().classes('w-full gap-1 flex-none pt-1 border-t'):
                            ui.button('Explain Code', icon='info', on_click=lambda: run_ai_action('explain')).props('outline dense size=xs color=primary').classes('flex-grow text-[10px] font-bold')
                            ui.button('Fix Query', icon='build', on_click=lambda: run_ai_action('fix')).props('outline dense size=xs color=warning').classes('flex-grow text-[10px] font-bold')
                            
                        # Chat Input text area
                        with ui.row().classes('w-full items-center gap-1.5 flex-none pt-1'):
                            chat_input = ui.input(placeholder='Ask Copilot a question...').props('outlined dense').classes('flex-grow text-xs').style('font-size: 11px;')
                            chat_input.on('keydown.enter', lambda: send_chat_message())
                            ui.button(icon='send', on_click=lambda: send_chat_message()).props('elevated dense color=primary size=sm').classes('px-2.5')

                    # Exit the split row slot context
                    workspace_split_row.__exit__(None, None, None)

                    # AI Assistant Helpers
                    def toggle_ai_panel():
                        ai_panel_col.visible = not ai_panel_col.visible
                        ai_panel_col.update()
                        app.storage.user['ai_panel_visible'] = ai_panel_col.visible
                        if ai_panel_col.visible:
                            ai_toggle_btn.props('unelevated color=indigo')
                        else:
                            ai_toggle_btn.props('flat color=primary')
                        ai_toggle_btn.update()

                    # Set initial button state based on storage
                    if ai_panel_col.visible:
                        ai_toggle_btn.props('unelevated color=indigo')
                    else:
                        ai_toggle_btn.props('flat color=primary')

                    def get_active_schema_summary():
                        try:
                            active_db = 'main'
                            try:
                                active_db = explorer.conn.execute("SELECT current_database();").fetchone()[0]
                            except Exception:
                                pass
                            
                            cols_rows = explorer.conn.execute("""
                                SELECT table_name, column_name, data_type 
                                FROM duckdb_columns 
                                WHERE database_name = ? AND schema_name = 'main'
                                ORDER BY table_name, column_index;
                            """, [active_db]).fetchall()
                            
                            if not cols_rows:
                                return "No tables found in active schema."
                                
                            tables = {}
                            for tbl, col, dtype in cols_rows:
                                if tbl not in tables:
                                    tables[tbl] = []
                                tables[tbl].append(f"{col} ({dtype})")
                                
                            summary = []
                            for tbl, cols in tables.items():
                                summary.append(f"Table '{tbl}' columns: {', '.join(cols)}")
                            return "\n".join(summary)
                        except Exception as ex:
                            return f"Error gathering schema context: {ex}"

                    async def send_ai_request_stream(prompt):
                        provider = APP_SETTINGS.get('ai_provider', 'none')
                        api_key = APP_SETTINGS.get('ai_api_key', '')
                        model = APP_SETTINGS.get('ai_model', 'gpt-4o')
                        base_url = APP_SETTINGS.get('ai_base_url', '')
                        
                        if provider == 'none':
                            yield "AI Copilot is currently disabled. Please configure your AI Provider in the Settings tab."
                            return
                            
                        system_prompt = f"""You are an expert DuckDB SQL Co-Pilot inside DuckDB Data Studio.
Here is the active database schema:
{get_active_schema_summary()}

Always provide DuckDB SQL code in standard markdown ```sql code blocks. Keep explanations concise and professional."""

                        import httpx
                        import json
                        
                        if provider in ('openai', 'custom', 'ollama'):
                            url = "https://api.openai.com/v1/chat/completions"
                            if base_url:
                                url = base_url
                            elif provider == 'ollama':
                                url = "http://host.docker.internal:11434/v1"
                                
                            url = url.rstrip('/')
                            if not url.endswith('/chat/completions'):
                                if not url.endswith('/v1'):
                                    url = url + '/v1'
                                url = url + '/chat/completions'
                                    
                            headers = {
                                "Content-Type": "application/json"
                            }
                            if api_key:
                                headers["Authorization"] = f"Bearer {api_key}"
                                
                            payload = {
                                "model": model,
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": prompt}
                                ],
                                "temperature": 0.2,
                                "stream": True
                            }
                            
                            try:
                                async with httpx.AsyncClient(timeout=60.0) as client:
                                    async with client.stream("POST", url, json=payload, headers=headers) as resp:
                                        if resp.status_code == 200:
                                            async for line in resp.aiter_lines():
                                                line_clean = line.strip()
                                                if not line_clean:
                                                    continue
                                                print(f"DEBUG: AI Stream Line: {line_clean}", flush=True)
                                                if line_clean.startswith("data:"):
                                                    data_str = line_clean[5:].strip()
                                                    if data_str == "[DONE]":
                                                        break
                                                    try:
                                                        chunk = json.loads(data_str)
                                                        delta = chunk.get('choices', [{}])[0].get('delta', {})
                                                        if 'content' in delta:
                                                            yield delta['content']
                                                    except Exception as parse_ex:
                                                        print(f"DEBUG: JSON parse error: {parse_ex} on {data_str}", flush=True)
                                        else:
                                            err_text = await resp.aread()
                                            yield f"Error from LLM Provider: {resp.status_code} - {err_text.decode(errors='ignore')}"
                            except Exception as ex:
                                yield f"Failed to connect to LLM Provider: {ex}"
                                
                        elif provider == 'anthropic':
                            url = "https://api.anthropic.com/v1/messages"
                            headers = {
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json"
                            }
                            payload = {
                                "model": model,
                                "system": system_prompt,
                                "messages": [
                                    {"role": "user", "content": prompt}
                                ],
                                "max_tokens": 1024,
                                "temperature": 0.2
                            }
                            try:
                                async with httpx.AsyncClient(timeout=60.0) as client:
                                    resp = await client.post(url, json=payload, headers=headers)
                                    if resp.status_code == 200:
                                        res_json = resp.json()
                                        yield res_json['content'][0]['text']
                                    else:
                                        yield f"Error from Anthropic: {resp.status_code} - {resp.text}"
                            except Exception as ex:
                                yield f"Failed to connect to Anthropic: {ex}"
                                
                        else:
                            yield "Unsupported AI Provider."

                    async def send_chat_message():
                        text = chat_input.value.strip()
                        if not text:
                            return
                        chat_input.value = ''
                        
                        with chat_history_container:
                            ui.chat_message(text=text, name='User', sent=True)
                        with chat_history_container:
                            typing_msg = ui.chat_message(text='', name='Copilot', sent=False)
                            
                        chat_history_area.scroll_to(percent=1.0)
                        
                        import time
                        full_response = ""
                        last_update = time.time()
                        async for chunk in send_ai_request_stream(text):
                            full_response += chunk
                            now = time.time()
                            if now - last_update > 0.15:
                                typing_msg.text = full_response
                                typing_msg.update()
                                chat_history_area.scroll_to(percent=1.0)
                                last_update = now
                        
                        typing_msg.text = full_response
                        typing_msg.update()
                        chat_history_area.scroll_to(percent=1.0)
                        
                    async def run_ai_action(action):
                        sql = sql_editor.value.strip()
                        if not sql:
                            ui.notify('SQL editor is empty!', type='warning')
                            return
                            
                        if not ai_panel_col.visible:
                            toggle_ai_panel()
                            
                        prompt = ""
                        if action == 'explain':
                            prompt = f"Explain this SQL query in detail, explaining what it does step by step:\n\n```sql\n{sql}\n```"
                        elif action == 'fix':
                            error_text = status_label.text
                            prompt = f"This SQL query failed to run with the following error/status:\n{error_text}\n\nHere is the SQL query:\n\n```sql\n{sql}\n```\n\nPlease suggest how to fix it."
                            
                        with chat_history_container:
                            ui.chat_message(text=f"Requesting query {action}...", name='User', sent=True)
                        with chat_history_container:
                            typing_msg = ui.chat_message(text='', name='Copilot', sent=False)
                            
                        chat_history_area.scroll_to(percent=1.0)
                        
                        import time
                        full_response = ""
                        last_update = time.time()
                        async for chunk in send_ai_request_stream(prompt):
                            full_response += chunk
                            now = time.time()
                            if now - last_update > 0.15:
                                typing_msg.text = full_response
                                typing_msg.update()
                                chat_history_area.scroll_to(percent=1.0)
                                last_update = now
                                
                        typing_msg.text = full_response
                        typing_msg.update()
                        chat_history_area.scroll_to(percent=1.0)

    # --- CALLBACKS ENCAPSULATED INSIDE INDEX CLIENT CONTEXT ---

    def get_table_statistics(schema_name, table_name, database_name='main', attached_dbs=None):
        """Perform analytical scans on a table to compute card metrics and column statistics."""
        # Open a dedicated connection for this background thread to ensure thread safety
        thread_conn = duckdb.connect(explorer.db_file, config=DB_CONFIG)
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

    def generate_schema_mermaid(explorer_instance, active_db, filter_text=""):
        try:
            # 1. Fetch tables and columns in the active schema (main)
            query_cols = """
                SELECT table_name, column_name, data_type 
                FROM duckdb_columns 
                WHERE database_name = ? AND schema_name = 'main'
                ORDER BY table_name, column_index;
            """
            cols_rows = explorer_instance.conn.execute(query_cols, [active_db]).fetchall()
            if not cols_rows:
                return "erDiagram\n    %% No tables found in the active schema."

            # Group columns by table_name
            tables = {}
            for tbl, col, dtype in cols_rows:
                # Apply text filter if provided
                if filter_text:
                    ft = filter_text.strip().lower()
                    if ft not in tbl.lower() and ft not in col.lower():
                        continue
                if tbl not in tables:
                    tables[tbl] = []
                tables[tbl].append((col, dtype))

            if not tables:
                return "erDiagram\n    %% No tables match your filter criteria."

            # 2. Fetch explicit constraints from the active database
            constraints_list = []
            try:
                constraints_list = explorer_instance.conn.execute("SELECT table_name, constraint_type, constraint_text FROM duckdb_constraints").fetchall()
            except Exception:
                pass

            # Identify primary keys per table
            primary_keys = {}
            for tbl, c_type, c_text in constraints_list:
                if tbl not in tables:
                    continue
                if c_type == 'PRIMARY KEY':
                    import re
                    pk_match = re.search(r"PRIMARY KEY\((.*?)\)", c_text, re.IGNORECASE)
                    if pk_match:
                        cols = [c.strip().strip('"').strip('`') for c in pk_match.group(1).split(',')]
                        if tbl not in primary_keys:
                            primary_keys[tbl] = set()
                        primary_keys[tbl].update(cols)

            # 3. Build relations list
            relations = []
            
            # Attempt to parse explicit FOREIGN KEY constraints if any
            for tbl, c_type, c_text in constraints_list:
                if tbl not in tables:
                    continue
                if c_type == 'FOREIGN KEY':
                    import re
                    fk_match = re.search(r"FOREIGN KEY\s*\((.*?)\)\s*REFERENCES\s*(\w+)\s*\((.*?)\)", c_text, re.IGNORECASE)
                    if fk_match:
                        fk_col = fk_match.group(1).strip().strip('"').strip('`')
                        target_tbl = fk_match.group(2).strip()
                        pk_col = fk_match.group(3).strip().strip('"').strip('`')
                        if target_tbl in tables:
                            relations.append((tbl, fk_col, target_tbl, pk_col, "explicit"))

            # Apply heuristics
            for tbl in tables.keys():
                for col, dtype in tables[tbl]:
                    col_lower = col.lower()
                    if col_lower.endswith('_id') or col_lower.endswith('id'):
                        prefix = col_lower[:-3] if col_lower.endswith('_id') else col_lower[:-2]
                        if not prefix:
                            continue
                        
                        # Normalize prefix for loose matching (e.g. ignoring underscores)
                        norm_prefix = prefix.replace('_', '')
                        candidate_norms = [norm_prefix, norm_prefix + 's', norm_prefix + 'es']
                        if norm_prefix.endswith('y'):
                            candidate_norms.append(norm_prefix[:-1] + 'ies')
                        
                        for target_tbl in tables.keys():
                            if target_tbl == tbl:
                                continue
                            norm_target = target_tbl.replace('_', '')
                            if norm_target in candidate_norms:
                                target_columns = [c[0].lower() for c in tables[target_tbl]]
                                target_pk = 'id'
                                if 'id' in target_columns:
                                    if not any(r[0] == tbl and r[1] == col and r[2] == target_tbl for r in relations):
                                        relations.append((tbl, col, target_tbl, target_pk, "heuristic"))
                                break

            # Generate Mermaid String
            lines = ["erDiagram"]

            # Render Entities
            for tbl, cols in tables.items():
                lines.append(f"    {tbl} {{")
                for col, dtype in cols:
                    type_display = dtype.split('(')[0].lower()
                    type_display = "".join([c if c.isalnum() else "_" for c in type_display])
                    
                    key_ann = ""
                    is_pk = False
                    is_fk = False
                    
                    if tbl in primary_keys and col in primary_keys[tbl]:
                        is_pk = True
                    elif col.lower() == 'id':
                        is_pk = True
                    
                    if any(r[0] == tbl and r[1] == col for r in relations):
                        is_fk = True

                    if is_pk and is_fk:
                        key_ann = "PK,FK"
                    elif is_pk:
                        key_ann = "PK"
                    elif is_fk:
                        key_ann = "FK"
                        
                    label_display = f" {key_ann}" if key_ann else ""
                    lines.append(f"        {type_display} {col}{label_display}")
                lines.append("    }")

            # Render Relationships
            for source_tbl, source_col, target_tbl, target_col, rel_type in relations:
                lines.append(f"    {target_tbl} ||--o{{ {source_tbl} : \"{source_col}\"")

            return "\n".join(lines)
        except Exception as err_ex:
            return f"erDiagram\n    %% Error generating diagram: {str(err_ex)}"

    def refresh_databases_list():
        """Fetch all attached databases from duckdb_databases and render them beautifully."""
        def detach_database_action(db_name):
            if db_name in ('main', 'starter'):
                ui.notify("Cannot detach the primary database!", type='warning')
                return
            try:
                explorer.conn.execute("USE main;")
                explorer.conn.execute(f"DETACH {db_name};")
                remove_attached_database(db_name)
                ui.notify(f"Successfully detached database '{db_name}'", type='success')
                refresh_schema_tree()
            except Exception as e:
                ui.notify(f"Failed to detach database: {str(e)}", type='negative', duration=5)

        def set_active_database_action(db_name):
            try:
                explorer.conn.execute(f"USE {db_name};")
                app.storage.user['active_database'] = db_name
                ui.notify(f"Database context switched to '{db_name}'", type='success')
                refresh_schema_tree()
            except Exception as e:
                ui.notify(f"Failed to set active database: {str(e)}", type='negative')

        databases_container.clear()
        try:
            db_rows = explorer.conn.execute("SELECT database_name, path FROM duckdb_databases ORDER BY database_name").fetchall()
            active_db = 'main'
            try:
                active_db = explorer.conn.execute("SELECT current_database();").fetchone()[0]
            except Exception:
                pass
                
            with databases_container:
                for db_name, db_path in db_rows:
                    if db_name in ('system', 'temp') or db_name.startswith('__'):
                        continue
                        
                    is_active = (db_name == active_db)
                    is_primary = (db_name in ('main', 'starter'))
                    badge_color = 'indigo' if is_active else 'emerald'
                    
                    with ui.row().classes('w-full items-center justify-between no-wrap gap-1 py-0.5 px-1 rounded hover:bg-slate-100/50 dark:hover:bg-slate-800/50 transition'):
                        with ui.row().classes('items-center gap-1 no-wrap truncate'):
                            # Star active context toggle button
                            if is_active:
                                ui.button(icon='star', on_click=lambda db=db_name: set_active_database_action(db)).props('flat dense round size=xs color=amber').tooltip('Active database context')
                            else:
                                ui.button(icon='star_border', on_click=lambda db=db_name: set_active_database_action(db)).props('flat dense round size=xs').classes('text-slate-400 hover:text-amber-500').tooltip('Set as active database context')
                                
                            db_icon = 'storage' if is_primary else 'cloud_queue'
                            ui.icon(db_icon, color=badge_color).classes('text-xs')
                            with ui.column().classes('gap-0 truncate'):
                                ui.label(db_name).classes('text-[11px] font-bold text-slate-800 dark:text-slate-100')
                                if db_path:
                                    # Show filename or path
                                    path_display = os.path.basename(db_path) if not db_path.startswith('ducklake:') else db_path
                                    ui.label(path_display).classes('text-[9px] font-mono text-slate-400 truncate').style('max-width: 120px;')
                                else:
                                    ui.label('In-Memory').classes('text-[9px] text-slate-400 font-mono')
                        
                        if not is_primary:
                            with ui.row().classes('items-center gap-0 no-wrap'):
                                ui.button(icon='edit', on_click=lambda db=db_name, path=db_path: open_rename_dialog(db, path)).props('flat dense round size=sm').classes('text-slate-400 hover:text-primary').tooltip('Rename connection alias')
                                ui.button(icon='delete', on_click=lambda db=db_name: detach_database_action(db)).props('flat dense round size=sm').classes('text-slate-400 hover:text-rose-500').tooltip('Detach database')
                        else:
                            if is_active:
                                ui.badge('Active', color=badge_color).classes('text-[8px] py-0.5 px-1')
                            else:
                                ui.badge('Primary', color='slate').classes('text-[8px] py-0.5 px-1')
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
                    
            if filter_text and expanded_keys:
                tree_state['expanded'] = list(set(expanded_keys))
            elif not tree_state['expanded']:
                current_active = 'main'
                try:
                    current_active = explorer.conn.execute("SELECT current_database();").fetchone()[0]
                except Exception:
                    pass
                tree_state['expanded'] = [current_active]
                
            def delete_table_action(table_id):
                nonlocal drop_target_db, drop_target_schema, drop_target_table
                parts = table_id.split('.')
                if len(parts) == 3:
                    drop_target_db, drop_target_schema, drop_target_table = parts
                    drop_title_label.text = f"Drop Table/View '{drop_target_db}.{drop_target_schema}.{drop_target_table}'?"
                    drop_table_dialog.open()

            tree_widget = ui.tree(nodes, label_key='label', on_select=handle_node_click).props('dense accordion').classes('text-slate-800 dark:text-slate-100')
            tree_widget.expanded = tree_state['expanded']
            tree_widget.on('update:expanded', lambda e: tree_state.update(expanded=e.args))
            
            with tree_widget:
                tree_widget.add_slot('default-header', f'''
                    <div class="row items-center justify-between no-wrap full-width">
                        <div class="row items-center no-wrap">
                            <q-icon :name="props.node.icon" class="q-mr-sm" />
                            <div>{{{{ props.node.label }}}}</div>
                        </div>
                        <q-btn v-if="props.node.id && props.node.id.split('.').length === 3" 
                               flat round dense size="xs" color="negative" icon="delete" 
                               @click.stop="getElement({tree_widget.id}).$emit('delete_node', props.node.id)">
                            <q-tooltip>Drop table/view</q-tooltip>
                        </q-btn>
                    </div>
                ''')
            tree_widget.on('delete_node', lambda e: delete_table_action(e.args))
                    
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
            populate_wizard_databases()
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
        
        try:
            trans_badge.set_text(f"{tx_count:,} Rows")
            trans_badge.props(f"color={get_color(tx_count)}")
            trans_badge.update()
            
            cust_badge.set_text(f"{cust_count:,} Rows")
            cust_badge.props(f"color={get_color(cust_count)}")
            cust_badge.update()
            
            invent_badge.set_text(f"{inv_count:,} Rows")
            invent_badge.props(f"color={get_color(inv_count)}")
            invent_badge.update()
        except NameError:
            pass
        
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
        elif density_value == '300000':
            num_customers = 10000
            num_transactions = 300000
            density_label = "300,000"
        elif density_value == '1500000':
            num_customers = 50000
            num_transactions = 1500000
            density_label = "1,500,000"
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

    def seed_car_rental_data(conn, num_customers=100, num_reservations=300):
        from faker import Faker
        import random
        import uuid
        from datetime import datetime, timedelta
        fake = Faker()
        Faker.seed(42)
        
        # Clear existing data just in case
        conn.execute("DELETE FROM maintenance_logs; DELETE FROM payments; DELETE FROM reservations; DELETE FROM driver_licenses; DELETE FROM customers; DELETE FROM cars; DELETE FROM vehicle_profiles; DELETE FROM locations;")
        
        # 1. seed locations
        locations = [
            (1, "LAX Airport Hub", "9000 Airport Blvd", "Los Angeles", "CA", "90045", "USA", True),
            (2, "SFO Airport Hub", "780 N McDonnell Rd", "San Francisco", "CA", "94128", "USA", True),
            (3, "JFK Airport Hub", "Building 123 Federal Circle", "Jamaica", "NY", "11430", "USA", True),
            (4, "Miami Downtown Hub", "200 SE 2nd Ave", "Miami", "FL", "33131", "USA", True),
            (5, "Seattle Airport Hub", "3150 S 160th St", "SeaTac", "WA", "98188", "USA", True)
        ]
        conn.executemany("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", locations)
        
        # 2. seed vehicle_profiles
        profiles = [
            (1, "Economy", "Toyota", "Corolla", 2022, "gasoline", 5, 2, 45.0),
            (2, "Economy", "Honda", "Civic", 2023, "gasoline", 5, 2, 48.0),
            (3, "SUV", "Jeep", "Grand Cherokee", 2021, "gasoline", 5, 4, 85.0),
            (4, "SUV", "Ford", "Explorer", 2022, "gasoline", 7, 5, 95.0),
            (5, "Convertible", "Ford", "Mustang", 2023, "gasoline", 4, 2, 120.0),
            (6, "Electric", "Tesla", "Model 3", 2023, "electric", 5, 3, 90.0),
            (7, "Electric", "Tesla", "Model Y", 2022, "electric", 5, 4, 110.0)
        ]
        conn.executemany("INSERT INTO vehicle_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", profiles)
        
        # 3. seed cars
        cars = []
        for c_id in range(1, 31):
            prof = random.choice(profiles)
            loc = random.choice(locations)
            vin = fake.unique.vin()
            plate = fake.unique.license_plate()
            color = random.choice(["Black", "White", "Silver", "Gray", "Red", "Blue"])
            odom = random.randint(1000, 80000)
            status = random.choice(["available", "available", "available", "rented", "maintenance"])
            cars.append((c_id, prof[0], loc[0], vin, plate, color, odom, status, datetime.now()))
        conn.executemany("INSERT INTO cars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", cars)
        
        # 4. seed customers
        customers = []
        licenses = []
        states = ["CA", "NY", "FL", "TX", "WA", "NV", "OR", "AZ"]
        for cust_id in range(1, num_customers + 1):
            fn = fake.first_name()
            ln = fake.last_name()
            email = f"{fn.lower()}.{ln.lower()}@example.com"
            phone = fake.phone_number()
            status = random.choices(["active", "suspended", "blacklisted"], weights=[95, 4, 1], k=1)[0]
            created = datetime.now() - timedelta(days=random.randint(30, 700))
            customers.append((cust_id, email, fn, ln, phone, status, created, created))
            
            # license
            lic_num = fake.unique.bothify(text='??######')
            exp = datetime.now() + timedelta(days=random.randint(30, 1500))
            verified = random.choice([True, True, False])
            ver_at = created + timedelta(days=2) if verified else None
            licenses.append((cust_id, lic_num, random.choice(states), "USA", exp.date(), verified, ver_at))
            
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?)", customers)
        conn.executemany("INSERT INTO driver_licenses VALUES (?, ?, ?, ?, ?, ?, ?)", licenses)
        
        # 5. seed reservations & payments
        reservations = []
        payments = []
        pay_id = 1
        
        for r_idx in range(1, num_reservations + 1):
            cust = random.choice(customers)
            car = random.choice(cars)
            pickup_loc = random.choice(locations)
            dropoff_loc = random.choice(locations)
            
            # date logistics
            pickup_time = datetime.now() - timedelta(days=random.randint(-15, 300))
            duration = random.randint(1, 14)
            dropoff_time = pickup_time + timedelta(days=duration)
            
            is_completed = pickup_time < datetime.now()
            status = "completed" if is_completed else random.choice(["confirmed", "active", "cancelled"])
            
            actual_pickup = pickup_time if status in ["completed", "active"] else None
            actual_dropoff = dropoff_time if status == "completed" else None
            
            # Find daily rate
            daily_rate = [p[8] for p in profiles if p[0] == car[1]][0]
            rental_cost = round(daily_rate * duration, 2)
            ins_cost = round(random.choice([0.0, 15.0, 30.0]) * duration, 2)
            late_fees = round(random.choice([0.0, 0.0, 50.0]) if status == "completed" else 0.0, 2)
            tax = round((rental_cost + ins_cost) * 0.08, 2)
            total = round(rental_cost + ins_cost + late_fees + tax, 2)
            
            reservations.append((
                r_idx, uuid.uuid4(), cust[0], car[0],
                pickup_loc[0], dropoff_loc[0],
                pickup_time, dropoff_time,
                actual_pickup, actual_dropoff,
                status, rental_cost, ins_cost, late_fees, tax, total,
                pickup_time - timedelta(days=random.randint(1, 10)),
                datetime.now()
            ))
            
            # Payment for reservations
            if status != "cancelled":
                p_status = "captured" if is_completed or status == "active" else "authorized"
                payments.append((
                    pay_id, r_idx, "Stripe", f"ch_{uuid.uuid4().hex[:12]}",
                    total, "deposit", p_status, pickup_time - timedelta(days=1)
                ))
                pay_id += 1
                
        conn.executemany("INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", reservations)
        conn.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?, ?)", payments)
        
        # 6. seed maintenance logs
        maintenance = []
        for m_idx in range(1, 15):
            car = random.choice(cars)
            m_type = random.choice(["oil_change", "tire_rotation", "body_repair", "detailing"])
            cost = random.choice([49.99, 89.99, 450.00, 120.00])
            odom = random.randint(1000, 80000)
            started = datetime.now() - timedelta(days=random.randint(10, 200))
            completed = started + timedelta(hours=random.randint(2, 48))
            maintenance.append((
                m_idx, car[0], m_type, f"Scheduled {m_type}", cost, odom, started, completed, "Done"
            ))
        conn.executemany("INSERT INTO maintenance_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", maintenance)

    def seed_e_commerce_data(conn, num_users=100, num_orders=300):
        from faker import Faker
        import random
        import json
        import uuid
        from datetime import datetime, timedelta
        fake = Faker()
        Faker.seed(42)
        
        # Clear existing data just in case
        conn.execute("DELETE FROM payments; DELETE FROM order_items; DELETE FROM orders; DELETE FROM product_images; DELETE FROM product_variants; DELETE FROM products; DELETE FROM categories; DELETE FROM user_addresses; DELETE FROM users;")
        
        # 1. seed categories
        categories = [
            (1, None, "Electronics", "electronics", "Gadgets and devices", True),
            (2, None, "Apparel", "apparel", "Clothing and fashion", True),
            (3, 1, "Smartphones", "smartphones", "Mobile phones", True),
            (4, 1, "Audio", "audio", "Headphones and speakers", True),
            (5, 2, "Menswear", "menswear", "Men's clothing", True)
        ]
        conn.executemany("INSERT INTO categories VALUES (?, ?, ?, ?, ?, ?)", categories)
        
        # 2. seed products
        products = [
            (1, 3, "iPhone 15 Pro", "iphone-15-pro", "Latest Apple flagship", "Apple", True, datetime.now(), datetime.now()),
            (2, 3, "Galaxy S24 Ultra", "galaxy-s24-ultra", "Premium Android device", "Samsung", True, datetime.now(), datetime.now()),
            (3, 4, "WH-1000XM5", "wh-1000xm5", "Top noise-cancelling headphones", "Sony", True, datetime.now(), datetime.now()),
            (4, 5, "Slim Fit Chinos", "slim-fit-chinos", "Comfortable cotton trousers", "Uniqlo", True, datetime.now(), datetime.now()),
            (5, 4, "Eco Speaker", "eco-speaker", "Portable bluetooth speaker", "JBL", True, datetime.now(), datetime.now())
        ]
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", products)
        
        # 3. seed variants
        variants = [
            (1, 1, "IPH15P-128-BLK", 999.0, 1099.0, 0.187, json.dumps({"color": "Black", "storage": "128GB"}), datetime.now(), datetime.now()),
            (2, 1, "IPH15P-256-SLV", 1099.0, None, 0.187, json.dumps({"color": "Silver", "storage": "256GB"}), datetime.now(), datetime.now()),
            (3, 2, "GALS24U-256-GRY", 1199.0, 1299.0, 0.232, json.dumps({"color": "Titanium Gray", "storage": "256GB"}), datetime.now(), datetime.now()),
            (4, 3, "SONYXM5-BLK", 348.0, 399.0, 0.250, json.dumps({"color": "Black"}), datetime.now(), datetime.now()),
            (5, 4, "CHINO-32-BEG", 39.90, None, 0.400, json.dumps({"size": "32", "color": "Beige"}), datetime.now(), datetime.now())
        ]
        conn.executemany("INSERT INTO product_variants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", variants)
        
        # 4. seed product_images
        images = [
            (1, 1, 1, "https://example.com/images/iphone15-blk.jpg", "iPhone 15 Pro Black", 1, True),
            (2, 2, 3, "https://example.com/images/s24u-gry.jpg", "Galaxy S24 Ultra Gray", 1, True),
            (3, 3, 4, "https://example.com/images/sony-xm5.jpg", "Sony WH-1000XM5 Black", 1, True)
        ]
        conn.executemany("INSERT INTO product_images VALUES (?, ?, ?, ?, ?, ?, ?)", images)
        
        # 5. seed users
        users = []
        addresses = []
        addr_id = 1
        
        for u_id in range(1, num_users + 1):
            fn = fake.first_name()
            ln = fake.last_name()
            email = f"{fn.lower()}.{ln.lower()}@example.com"
            phone = fake.phone_number()
            active = True
            created = datetime.now() - timedelta(days=random.randint(15, 600))
            users.append((u_id, email, fn, ln, phone, active, created, created))
            
            # shipping address
            addresses.append((
                addr_id, u_id, "shipping", True,
                fake.street_address(), None, fake.city(), fake.state(), fake.zipcode(), "USA", created
            ))
            addr_id += 1
            
            # optional billing address
            if random.choice([True, False]):
                addresses.append((
                    addr_id, u_id, "billing", False,
                    fake.street_address(), None, fake.city(), fake.state(), fake.zipcode(), "USA", created
                ))
                addr_id += 1
                
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)", users)
        conn.executemany("INSERT INTO user_addresses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", addresses)
        
        # 6. seed orders & order items & payments
        orders = []
        items = []
        payments = []
        item_id = 1
        pay_id = 1
        
        for o_id in range(1, num_orders + 1):
            user = random.choice(users)
            user_addrs = [a for a in addresses if a[1] == user[0]]
            ship_addr = user_addrs[0] if user_addrs else None
            bill_addr = user_addrs[-1] if user_addrs else None
            
            created = datetime.now() - timedelta(days=random.randint(1, 200))
            o_status = random.choice(["completed", "completed", "completed", "shipped", "pending", "cancelled"])
            
            # Items selection
            num_items = random.choices([1, 2, 3], weights=[70, 20, 10], k=1)[0]
            selected_vars = random.sample(variants, num_items)
            
            subtotal = 0.0
            order_items_to_add = []
            for var in selected_vars:
                qty = random.choice([1, 1, 2])
                price = var[3]
                subtotal += price * qty
                
                order_items_to_add.append((
                    item_id, o_id, var[0], var[2], price, qty
                ))
                item_id += 1
                
            ship_cost = 0.0 if subtotal > 100.0 else 5.99
            tax = round(subtotal * 0.08, 2)
            discount = round(subtotal * 0.1 if random.choice([True, False, False]) else 0.0, 2)
            total = round(subtotal + ship_cost + tax - discount, 2)
            
            orders.append((
                o_id, f"ORD-{100000 + o_id}", user[0], o_status,
                subtotal, ship_cost, tax, discount, total,
                ship_addr[0] if ship_addr else None, bill_addr[0] if bill_addr else None,
                created, datetime.now()
            ))
            
            items.extend(order_items_to_add)
            
            if o_status != "cancelled":
                p_status = "captured" if o_status in ["completed", "shipped"] else "pending"
                payments.append((
                    pay_id, o_id, "Stripe", f"ch_{uuid.uuid4().hex[:12]}" if p_status == "captured" else "",
                    total, p_status, created
                ))
                pay_id += 1
                
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)
        conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", items)
        conn.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?)", payments)

    def seed_smart_home_data(conn, num_devices=100, num_readings=500):
        from faker import Faker
        import random
        from datetime import datetime, timedelta
        fake = Faker()
        Faker.seed(42)
        
        # Clear existing data just in case
        conn.execute("DELETE FROM device_events; DELETE FROM energy_consumption; DELETE FROM thermostat_readings; DELETE FROM devices; DELETE FROM rooms;")
        
        # 1. seed rooms
        rooms = [
            (1, "Living Room", 1, "Living Zone"),
            (2, "Kitchen", 1, "Living Zone"),
            (3, "Master Bedroom", 2, "Sleeping Zone"),
            (4, "Guest Bedroom", 2, "Sleeping Zone"),
            (5, "Basement Office", 0, "Basement"),
            (6, "Garage", 1, "Basement")
        ]
        conn.executemany("INSERT INTO rooms VALUES (?, ?, ?, ?)", rooms)
        
        # 2. seed devices
        device_types = ["Thermostat", "Smart Plug", "Light Switch", "Camera"]
        models = {
            "Thermostat": ["Nest Learning v3", "Ecobee SmartPremium"],
            "Smart Plug": ["Kasa EP10", "Wemo SmartPlug"],
            "Light Switch": ["Lutron Caseta", "Philips Hue Switch"],
            "Camera": ["Ring Indoor Cam", "Nest Cam IQ"]
        }
        
        devices = []
        for d_id in range(1, num_devices + 1):
            room = random.choice(rooms)
            d_type = random.choice(device_types)
            model = random.choice(models[d_type])
            firmware = f"v{random.randint(1, 4)}.{random.randint(0, 9)}.{random.randint(0, 99)}"
            installed = datetime.now() - timedelta(days=random.randint(10, 300))
            online = random.choice([True, True, True, False])
            devices.append((d_id, room[0], d_type, model, firmware, installed, online))
            
        conn.executemany("INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, ?)", devices)
        
        # 3. seed readings and energy
        thermostats = [d for d in devices if d[2] == "Thermostat"]
        plugs_and_switches = [d for d in devices if d[2] in ["Smart Plug", "Light Switch"]]
        all_online_devices = [d for d in devices if d[6]]
        
        if not thermostats:
            thermostats = [devices[0]]
            
        thermostat_readings = []
        energy_consumption = []
        device_events = []
        
        start_time = datetime.now() - timedelta(days=30)
        
        # Generate readings
        for r_idx in range(1, num_readings + 1):
            timestamp = start_time + timedelta(minutes=random.randint(1, 30 * 24 * 60))
            
            # Thermostat reading
            t_dev = random.choice(thermostats)
            target = random.choice([20.0, 21.0, 22.0, 23.5])
            actual = target + random.uniform(-2.5, 2.5)
            humidity = random.uniform(35.0, 65.0)
            hvac = random.choice(['off', 'heating', 'cooling'])
            thermostat_readings.append((timestamp, t_dev[0], target, actual, humidity, hvac))
            
            # Energy consumption
            e_dev = random.choice(plugs_and_switches) if plugs_and_switches else random.choice(devices)
            watts = random.uniform(5.0, 1500.0) if e_dev[2] == "Smart Plug" else random.uniform(2.0, 60.0)
            voltage = random.choice([118.5, 120.0, 121.2])
            energy_consumption.append((timestamp, e_dev[0], watts, voltage))
            
            # Device events
            if r_idx % 10 == 0:
                ev_dev = random.choice(all_online_devices) if all_online_devices else random.choice(devices)
                ev_type = random.choice(["connection_drop", "firmware_updated", "motion_detected"])
                severity = "warning" if ev_type == "connection_drop" else ("info" if ev_type == "firmware_updated" else "info")
                details = f"Device detected: {ev_type}"
                device_events.append((timestamp, ev_dev[0], ev_type, severity, details))
                
        conn.executemany("INSERT INTO thermostat_readings VALUES (?, ?, ?, ?, ?, ?)", thermostat_readings)
        conn.executemany("INSERT INTO energy_consumption VALUES (?, ?, ?, ?)", energy_consumption)
        if device_events:
            conn.executemany("INSERT INTO device_events VALUES (?, ?, ?, ?, ?)", device_events)

    def seed_logistics_data(conn, num_couriers=100, num_events=500):
        from faker import Faker
        import random
        from datetime import datetime, timedelta
        fake = Faker()
        Faker.seed(42)
        
        # Clear existing data
        conn.execute("DELETE FROM delivery_feedback; DELETE FROM tracking_events; DELETE FROM shipments; DELETE FROM couriers; DELETE FROM depots;")
        
        # 1. seed depots
        depots = [
            (1, "North Regional Sorting Hub", "Amsterdam", 20000),
            (2, "South Regional Sorting Hub", "Eindhoven", 15000),
            (3, "West Port Distribution Center", "Rotterdam", 35000),
            (4, "East Border Gateway", "Enschede", 12000),
            (5, "Central Hub Utrecht", "Utrecht", 50000)
        ]
        conn.executemany("INSERT INTO depots VALUES (?, ?, ?, ?)", depots)
        
        # 2. seed couriers
        v_types = ["Electric Van", "Heavy Truck", "Bicycle"]
        couriers = []
        for c_id in range(1, num_couriers + 1):
            name = fake.name()
            v_type = random.choice(v_types)
            status = random.choices(["active", "on_leave"], weights=[95, 5], k=1)[0]
            couriers.append((c_id, name, v_type, status))
        conn.executemany("INSERT INTO couriers VALUES (?, ?, ?, ?)", couriers)
        
        # 3. seed shipments
        num_shipments = max(10, num_events // 3)
        shipments = []
        cities = ["Amsterdam", "Rotterdam", "Utrecht", "The Hague", "Eindhoven", "Groningen", "Maastricht"]
        for s_id in range(1, num_shipments + 1):
            sender = fake.company()
            city = random.choice(cities)
            weight = round(random.uniform(0.5, 25.0) if random.choice([True, True, False]) else random.uniform(25.0, 500.0), 2)
            created = datetime.now() - timedelta(days=random.randint(1, 45))
            status = random.choices(["delivered", "out_for_delivery", "sorted", "failed"], weights=[85, 8, 5, 2], k=1)[0]
            shipments.append((s_id, sender, city, weight, status, created))
        conn.executemany("INSERT INTO shipments VALUES (?, ?, ?, ?, ?, ?)", shipments)
        
        # 4. seed tracking events
        tracking_events = []
        feedback = []
        te_id = 1
        
        for s in shipments:
            s_id, _, _, _, status, created = s
            depot = random.choice(depots)
            courier = random.choice(couriers)
            
            # Event 1: Created
            t1 = created + timedelta(minutes=random.randint(10, 120))
            tracking_events.append((te_id, s_id, None, None, t1, "manifest_received", "Shipment details uploaded by sender"))
            te_id += 1
            
            # Event 2: Arrived at Depot
            t2 = t1 + timedelta(minutes=random.randint(60, 360))
            tracking_events.append((te_id, s_id, None, depot[0], t2, "arrived_at_depot", f"Arrived at sorting hub {depot[1]}"))
            te_id += 1
            
            # Event 3: Departed Depot
            t3 = t2 + timedelta(minutes=random.randint(120, 720))
            tracking_events.append((te_id, s_id, courier[0], depot[0], t3, "departed_depot", f"Departed sorting hub {depot[1]}"))
            te_id += 1
            
            # Event 4: Terminal Event
            t4 = t3 + timedelta(minutes=random.randint(30, 240))
            if status == "delivered":
                tracking_events.append((te_id, s_id, courier[0], None, t4, "delivered", f"Delivered to recipient by courier {courier[1]}"))
                te_id += 1
                
                # Feedback
                if random.choice([True, False, False]):
                    rating = random.choices([5, 4, 3, 2, 1], weights=[70, 15, 10, 3, 2], k=1)[0]
                    comments = fake.sentence() if rating < 4 else "Great service!"
                    feedback.append((s_id, rating, comments, t4 + timedelta(minutes=random.randint(5, 120))))
            elif status == "failed":
                tracking_events.append((te_id, s_id, courier[0], None, t4, "delivery_attempt_failed", "Recipient not home, delivery re-scheduled"))
                te_id += 1
                
        conn.executemany("INSERT INTO tracking_events VALUES (?, ?, ?, ?, ?, ?, ?)", tracking_events)
        if feedback:
            conn.executemany("INSERT INTO delivery_feedback VALUES (?, ?, ?, ?)", feedback)

    def seed_clickstream_data(conn, num_visitors=100, num_events=500):
        from faker import Faker
        import random
        import uuid
        from datetime import datetime, timedelta
        fake = Faker()
        Faker.seed(42)
        
        # Clear existing data
        conn.execute("DELETE FROM checkout_events; DELETE FROM campaign_clicks; DELETE FROM page_views; DELETE FROM sessions; DELETE FROM visitors;")
        
        # 1. seed visitors
        sources = ["Organic Search", "Google Ads", "Newsletter", "Direct", "Social Media"]
        countries = ["NL", "DE", "BE", "FR", "UK", "US"]
        visitors = []
        campaign_clicks = []
        cc_id = 1
        
        for v_id in range(1, num_visitors + 1):
            cookie = f"cookie_{uuid.uuid4().hex[:12]}"
            source = random.choice(sources)
            country = random.choice(countries)
            created = datetime.now() - timedelta(days=random.randint(10, 60))
            visitors.append((v_id, cookie, source, country, created))
            
            # Optional campaign click
            if source in ["Google Ads", "Newsletter", "Social Media"] and random.choice([True, False]):
                campaign = f"Promo_{random.choice(['Summer', 'BlackFriday', 'Spring'])}_2026"
                medium = 'cpc' if source == "Google Ads" else ('email' if source == "Newsletter" else 'social')
                campaign_clicks.append((cc_id, v_id, campaign, medium, created - timedelta(minutes=random.randint(1, 30))))
                cc_id += 1
                
        conn.executemany("INSERT INTO visitors VALUES (?, ?, ?, ?, ?)", visitors)
        if campaign_clicks:
            conn.executemany("INSERT INTO campaign_clicks VALUES (?, ?, ?, ?, ?)", campaign_clicks)
            
        # 2. seed sessions
        browsers = ["Chrome", "Firefox", "Safari", "Edge"]
        sessions = []
        s_idx = 1
        for v in visitors:
            v_id, _, _, _, v_created = v
            # Some visitors have multiple sessions
            for s_count in range(random.choices([1, 2], weights=[80, 20], k=1)[0]):
                started = v_created + timedelta(days=s_count * random.randint(1, 5), hours=random.randint(0, 23))
                token = f"sess_{uuid.uuid4().hex[:16]}"
                ip = fake.ipv4()
                browser = random.choice(browsers)
                sessions.append((s_idx, v_id, token, ip, browser, started))
                s_idx += 1
        conn.executemany("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)", sessions)
        
        # 3. seed page views and checkout events
        page_views = []
        checkout_events = []
        pv_id = 1
        co_id = 1
        
        paths = ["/home", "/categories", "/products/details", "/cart", "/checkout"]
        
        for sess in sessions:
            sess_id, _, _, _, _, s_time = sess
            
            # Generate click path for the session
            # Bounce sessions only have 1 page view
            is_bounce = random.choice([True, False, False, False]) # 25% bounce rate
            view_count = 1 if is_bounce else random.randint(2, 6)
            
            current_time = s_time
            for page_idx in range(view_count):
                url = paths[page_idx] if page_idx < len(paths) else random.choice(paths)
                load_time = random.choices([random.randint(100, 500), random.randint(500, 1500), random.randint(1500, 4000)], weights=[70, 25, 5], k=1)[0]
                referrer = "https://google.com" if page_idx == 0 else f"https://mystore.localhost{paths[page_idx-1]}"
                page_views.append((pv_id, sess_id, url, referrer, load_time, current_time))
                pv_id += 1
                
                # Advance time between pages
                current_time += timedelta(seconds=random.randint(10, 300))
                
            # If session reached checkout page, generate checkout events
            if not is_bounce and view_count >= 4:
                steps = ['1_view_cart', '2_shipping', '3_payment', '4_success']
                cart_value = round(random.uniform(15.99, 450.0), 2)
                
                # Determine how far they got in the checkout funnel
                funnel_depth = random.choices([1, 2, 3, 4], weights=[10, 15, 15, 60], k=1)[0]
                
                for step_idx in range(funnel_depth):
                    step = steps[step_idx]
                    success = (step_idx == funnel_depth - 1) and (funnel_depth == 4)
                    checkout_events.append((
                        co_id, sess_id, step, cart_value, success, current_time
                    ))
                    co_id += 1
                    current_time += timedelta(seconds=random.randint(30, 120))
                    
        conn.executemany("INSERT INTO page_views VALUES (?, ?, ?, ?, ?, ?)", page_views)
        if checkout_events:
            conn.executemany("INSERT INTO checkout_events VALUES (?, ?, ?, ?, ?, ?)", checkout_events)

    async def trigger_template_seed(template_path, density_value='6500'):
        """Generates a new duckdb database with a sanitised name based on the template file name, attaches it, and populates it."""
        if not template_path or not os.path.exists(template_path):
            ui.notify("Template file not found.", type='negative')
            return
            
        import re
        # Sanitise DB name
        base_name = os.path.splitext(os.path.basename(template_path))[0]
        if base_name.startswith('seed_'):
            base_name = base_name[5:]
            
        # Parse database name from the SQL file if specified as a comment
        db_name_override = None
        if os.path.exists(template_path):
            try:
                with open(template_path, 'r', encoding='utf-8') as f:
                    for _ in range(15):
                        line = f.readline()
                        if not line:
                            break
                        match = re.search(r'--\s*(?:Database Name|Database|DB Name|DB):\s*([a-zA-Z0-9_]+)', line, re.IGNORECASE)
                        if match:
                            db_name_override = match.group(1).strip()
                            break
            except Exception as e:
                print(f"DEBUG: Failed to read template for name override: {e}", flush=True)

        if db_name_override:
            sanitised_name = db_name_override
        else:
            # Convert camelCase/PascalCase or dashes to snake_case
            name_mappings = {
                'noaaweather': 'noaa_weather',
                'nyctaxi': 'nyc_taxi',
                'githubarchive': 'github_archive',
                'openaq': 'open_aq',
                'ns-railway': 'ns_railway',
                'ns_railway': 'ns_railway',
            }
            
            normalized = base_name.lower().replace('_', '').replace('-', '')
            if normalized in name_mappings:
                sanitised_name = name_mappings[normalized]
            else:
                s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', base_name)
                sanitised_name = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower().replace('-', '_')
                sanitised_name = re.sub(r'_+', '_', sanitised_name).strip('_')
        
        # Determine database path
        if os.path.exists('/databases') and os.path.isdir('/databases'):
            db_path = f"/databases/{sanitised_name}.duckdb"
        else:
            if not os.path.exists('databases'):
                os.makedirs('databases', exist_ok=True)
            db_path = f"databases/{sanitised_name}.duckdb"
            
        # Scale parameters according to chosen density
        if density_value == '1000':
            num_entities = 50
            num_facts = 150
            density_label = "Light"
        elif density_value == '300000':
            num_entities = 10000
            num_facts = 300000
            density_label = "Super Dense"
        elif density_value == '1500000':
            num_entities = 50000
            num_facts = 1500000
            density_label = "Ultra Dense"
        elif density_value == '15000':
            num_entities = 500
            num_facts = 2000
            density_label = "Dense"
        else:
            num_entities = 150
            num_facts = 500
            density_label = "Standard"

        ui.notify(f"Creating and seeding database '{sanitised_name}' ({density_label} density)... Please wait.", type='info')
        
        loop = asyncio.get_event_loop()
        def do_template_seeding():
            with open(template_path, 'r', encoding='utf-8') as f:
                full_content = f.read()
                
            parts = full_content.split("-- === SNIPPETS ===")
            schema_sql = parts[0]
            
            # To ensure a clean slate and avoid "Table already exists" errors on re-seeding,
            # detach the database and delete the file if they already exist.
            attached_dbs = [row[0] for row in explorer.conn.execute("PRAGMA show_databases;").fetchall()]
            if sanitised_name in attached_dbs:
                try:
                    explorer.conn.execute(f"DETACH {sanitised_name};")
                except Exception as ex:
                    print(f"DEBUG: Failed to detach {sanitised_name} during cleanup: {ex}", flush=True)
            
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except Exception as ex:
                    print(f"DEBUG: Failed to remove old database file {db_path}: {ex}", flush=True)
            
            # Now attach the fresh database file
            attach_sql = f"ATTACH '{db_path}' AS {sanitised_name};"
            explorer.conn.execute(attach_sql)
                
            # Save it to config
            save_attached_database(sanitised_name, 'duckdb', db_path)
            
            # Run the seeding SQL inside the new database
            current_db_res = explorer.conn.execute("SELECT current_database();").fetchone()
            prev_db = current_db_res[0] if current_db_res else 'main'
            
            explorer.conn.execute(f"USE {sanitised_name};")
            try:
                explorer.conn.execute(schema_sql)
                
                # Check and run custom python mock data seeding with scaled parameters
                if sanitised_name == 'car_rental':
                    seed_car_rental_data(explorer.conn, num_customers=num_entities, num_reservations=num_facts)
                elif sanitised_name == 'e_commerce':
                    seed_e_commerce_data(explorer.conn, num_users=num_entities, num_orders=num_facts)
                elif sanitised_name == 'smart_home':
                    seed_smart_home_data(explorer.conn, num_devices=num_entities, num_readings=num_facts)
                elif sanitised_name == 'logistics':
                    seed_logistics_data(explorer.conn, num_couriers=num_entities, num_events=num_facts)
                elif sanitised_name == 'clickstream':
                    seed_clickstream_data(explorer.conn, num_visitors=num_entities, num_events=num_facts)
            finally:
                explorer.conn.execute(f"USE {prev_db};")
                
            # Parse and deploy snippets if present
            if len(parts) > 1:
                import re
                import uuid
                snippets_part = parts[1]
                snippet_blocks = re.findall(r'-- === SNIPPET START ===(.*?)-- === SNIPPET END ===', snippets_part, re.DOTALL)
                for block in snippet_blocks:
                    lines = block.strip().split('\n')
                    name = "Unnamed Snippet"
                    description = ""
                    sql_lines = []
                    
                    for line in lines:
                        if line.startswith('-- Name:'):
                            name = line[8:].strip()
                        elif line.startswith('-- Description:'):
                            description = line[15:].strip()
                        else:
                            sql_lines.append(line)
                            
                    sql_code = '\n'.join(sql_lines).strip()
                    
                    # Store in _duckdb_studio_saved_queries
                    try:
                        config_db.execute("DELETE FROM _duckdb_studio_saved_queries WHERE name = ?", (name,))
                        config_db.execute(
                            "INSERT INTO _duckdb_studio_saved_queries (id, name, description, sql_code, created_at, category) VALUES (?, ?, ?, ?, ?, ?)",
                            (str(uuid.uuid4()), name, description, sql_code, datetime.now().isoformat(), "Analytical")
                        )
                    except Exception as ex_db:
                        print(f"DEBUG: Failed to save template snippet to SQLite: {ex_db}", flush=True)
                
        try:
            await loop.run_in_executor(None, do_template_seeding)
            ui.notify(f"Successfully created, attached, and seeded database '{sanitised_name}' ({density_label} density)!", type='positive')
            refresh_schema_tree()
            try:
                refresh_saved_queries_list()
            except Exception:
                pass
        except Exception as e:
            ui.notify(f"Failed to seed database: {e}", type='negative', duration=7)


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

    def delete_history_item(hist_id):
        try:
            config_db.execute("DELETE FROM _duckdb_studio_query_history WHERE id = ?;", [hist_id])
            ui.notify("History item deleted", type='info')
            update_query_history_list()
        except Exception as e:
            ui.notify(f"Failed to delete history item: {e}", type='negative')

    def clear_all_history():
        try:
            config_db.execute("DELETE FROM _duckdb_studio_query_history;")
            ui.notify("Query history cleared", type='info')
            update_query_history_list()
        except Exception as e:
            ui.notify(f"Failed to clear query history: {e}", type='negative')

    def update_query_history_list():
        """Repopulate the query history list from the SQLite database."""
        try:
            rows = config_db.query_all("""
                SELECT id, query_text, timestamp, duration_ms, status, rows_count, error_message 
                FROM _duckdb_studio_query_history 
                ORDER BY timestamp DESC 
                LIMIT 50;
            """)
        except Exception as e:
            print(f"Failed to fetch query history: {e}")
            rows = []
            
        history_container.clear()
        with history_container:
            if not rows:
                with ui.column().classes('w-full items-center justify-center py-6 text-slate-400 dark:text-slate-500'):
                    ui.icon('history', size='lg')
                    ui.label('No query history recorded yet').classes('text-xs mt-1')
                return
                
            for r in rows:
                q_text = r['query_text']
                dur = r['duration_ms']
                status = r['status']
                ts = r['timestamp'].replace('T', ' ')[:19]
                rows_count = r['rows_count']
                err_msg = r['error_message']
                hist_id = r['id']
                
                is_success = status == 'SUCCESS'
                icon_name = 'check_circle' if is_success else 'cancel'
                icon_color = 'emerald' if is_success else 'rose'
                bg_color = 'hover:bg-slate-50 dark:hover:bg-slate-900'
                
                def make_restore_handler(s=q_text):
                    return lambda _: load_history_query(s)
                
                with ui.card().classes(f'w-full p-3 border rounded shadow-none {bg_color} transition flex-col gap-2'):
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        with ui.row().classes('items-center gap-2 flex-grow min-w-0'):
                            ui.icon(icon_name, color=icon_color).classes('text-lg flex-none')
                            ui.label(ts).classes('text-xs text-slate-400 font-mono flex-none')
                            ui.label(f"{dur}ms").classes('text-xs text-indigo-500 font-mono flex-none')
                            if is_success:
                                ui.label(f"{rows_count:,} rows" if rows_count is not None else "0 rows").classes('text-xs text-emerald-600 font-mono flex-none')
                            else:
                                ui.label("Failed").classes('text-xs text-rose-500 font-mono flex-none')
                        
                        with ui.row().classes('items-center gap-1 flex-none'):
                            # Clipboard copy
                            ui.button(icon='content_copy', on_click=lambda _, q=q_text: [ui.clipboard.write(q), ui.notify('Copied to clipboard!', type='info')]).props('flat dense size=sm round color=slate')
                            # Delete entry
                            ui.button(icon='delete', on_click=lambda _, i=hist_id: delete_history_item(i)).props('flat dense size=sm round color=rose')
                    
                    with ui.row().classes('w-full justify-between items-center cursor-pointer no-wrap').on('click', make_restore_handler()):
                        ui.label(q_text).classes('text-xs font-mono truncate flex-grow text-slate-700 dark:text-slate-300').style('max-width: 90%;')
                        ui.icon('keyboard_arrow_right', color='slate').classes('flex-none')
                    
                    if not is_success and err_msg:
                        ui.label(f"Error: {err_msg}").classes('text-xs font-mono text-rose-600 dark:text-rose-400 break-all p-1.5 bg-rose-50 dark:bg-rose-950/30 rounded w-full')

    def load_history_query(sql_str):
        sql_editor.value = sql_str
        ui.notify("Query loaded to editor.", type='info')

    def run_editor_query():
        """Execute the SQL code in the editor, and update tables, statistics, and graphs."""
        sql = sql_editor.value.strip()
        if not sql:
            ui.notify('Please write a query first!', type='warning')
            return
            
        # Prevent detaching, renaming, or overriding primary database from within SQL Editor
        import re
        sql_clean = sql.strip().lower()
        if re.search(r"\bdetach\s+(?:database\s+)?['\"]?(?:main|starter)['\"]?\b", sql_clean):
            ui.notify("Security Policy: Detaching the primary database is not allowed!", type='negative')
            status_label.text = "Error: Detaching primary database is blocked by policy."
            return
            
        if re.search(r"\balter\s+database\s+['\"]?(?:main|starter)['\"]?\s+rename\s+to\b", sql_clean):
            ui.notify("Security Policy: Renaming the primary database is not allowed!", type='negative')
            status_label.text = "Error: Renaming primary database is blocked by policy."
            return

        if re.search(r"\battach\s+.*?\bas\s+['\"]?(?:main|starter)['\"]?\b", sql_clean):
            ui.notify("Security Policy: Overriding the 'main' or 'starter' database name is not allowed!", type='negative')
            status_label.text = "Error: Overriding primary database name is blocked by policy."
            return
            

            
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
        
        # Resolve parameters if any
        params = detect_parameters(sql)
        if params:
            empty_params = [p for p in params if p not in parameter_input_fields or parameter_input_fields[p].value is None or str(parameter_input_fields[p].value).strip() == ""]
            if empty_params:
                ui.notify(f"Please fill in all query parameters: {', '.join(empty_params)}", type='warning')
                status_label.text = "Execution halted: Missing parameter values."
                return
            param_values = {p: parameter_input_fields[p].value for p in params}
            sql_exec = substitute_sql_parameters(sql, param_values)
        else:
            sql_exec = sql
        print(f"DEBUG EXECUTING SQL: {sql_exec}", flush=True)

        # Run the query in DuckDB
        res = explorer.query(sql_exec)
        
        # Log to Persistent SQLite Query History
        try:
            import uuid
            hist_id = str(uuid.uuid4())
            import datetime
            ts = datetime.datetime.now().isoformat()
            dur = res.get('duration_ms', 0)
            status = 'ERROR' if 'error' in res else 'SUCCESS'
            rows_cnt = res.get('affected_rows', 0) if 'error' not in res else None
            err_msg = res.get('error', None) if 'error' in res else None
            
            config_db.execute("""
                INSERT INTO _duckdb_studio_query_history (id, query_text, timestamp, duration_ms, status, rows_count, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, [hist_id, sql_exec, ts, dur, status, rows_cnt, err_msg])
            
            update_query_history_list()
        except Exception as he:
            print(f"Failed to log query history: {he}")
        
        # Track query latency for Telemetry Dashboard
        import datetime
        query_latency_history.append({
            'timestamp': datetime.datetime.now().strftime('%H:%M:%S'),
            'latency': res.get('duration_ms', 0),
            'query': sql_exec[:40] + ('...' if len(sql_exec) > 40 else ''),
            'success': 'error' not in res
        })
        if len(query_latency_history) > 15:
            query_latency_history.pop(0)
        
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

        # Force refresh of schema, databases tree, and ER Diagram if DDL or attach/detach/use statements are executed
        sql_lower = sql.lower().strip()
        if any(keyword in sql_lower for keyword in ["attach ", "detach ", "create ", "drop ", "alter ", "use "]):
            refresh_schema_tree()
            try:
                refresh_er_diagram()
            except Exception:
                pass

        # Display success metrics
        if res.get('truncated', False):
            status_label.text = f"Completed in {res['duration_ms']}ms | Rows: {res['affected_rows']}+ (Truncated to 10,000)"
            ui.notify('Results truncated to first 10,000 rows for performance.', type='warning', duration=5)
        else:
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
        """Trigger native high-performance DuckDB CSV export and browser download."""
        sql = sql_editor.value.strip()
        if not sql:
            ui.notify('Please write a query first!', type='warning')
            return
            
        try:
            params = detect_parameters(sql)
            if params:
                empty_params = [p for p in params if p not in parameter_input_fields or parameter_input_fields[p].value is None or str(parameter_input_fields[p].value).strip() == ""]
                if empty_params:
                    ui.notify(f"Please fill in all query parameters: {', '.join(empty_params)}", type='warning')
                    return
                param_values = {p: parameter_input_fields[p].value for p in params}
                sql_exec = substitute_sql_parameters(sql, param_values)
            else:
                sql_exec = sql

            # Clean trailing semicolon
            sql_clean = sql_exec.strip()
            if sql_clean.endswith(';'):
                sql_clean = sql_clean[:-1].strip()

            os.makedirs('exports', exist_ok=True)
            import uuid
            temp_path = f"exports/export_{uuid.uuid4().hex}.csv"

            # Execute native COPY command
            copy_query = f"COPY ({sql_clean}) TO '{temp_path}' (FORMAT CSV, HEADER TRUE);"
            explorer.conn.execute(copy_query)

            # Trigger standard browser download
            ui.download(temp_path, f"duckdb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
            ui.notify('CSV Export completed successfully.', type='success')

            # Schedule garbage collection cleanup for the temporary file
            def cleanup():
                import time
                time.sleep(15)
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass
            import threading
            threading.Thread(target=cleanup, daemon=True).start()

        except Exception as ex:
            ui.notify(f"CSV Export failed: {ex}", type='negative', duration=7)

    def trigger_parquet_download():
        """Trigger native high-performance DuckDB Parquet export and browser download."""
        sql = sql_editor.value.strip()
        if not sql:
            ui.notify('Please write a query first!', type='warning')
            return
            
        try:
            params = detect_parameters(sql)
            if params:
                empty_params = [p for p in params if p not in parameter_input_fields or parameter_input_fields[p].value is None or str(parameter_input_fields[p].value).strip() == ""]
                if empty_params:
                    ui.notify(f"Please fill in all query parameters: {', '.join(empty_params)}", type='warning')
                    return
                param_values = {p: parameter_input_fields[p].value for p in params}
                sql_exec = substitute_sql_parameters(sql, param_values)
            else:
                sql_exec = sql

            # Clean trailing semicolon
            sql_clean = sql_exec.strip()
            if sql_clean.endswith(';'):
                sql_clean = sql_clean[:-1].strip()

            os.makedirs('exports', exist_ok=True)
            import uuid
            temp_path = f"exports/export_{uuid.uuid4().hex}.parquet"

            # Execute native COPY command
            copy_query = f"COPY ({sql_clean}) TO '{temp_path}' (FORMAT PARQUET);"
            explorer.conn.execute(copy_query)

            # Trigger standard browser download
            ui.download(temp_path, f"duckdb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.parquet")
            ui.notify('Parquet Export completed successfully.', type='success')

            # Schedule garbage collection cleanup for the temporary file
            def cleanup():
                import time
                time.sleep(15)
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass
            import threading
            threading.Thread(target=cleanup, daemon=True).start()

        except Exception as ex:
            ui.notify(f"Parquet Export failed: {ex}", type='negative', duration=7)

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
                    
                nodes.append(f'{node_id}["{label}"]:::{cls}')
                
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
        sql_raw = sql_editor.value.strip()
        if not sql_raw:
            ui.notify('Please write a query first!', type='warning')
            return
            
        params = detect_parameters(sql_raw)
        if params:
            param_values = {p: parameter_input_fields[p].value for p in params if p in parameter_input_fields}
            sql = substitute_sql_parameters(sql_raw, param_values)
        else:
            sql = sql_raw
        
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
            with ui.row().classes('w-full gap-2 flex-nowrap justify-between'):
                # Card 1: Mode
                with ui.card().classes('flex-grow p-2.5 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                    ui.icon('settings', color='primary').classes('text-lg')
                    ui.label('Execution Mode').classes('text-[10px] text-slate-400 font-semibold uppercase')
                    mode_str = "Execution Profile" if is_analyze else "Logical Plan"
                    ui.label(mode_str).classes('text-xs font-bold text-slate-800 dark:text-white text-center')
                
                # Card 2: Duration
                with ui.card().classes('flex-grow p-2.5 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                    ui.icon('timer', color='secondary').classes('text-lg')
                    ui.label('Total Time').classes('text-[10px] text-slate-400 font-semibold uppercase')
                    time_str = total_time if total_time else "N/A"
                    ui.label(time_str).classes('text-xs font-bold text-slate-800 dark:text-white')
                    
                # Card 3: Key Operators Count
                with ui.card().classes('flex-grow p-2.5 border border-slate-100 dark:border-slate-800 shadow-none dark-bg-panel items-center gap-1'):
                    ui.icon('analytics', color='positive').classes('text-lg')
                    ui.label('Operators Detected').classes('text-[10px] text-slate-400 font-semibold uppercase')
                    op_list = []
                    if seq_scans: op_list.append(f"Scans: {seq_scans}")
                    if hash_joins: op_list.append(f"Joins: {hash_joins}")
                    if sorts: op_list.append(f"Sorts: {sorts}")
                    op_str = ", ".join(op_list) if op_list else "None"
                    ui.label(op_str).classes('text-xs font-bold text-slate-800 dark:text-white text-center')
            
            # Suggestions Section
            if suggestions:
                with ui.card().classes('w-full p-2.5 border border-indigo-100 dark:border-indigo-900 bg-indigo-50/50 dark:bg-indigo-950/20 shadow-none gap-1'):
                    with ui.row().classes('items-center gap-1.5 text-indigo-600 dark:text-indigo-400'):
                        ui.icon('lightbulb', size='xs')
                        ui.label('Optimization Insights & Tips').classes('font-bold text-xs')
                    ui.separator().classes('opacity-50')
                    for op_type, text in suggestions:
                        with ui.row().classes('items-start gap-1.5 py-0.5 no-wrap'):
                            ui.icon('chevron_right', size='xs', color='indigo').classes('mt-0.5')
                            ui.markdown(f"**{op_type}**: {text}").classes('text-[11px] text-slate-700 dark:text-slate-300')
                            
            # Visual Tree Section Header
            with ui.row().classes('w-full justify-between items-center px-1 pt-1'):
                ui.label('Visual Execution Plan:').classes('text-xs font-bold text-slate-700 dark:text-slate-300')
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
    
    current_uploaded_path = ""
    current_file_ext = ""
    column_mapping_rows = []

    async def handle_upload_and_sniff(e):
        nonlocal current_uploaded_path, current_file_ext
        
        temp_dir = 'scratch'
        os.makedirs(temp_dir, exist_ok=True)
        current_file_ext = os.path.splitext(e.file.name)[1].lower()
        current_uploaded_path = os.path.join(temp_dir, f"temp_upload_{int(time.time())}{current_file_ext}")
        
        try:
            # Read file bytes and write to temp path
            content_bytes = await e.file.read()
            with open(current_uploaded_path, 'wb') as f:
                f.write(content_bytes)
            
            # Sniff CSV options or detect schemas
            if current_file_ext == '.parquet':
                sniff_query = f"DESCRIBE SELECT * FROM read_parquet('{current_uploaded_path}') LIMIT 0;"
                preview_query = f"SELECT * FROM read_parquet('{current_uploaded_path}') LIMIT 5;"
                csv_options_expansion.visible = False
            elif current_file_ext in ('.json', '.ndjson'):
                sniff_query = f"DESCRIBE SELECT * FROM read_json_auto('{current_uploaded_path}') LIMIT 0;"
                preview_query = f"SELECT * FROM read_json_auto('{current_uploaded_path}') LIMIT 5;"
                csv_options_expansion.visible = False
            else:
                sniff_query = f"DESCRIBE SELECT * FROM read_csv_auto('{current_uploaded_path}') LIMIT 0;"
                preview_query = f"SELECT * FROM read_csv_auto('{current_uploaded_path}') LIMIT 5;"
                csv_options_expansion.visible = True
            csv_options_expansion.update()
            
            # Retrieve column metadata
            cols_meta = explorer.conn.execute(sniff_query).fetchall()
            
            # Fetch preview rows
            preview_data = explorer.conn.execute(preview_query).fetchall()
            preview_cols = [x[0] for x in explorer.conn.execute(sniff_query).description]
            
            # Sanitize suggested table name
            tbl_name = os.path.splitext(e.file.name)[0]
            tbl_name = "".join([c if c.isalnum() else "_" for c in tbl_name]).strip("_").lower()
            table_name_input.value = tbl_name
            table_name_input.update()
            
            # Render column mappings dynamically
            mapping_grid_container.clear()
            column_mapping_rows.clear()
            with mapping_grid_container:
                with ui.row().classes('w-full font-bold text-xs text-slate-500 border-b pb-1 gap-4 items-center no-wrap'):
                    ui.label('Import').classes('w-12')
                    ui.label('Source Column').classes('flex-grow min-w-[150px]')
                    ui.label('Destination Name').classes('flex-grow min-w-[150px]')
                    ui.label('Type').classes('w-32')
                    
                for col in cols_meta:
                    col_name = col[0]
                    col_type = col[1]
                    with ui.row().classes('w-full gap-4 items-center text-xs no-wrap'):
                        active_cb = ui.checkbox(value=True).classes('w-12')
                        ui.label(col_name).classes('flex-grow min-w-[150px] truncate font-mono')
                        dest_in = ui.input(value=col_name).props('outlined dense').classes('flex-grow min-w-[150px]')
                        type_sel = ui.select(
                            options={
                                col_type: col_type,
                                'VARCHAR': 'VARCHAR',
                                'INTEGER': 'INTEGER',
                                'BIGINT': 'BIGINT',
                                'DOUBLE': 'DOUBLE',
                                'TIMESTAMP': 'TIMESTAMP',
                                'DATE': 'DATE',
                                'BOOLEAN': 'BOOLEAN'
                            },
                            value=col_type
                        ).props('outlined dense').classes('w-32')
                        
                        column_mapping_rows.append({
                            'active': active_cb,
                            'src': col_name,
                            'dest': dest_in,
                            'type': type_sel
                        })
            mapping_grid_container.update()
            
            # Render preview table
            preview_table_container.clear()
            columns_def = [{'name': c, 'label': c, 'field': c, 'align': 'left'} for c in preview_cols]
            rows_def = []
            for row in preview_data:
                row_dict = {}
                for idx, val in enumerate(row):
                    row_dict[preview_cols[idx]] = str(val) if val is not None else ''
                rows_def.append(row_dict)
                
            with preview_table_container:
                ui.table(columns=columns_def, rows=rows_def, row_key=preview_cols[0] if preview_cols else 'id').props('dense flat bordered').classes('w-full text-xs')
            preview_table_container.update()
            
            set_step(2)
            
        except Exception as sniff_ex:
            ui.notify(f"Failed to parse file layout: {sniff_ex}", type='negative')

    async def trigger_import():
        nonlocal current_uploaded_path, current_file_ext
        target_db = target_db_select.value
        target_schema = target_schema_select.value
        tbl_name = table_name_input.value.strip()
        if not tbl_name:
            ui.notify('Please enter a valid table name!', type='warning')
            return
            
        fq_name = f"{target_db}.{target_schema}.{tbl_name}"
        policy = collision_select.value
        mode = import_mode_select.value
        is_external = (mode == 'external')
        
        # Check collisions
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
                ui.notify(f"Table/View '{tbl_name}' already exists in {target_db}.{target_schema}!", type='warning')
                return
            elif policy == 'replace':
                explorer.conn.execute(f"DROP VIEW IF EXISTS {fq_name};")
                explorer.conn.execute(f"DROP TABLE IF EXISTS {fq_name};")
        
        # Construct the query based on mapped columns
        select_cols = []
        for row in column_mapping_rows:
            if row['active'].value:
                src_escaped = f'"{row["src"]}"'
                dest_escaped = f'"{row["dest"].value.strip()}"'
                dtype = row['type'].value
                select_cols.append(f"CAST({src_escaped} AS {dtype}) AS {dest_escaped}")
                
        if not select_cols:
            ui.notify("Please select at least one column to import!", type='warning')
            return
            
        cols_clause = ", ".join(select_cols)
        
        # Determine correct read function
        if current_file_ext == '.parquet':
            read_func = f"read_parquet('{current_uploaded_path}')"
        elif current_file_ext in ('.json', '.ndjson'):
            read_func = f"read_json_auto('{current_uploaded_path}')"
        else: # CSV
            delim = delimiter_select.value
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
                
            delim_opt = ""
            if extra_opts:
                delim_opt = ", " + ", ".join(extra_opts)
            read_func = f"read_csv_auto('{current_uploaded_path}'{delim_opt})"
            
        # Ingestion Query
        if is_external:
            persistent_path = os.path.join('scratch', f"{tbl_name}{current_file_ext}")
            try:
                if os.path.exists(persistent_path):
                    os.remove(persistent_path)
                os.rename(current_uploaded_path, persistent_path)
                current_uploaded_path = persistent_path
            except Exception as rename_ex:
                print(f"Rename failed: {rename_ex}")
            
            read_func = read_func.replace(current_uploaded_path, persistent_path)
            ingest_sql = f"CREATE VIEW {fq_name} AS SELECT {cols_clause} FROM {read_func};"
        else:
            if exists and policy == 'append':
                ingest_sql = f"INSERT INTO {fq_name} SELECT {cols_clause} FROM {read_func};"
            else:
                ingest_sql = f"CREATE TABLE {fq_name} AS SELECT {cols_clause} FROM {read_func};"
                
        try:
            start_time = time.time()
            explorer.conn.execute(ingest_sql)
            explorer.conn.commit()
            duration = time.time() - start_time
            
            try:
                imported_count = explorer.conn.execute(f"SELECT COUNT(*) FROM {fq_name}").fetchone()[0]
            except Exception:
                imported_count = "N/A"
                
            success_metrics_label.text = f"Successfully imported {imported_count} rows in {duration:.2f} seconds."
            success_query_action_btn.on_click(lambda: auto_query_imported(fq_name))
            set_step(3)
            
            if not is_external and os.path.exists(current_uploaded_path):
                try:
                    os.remove(current_uploaded_path)
                except Exception:
                    pass
                    
        except Exception as ingest_ex:
            ui.notify(f"Ingestion failed: {ingest_ex}", type='negative', duration=7)

    def auto_query_imported(fq_name):
        import_dialog.close()
        refresh_schema_tree()
        populate_builder_tables()
        sql_editor.value = f"SELECT * FROM {fq_name} LIMIT 100;"
        run_editor_query()

    def handle_import_mode_change(e):
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
            if 'main' in dbs:
                target_db_select.value = 'main'
            elif 'starter' in dbs:
                target_db_select.value = 'starter'
            else:
                target_db_select.value = dbs[0] if dbs else 'main'
            target_db_select.update()
            
            update_import_schemas(target_db_select.value)
            uploader.reset()
            set_step(1)
        except Exception as e:
            print(f"Error loading databases for import dialog: {e}")
        import_dialog.open()

    # Table stats inspector overlay dialog
    with ui.dialog() as inspector_dialog, ui.card().classes('w-[900px] max-w-[95vw] p-6 gap-4 border border-slate-100 dark:border-slate-800 rounded-xl dark-bg-flat'):
        inspector_content = ui.column().classes('w-full gap-4')

    # Build Modal Ingestion Dialog
    with ui.dialog() as import_dialog, ui.card().classes('w-[900px] max-w-[95vw] p-6 gap-4 border border-slate-100 dark:border-slate-800 rounded-xl dark-bg-flat'):
        
        def set_step(step_num):
            step_1_container.visible = (step_num == 1)
            step_2_container.visible = (step_num == 2)
            step_3_container.visible = (step_num == 3)
            step_1_container.update()
            step_2_container.update()
            step_3_container.update()

        # Step 1 Container
        step_1_container = ui.column().classes('w-full gap-4')
        with step_1_container:
            ui.label('📥 Drag-and-Drop Data Import Wizard').classes('text-xl font-bold text-slate-800 dark:text-white')
            ui.label('Upload a local CSV, Parquet, or JSON file to parse, customize, and ingest into the database.').classes('text-xs text-slate-500 -mt-2')
            
            with ui.row().classes('w-full gap-4'):
                target_db_select = ui.select(
                    options={'main': 'main'},
                    value='main',
                    label='Target Database',
                    on_change=lambda e: update_import_schemas(e.value)
                ).props('outlined dense').classes('flex-grow')
                
                target_schema_select = ui.select(
                    options={'main': 'main'},
                    value='main',
                    label='Target Schema'
                ).props('outlined dense').classes('flex-grow')

            uploader = ui.upload(
                label='Drag & Drop file here, or click to browse',
                auto_upload=True,
                max_files=1,
                on_upload=lambda e: handle_upload_and_sniff(e)
            ).props('outlined dense accept=".csv,.tsv,.parquet,.json,.ndjson"').classes('w-full custom-dropzone h-48 justify-center border-2 border-dashed border-indigo-200 dark:border-slate-800 rounded-xl p-4')
            
            with ui.row().classes('w-full justify-end pt-2'):
                ui.button('Cancel', on_click=import_dialog.close).props('flat')

        # Step 2 Container
        step_2_container = ui.column().classes('w-full gap-4')
        with step_2_container:
            ui.label('⚙️ Configure Table Schema & Preview').classes('text-lg font-bold text-slate-800 dark:text-white')
            
            with ui.row().classes('w-full gap-4 items-center'):
                table_name_input = ui.input('Table Name', placeholder='Suggested automatically').props('outlined dense').classes('flex-grow')
                import_mode_select = ui.select(
                    options={
                        'table': 'Import as Physical Table',
                        'external': 'Import as External Table (View)'
                    },
                    value='table',
                    label='Import Mode',
                    on_change=lambda e: handle_import_mode_change(e)
                ).props('outlined dense').classes('flex-grow')
                
                collision_select = ui.select(
                    options={
                        'fail': 'Fail if table exists',
                        'replace': 'Replace table (Overwrite)',
                        'append': 'Append rows to existing'
                    },
                    value='fail',
                    label='Collision Policy'
                ).props('outlined dense').classes('flex-grow')

            csv_options_expansion = ui.expansion('🔧 CSV Parsing Options', icon='tune').classes('w-full border border-slate-200 dark:border-slate-800 rounded-md text-xs')
            with csv_options_expansion:
                with ui.column().classes('p-3 gap-2 w-full'):
                    with ui.row().classes('w-full gap-4'):
                        delimiter_select = ui.select(
                            options={
                                'auto': 'Auto-detect Delimiter',
                                ',': 'Comma ( , )',
                                ';': 'Semicolon ( ; )',
                                '\t': 'Tab ( \\t )',
                                '|': 'Pipe ( | )'
                            },
                            value='auto',
                            label='Delimiter'
                        ).props('outlined dense').classes('flex-grow')
                        
                        all_varchar_checkbox = ui.checkbox('Load all as text', value=False).classes('text-xs')
                        ignore_errors_checkbox = ui.checkbox('Ignore errors', value=False).classes('text-xs')
                    with ui.row().classes('w-full gap-4'):
                        null_padding_checkbox = ui.checkbox('Null padding', value=False).classes('text-xs')
                        strict_mode_checkbox = ui.checkbox('Strict mode', value=True).classes('text-xs')

            ui.label('Column Mapping').classes('text-sm font-bold text-slate-700 dark:text-slate-300 mt-2')
            mapping_grid_container = ui.column().classes('w-full gap-2 border p-3 rounded-lg dark:border-slate-800 max-h-48 overflow-y-auto')

            ui.label('Data Preview (First 5 rows)').classes('text-sm font-bold text-slate-700 dark:text-slate-300 mt-2')
            preview_table_container = ui.column().classes('w-full overflow-x-auto')

            with ui.row().classes('w-full justify-between pt-2'):
                ui.button('Back', icon='arrow_back', on_click=lambda: set_step(1)).props('flat')
                ui.button('Ingest Data', icon='bolt', color='primary', on_click=lambda: trigger_import()).props('elevated')

        # Step 3 Container
        step_3_container = ui.column().classes('w-full gap-4 items-center justify-center p-6')
        with step_3_container:
            ui.icon('check_circle', color='emerald', size='lg').classes('text-5xl')
            ui.label('Data Ingestion Complete!').classes('text-xl font-bold text-slate-800 dark:text-white')
            success_metrics_label = ui.label('').classes('text-sm text-slate-600 dark:text-slate-400')
            
            with ui.row().classes('gap-4 mt-4'):
                success_query_action_btn = ui.button('Query Table in Editor', icon='search', color='primary').props('elevated')
                ui.button('Close Wizard', on_click=import_dialog.close).props('flat')

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

    # Build Drop Table/View Confirmation Dialog
    with ui.dialog() as drop_table_dialog, ui.card().classes('p-6 gap-4'):
        drop_title_label = ui.label("").classes('font-bold text-base')
        ui.label("This action will drop the table/view and delete all its data permanently. This cannot be undone.").classes('text-xs text-slate-500')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancel', on_click=drop_table_dialog.close).props('flat')
            
            def perform_drop_action():
                nonlocal drop_target_db, drop_target_schema, drop_target_table
                try:
                    explorer.conn.execute(f"DROP TABLE IF EXISTS {drop_target_db}.{drop_target_schema}.{drop_target_table};")
                    explorer.conn.execute(f"DROP VIEW IF EXISTS {drop_target_db}.{drop_target_schema}.{drop_target_table};")
                    ui.notify(f"Successfully dropped table/view '{drop_target_table}'", type='success')
                    refresh_schema_tree()
                    drop_table_dialog.close()
                except Exception as ex:
                    ui.notify(f"Failed to drop table/view: {ex}", type='negative')
                    
            ui.button('Confirm Drop', color='negative', on_click=perform_drop_action).props('elevated')

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
        
        with ui.row().classes('w-full gap-2 items-center flex-nowrap'):
            attach_path_input = ui.input(
                label='Path or Connection String',
                placeholder='e.g., /databases/my_db.duckdb or host=localhost ...'
            ).props('outlined dense').classes('flex-grow')
            
            async def select_attach_file():
                start_dir = '/databases' if os.path.exists('/databases') else os.path.abspath('.')
                picker = local_file_picker(start_dir, upper_limit=None, multiple=False)
                res = await picker
                if res:
                    attach_path_input.set_value(res[0])
                    
            ui.button(icon='folder_open', on_click=select_attach_file).props('dense outline').classes('p-2 q-mt-md').tooltip('Browse database files')
        
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
                
            if alias in ('main', 'starter'):
                ui.notify("Cannot use 'main' or 'starter' as database alias!", type='warning')
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

    # Build Rename Database Dialog
    with ui.dialog() as rename_db_dialog, ui.card().classes('w-96 p-6 gap-4'):
        ui.label('✏️ Rename Connection Alias').classes('text-lg font-bold text-slate-800 dark:text-white')
        ui.label('This will detach the database and re-attach it under the new name.').classes('text-xs text-slate-500 -mt-2')
        
        rename_alias_input = ui.input('New Database Alias', placeholder='e.g., new_alias_name').props('outlined dense').classes('w-full')
        
        async def handle_rename_db():
            nonlocal rename_target_old_name, rename_target_path
            new_alias = rename_alias_input.value.strip()
            if not new_alias:
                ui.notify('Please provide a new alias!', type='warning')
                return
                
            # Clean alias
            new_alias = "".join([c if c.isalnum() else "_" for c in new_alias]).strip("_").lower()
            if not new_alias:
                ui.notify('Please provide a valid alphanumeric alias!', type='warning')
                return
                
            if new_alias == rename_target_old_name:
                ui.notify('New alias is the same as the old alias!', type='warning')
                return

            if rename_target_old_name in ('main', 'starter'):
                ui.notify("Cannot rename the primary database!", type='warning')
                return

            if new_alias in ('main', 'starter'):
                ui.notify("Cannot rename connection alias to 'main' or 'starter'!", type='warning')
                return
                
            try:
                # 1. Look up the database type in attached_databases.yaml
                config_path = get_config_path()
                db_type = 'duckdb'
                data_path = None
                
                if os.path.exists(config_path):
                    import yaml
                    try:
                        with open(config_path, 'r') as f:
                            cfg = yaml.safe_load(f)
                            if cfg and 'databases' in cfg:
                                for db in cfg['databases']:
                                    if db.get('name') == rename_target_old_name:
                                        db_type = db.get('type', 'duckdb')
                                        if db_type == 'ducklake' and 'options' in db:
                                            data_path = db['options'].get('data_path')
                                        break
                    except Exception as e:
                        print(f"Error reading config during rename lookup: {e}")
                
                # 2. Detach old database
                explorer.conn.execute("USE main;")
                explorer.conn.execute(f"DETACH {rename_target_old_name};")
                remove_attached_database(rename_target_old_name)
                
                # 3. Construct ATTACH command under new alias
                if db_type == 'ducklake':
                    sql = f"ATTACH 'ducklake:{rename_target_path}' AS {new_alias}"
                    if data_path:
                        sql += f" (DATA_PATH '{data_path}')"
                    sql += ";"
                elif db_type == 'sqlite':
                    sql = f"ATTACH '{rename_target_path}' AS {new_alias} (TYPE sqlite);"
                elif db_type == 'postgres':
                    sql = f"ATTACH '{rename_target_path}' AS {new_alias} (TYPE postgres);"
                elif db_type == 'mysql':
                    sql = f"ATTACH '{rename_target_path}' AS {new_alias} (TYPE mysql);"
                else: # duckdb
                    sql = f"ATTACH '{rename_target_path}' AS {new_alias};"
                    
                # 4. Execute ATTACH
                explorer.conn.execute(sql)
                
                # 5. Save to YAML config
                save_attached_database(new_alias, db_type, rename_target_path, data_path)
                
                ui.notify(f"Successfully renamed database connection to '{new_alias}'!", type='success')
                rename_db_dialog.close()
                refresh_schema_tree()
            except Exception as ex:
                ui.notify(f"Failed to rename database: {str(ex)}", type='negative', duration=7)
                
        with ui.row().classes('w-full justify-end gap-2 pt-2'):
            ui.button('Cancel', on_click=rename_db_dialog.close).props('flat')
            ui.button('Rename', icon='edit', color='primary', on_click=handle_rename_db).props('elevated')

    def open_rename_dialog(old_name, db_path):
        if old_name in ('main', 'starter'):
            ui.notify("Cannot rename the primary database!", type='warning')
            return
        nonlocal rename_target_old_name, rename_target_path
        rename_target_old_name = old_name
        rename_target_path = db_path
        rename_alias_input.value = old_name
        rename_db_dialog.open()

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
                    
                # Split trailing clauses (GROUP BY, HAVING, ORDER BY, LIMIT, OFFSET)
                sql, trailing = split_sql_trailing_clauses(sql, keywords=['GROUP BY', 'HAVING', 'ORDER BY', 'LIMIT', 'OFFSET'])
                
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
                    
                has_where = has_top_level_where(sql)
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
            dup = config_db.query_one("SELECT 1 FROM _duckdb_studio_api_endpoints WHERE path = ? AND id != ?", [path, endpoint_id])
            if dup:
                ui.notify(f"Endpoint path '/api/{path}' already exists! Please use a unique path.", type='negative')
                return
                
            rl_value = rate_limit.strip() if rate_limit and rate_limit.strip() else None
            config_db.execute("""
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

    # Restore saved active database context, defaulting to 'main'
    saved_active = 'main'
    try:
        explorer.conn.execute("USE main;")
        app.storage.user['active_database'] = 'main'
    except Exception:
        pass

    # --- INITIAL RUN ON CLIENT BROWSER CONNECT ---
    refresh_schema_tree()
    populate_builder_tables()
    update_query_history_list()
    refresh_saved_queries_list()
    populate_wizard_databases()
    try:
        refresh_parameter_inputs(sql_editor.value)
    except Exception:
        pass


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


@app.get("/garage_orange.svg")
def garage_orange_svg():
    from fastapi import Response
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="74 69 100 68" width="24" height="24">
  <style>
    .st0{fill:#4E4E4E;}
    .st3{fill:#45C8FF;}
    .st4{fill:#FF9329;}
  </style>
  <g transform="translate(2.0995769,2.0995769)">
    <path id="path6" d="m 136.06214,99.13643 c -0.8681,0.09646 -1.83266,0 -2.70078,-0.289369 L 99.794436,89.780144 c -0.868109,-0.28937 -1.736218,-0.675196 -2.507872,-1.157479 z" />
    <path id="path26" class="st3" d="m 136.73735,113.02618 18.42323,-7.42716 c 0.38583,-0.19291 0.57874,-0.57874 0.48228,-1.06102 -0.0965,-0.19292 -0.19291,-0.38583 -0.48228,-0.48229 -2.12204,-0.8681 -4.82284,-1.92913 -7.42716,-2.99015 -0.4823,-0.19291 -5.01576,3.08661 -5.40158,3.37598 l -7.90945,6.36613 c -1.83268,1.73622 -0.19291,3.27953 2.31496,2.21851 z" />
    <ellipse id="circle28" class="st3" cx="123.42634" cy="120.26041" rx="9.645668" ry="9.6456566" />
    <path id="path6-0" d="m 136.06214,99.13643 c -0.8681,0.09646 -1.83266,0 -2.70078,-0.289369 L 99.794436,89.780144 c -0.868109,-0.28937 -1.736218,-0.675196 -2.507872,-1.157479 z" />
    <path id="path24-3-6-9" class="st4" d="m 123.0405,70.199461 c -1.44685,0 -2.89371,0.28937 -4.14765,0.868109 L 76.259006,89.973057 c -0.771652,0.289369 -1.157479,1.253935 -0.868109,2.025588 0,0 0,0 0,0 0,0.09646 0,0.09646 0.09646,0.192913 l 6.848424,13.503922 h 5.980314 l -0.86811,-4.72638 c -0.09646,-0.38582 -0.675197,-3.086605 -1.253937,-5.015736 l 19.966532,6.269676 c 0.28937,1.25394 0.57874,2.41141 1.06103,3.47244 h 32.31298 c 0.38582,-1.06103 0.67519,-2.2185 0.86811,-3.47244 l 19.87007,-6.17322 c -0.57873,1.929131 -1.15747,4.62992 -1.25393,5.01574 l -0.86812,4.72637 h 5.98032 l 6.75197,-13.407459 0.0965,-0.09646 0.0965,-0.192913 c 0,0 0,0 0,0 0.0965,-0.192913 0.0965,-0.28937 0.0965,-0.482283 0,-0.675196 -0.38583,-1.253935 -0.96457,-1.543305 l -42.6339,-18.905486 c -1.54332,-0.675196 -2.99017,-1.061022 -4.53347,-0.964566 z" />
    <path id="path24-3-2" class="st0" d="m 123.0405,79.073465 c -1.44685,0 -2.89371,0.28937 -4.14765,0.868109 L 76.259006,98.847061 c -0.771652,0.289369 -1.157479,1.253939 -0.868109,2.025589 0,0 0,0 0,0 0,0.0965 0,0.0965 0.09646,0.19291 l 3.665353,7.3307 h 7.909449 c -0.289371,-1.06102 -0.578742,-2.31496 -0.964568,-3.56889 l 11.285433,3.56889 h 51.507866 l 11.28542,-3.56889 c -0.38581,1.15748 -0.67518,2.50787 -0.96455,3.56889 h 7.90943 l 3.66536,-7.23424 0.0965,-0.0965 0.0965,-0.19291 c 0,0 0,0 0,0 0.0965,-0.19291 0.0965,-0.28937 0.0965,-0.48228 0,-0.6752 -0.38582,-1.25394 -0.96457,-1.543309 L 127.47751,79.941574 c -1.44686,-0.578739 -2.89371,-0.868109 -4.43701,-0.868109 z" />
    <path id="path24-0" class="st4" d="m 171.07592,109.45728 c 0,0.19292 0,0.28937 -0.0965,0.48229 0,0 0,0 0,0 l -0.0965,0.19291 v 0 l -0.0965,0.0965 -10.32087,20.44879 c -1.44684,2.79724 -4.05116,2.70078 -3.66533,-0.0965 l 2.12203,-11.57479 c 0.0965,-0.38582 0.6752,-3.08661 1.25394,-5.01574 l -19.87014,6.17322 c -3.08661,20.35234 -29.90156,20.64171 -34.24212,0 L 86.0974,113.89428 c 0.578741,1.92914 1.157481,4.62992 1.253938,5.01575 l 2.122046,11.57478 c 0.482284,2.8937 -2.218503,2.99016 -3.665353,0.0965 L 75.390897,110.03602 c 0,-0.0964 -0.09646,-0.0964 -0.09646,-0.19291 -0.385827,-0.77165 0,-1.73622 0.771653,-2.02559 0,0 0,0 0,0 l 42.63386,-18.905486 c 2.70078,-1.157478 5.88385,-1.157478 8.58464,0 l 42.63385,18.905486 c 0.77166,0.38583 1.15748,0.96457 1.15748,1.63976 z" />
    <path id="path26-2" class="st0" d="m 136.73735,113.02618 18.42323,-7.42716 c 0.38583,-0.19291 0.57874,-0.57874 0.48228,-1.06102 -0.0965,-0.19292 -0.19291,-0.38583 -0.48228,-0.48229 -2.12204,-0.8681 -4.82284,-1.92913 -7.42716,-2.99015 -0.4823,-0.19291 -5.01576,3.08661 -5.40158,3.37598 l -7.90945,6.36613 c -1.83268,1.73622 -0.19291,3.27953 2.31496,2.21851 z" />
    <ellipse id="circle28-3" class="st0" cx="123.42634" cy="120.26041" rx="9.645668" ry="9.6456566" />
  </g>
</svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")



@app.get("/telemetry_colored.svg")
def telemetry_colored_svg():
    from fastapi import Response
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
  <line x1="2" y1="12" x2="22" y2="12" stroke="#818CF8" stroke-width="1" opacity="0.2" />
  <line x1="12" y1="2" x2="12" y2="22" stroke="#818CF8" stroke-width="1" opacity="0.2" />
  <circle cx="12" cy="12" r="10" fill="#818CF8" opacity="0.1" stroke="#818CF8" stroke-width="1.5" />
  <path d="M3 12 h4 l2 -5 l3 10 l2 -7 l2 2 h4" fill="none" stroke="#6366F1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
</svg>'''
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


@app.get("/superset_logo.svg")
def superset_logo_svg():
    from fastapi import Response
    svg_content = '''<svg viewBox="31 35 150 77" version="1.1" xmlns="http://www.w3.org/2000/svg" width="24" height="24">
    <path d="M141.56,37.83C129.1,37.83 117.56,44.83 106.56,57.08C95.62,44.64 83.94,37.83 70.89,37.83C49.29,37.83 33.52,53.19 33.52,74C33.52,94.81 49.29,110 70.89,110C84.13,110 94.45,103.77 105.89,91.33C117,103.74 128.32,110 141.56,110C163.17,110 178.93,94.83 178.93,74C178.93,53.17 163.17,37.83 141.56,37.83ZM71,88.19C61.85,88.19 56.4,82.19 56.4,74.19C56.4,66.19 61.89,60 71,60C78.78,60 85,66.22 91.82,74.58C85.44,82.36 78.63,88.19 71,88.19ZM140.88,88.19C133.29,88.19 126.88,82.19 120.05,74.19C127.05,65.83 133.05,60 140.88,60C150.03,60 155.48,66.22 155.48,74.19C155.48,82.16 150.07,88.19 140.92,88.19L140.88,88.19Z" fill="#FFFFFF"/>
    <path d="M122.21,104.88L136.74,87.57C130.9,85.85 125.61,80.64 120.09,74.19L105.93,91.3C110.555,96.709 116.059,101.301 122.21,104.88Z" fill="#10B981"/>
    <path d="M106.52,57.08C101.915,51.629 96.45,46.967 90.34,43.28L75.8,60.81C81.33,62.69 86.23,67.71 91.43,74.05L92,74.45C92,74.45 106.7,56.88 106.52,57.08Z" fill="#10B981"/>
</svg>'''
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


# API generation handlers and sync function are imported from api_generator.py


# --- INITIALIZE AND SEED DATABASE ON STARTUP ---
print("INFO: Initializing and seeding database on startup...", flush=True)
seed_database(DB_NAME)
init_saved_queries_table(DB_NAME)
sync_fastapi_dynamic_routes()


def run_buenavista_server():
    try:
        import os
        os.environ["BUENAVISTA_HOST"] = "0.0.0.0"

        import logging
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("buenavista").setLevel(logging.DEBUG)

        import duckdb
        from buenavista.backends.duckdb import DuckDBConnection
        from buenavista import bv_dialects, postgres, rewrite

        class DuckDBPostgresRewriter(rewrite.Rewriter):
            def rewrite(self, sql: str) -> str:
                sql_clean = sql.strip().lower().rstrip(';')
                if sql_clean == "select pg_catalog.version()":
                    return "SELECT 'PostgreSQL 9.3' as version"
                if "show transaction isolation level" in sql_clean or "show transaction_isolation" in sql_clean:
                    return "SELECT 'read committed' as transaction_isolation"
                if "show standard_conforming_strings" in sql_clean:
                    return "SELECT 'on' as standard_conforming_strings"
                if "pg_backend_pid()" in sql_clean:
                    return "SELECT 42 as pg_backend_pid"
                return super().rewrite(sql)

        # Monkeypatch DuckDBSession.execute_sql to fix substring transaction command bug in buenavista
        from buenavista.backends.duckdb import DuckDBSession, DuckDBQueryResult
        import sqlglot
        
        def patched_execute_sql(self, sql: str, params=None):
            status = ""
            try:
                lsql = sqlglot.parse_one(sql).sql(comments=False)
            except Exception:
                lsql = sql

            lsql = lsql.lower().strip()
            
            # Use strict token matching or startswith rather than simple substring inclusion
            is_commit = lsql.startswith("commit") or lsql == "end"
            is_rollback = lsql.startswith("rollback") or lsql.startswith("abort")
            is_begin = lsql.startswith("begin") or lsql.startswith("start transaction")
            
            if self.in_txn:
                if is_commit:
                    self.in_txn = False
                    status = "COMMIT"
                elif is_rollback:
                    self.in_txn = False
                    status = "ROLLBACK"
                elif is_begin:
                    return DuckDBQueryResult(status="BEGIN")
            elif is_begin:
                self.in_txn = True
                status = "BEGIN"

            logging.getLogger("buenavista").debug("Original SQL: %s", sql)
            sql = self.rewrite_sql(sql)
            logging.getLogger("buenavista").debug("Rewritten SQL: %s", sql)
            if params:
                self._cursor.execute(sql, params)
            else:
                self._cursor.execute(sql)

            if status:
                return DuckDBQueryResult(status=status)

            rb = None
            if self._cursor.description:
                if lsql.startswith("load "):
                    self.refresh_config()
                    status = "LOAD"
                elif not (lsql.startswith("insert ") or lsql.startswith("update ") or lsql.startswith("delete ")):
                    rb = self._cursor.fetch_record_batch()
            return DuckDBQueryResult(rb, status)

        DuckDBSession.execute_sql = patched_execute_sql

        rewriter = DuckDBPostgresRewriter(bv_dialects.BVPostgres(), bv_dialects.BVDuckDB())
        db = duckdb.connect(DB_NAME, config=DB_CONFIG)
        
        # Attach any pre-configured databases to this connection too
        load_attached_databases_for_connection(db)

        address = ("0.0.0.0", 5433)
        server = postgres.BuenaVistaServer(
            address, DuckDBConnection(db), rewriter=rewriter, auth=None
        )
        print("INFO: Buena Vista PGWire server listening on 0.0.0.0:5433", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"ERROR: Buena Vista PGWire server encountered an error: {e}", flush=True)

def run_quack_server():
    try:
        import os
        import time
        import duckdb
        db = duckdb.connect(DB_NAME, config=DB_CONFIG)
        
        # Ensure attached databases are loaded in the server connection too
        load_attached_databases_for_connection(db)
        
        db.execute("INSTALL quack FROM core_nightly;")
        db.execute("LOAD quack;")
        
        token = os.environ.get("QUACK_TOKEN", "duckdb_studio_secret_token_123")
        db.execute(f"CALL quack_serve('quack:0.0.0.0:8001', token='{token}', allow_other_hostname=True, disable_ssl=True);")
        print("INFO: DuckDB Quack Server listening on 0.0.0.0:8001", flush=True)
        
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"ERROR: DuckDB Quack Server failed to start: {e}", flush=True)

# Start Quack daemon thread
import threading
quack_thread = threading.Thread(target=run_quack_server, daemon=True)
quack_thread.start()

# Start Buena Vista PGWire Server Thread
bv_thread = threading.Thread(target=run_buenavista_server, daemon=True)
bv_thread.start()

# Start Background Scheduler Thread
scheduler_thread = threading.Thread(target=run_background_scheduler, daemon=True)
scheduler_thread.start()


# Start application server
ui.run(title='DuckDB Data Studio Explorer', port=8085, show=False, storage_secret='duckdb_studio_secret_key_1337', reload=False, reconnect_timeout=30.0)
