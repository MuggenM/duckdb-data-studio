import os
import sqlite3
import uuid
import duckdb
from datetime import datetime

# Local DuckDB configuration wrapper mapping
DB_CONFIG = {
    'custom_user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

def get_config_db_path():
    """Get the path to the config SQLite file, using /config if running in Docker, otherwise config/app_config.db."""
    if os.path.exists('/config') and os.path.isdir('/config'):
        return '/config/app_config.db'
    if not os.path.exists('config'):
        os.makedirs('config', exist_ok=True)
    return 'config/app_config.db'

def get_main_db_path():
    """Get the path to the main DuckDB file, using /databases if running in Docker, otherwise databases/main.duckdb."""
    if os.path.exists('/databases') and os.path.isdir('/databases'):
        return '/databases/main.duckdb'
    if not os.path.exists('databases'):
        os.makedirs('databases', exist_ok=True)
    return 'databases/main.duckdb'

class SQLiteConfigManager:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = get_config_db_path()
        self.db_path = db_path
        self._init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self.get_connection() as conn:
            # 1. Saved queries table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_saved_queries (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    sql_code TEXT,
                    created_at TIMESTAMP,
                    category TEXT DEFAULT 'Analytical'
                );
            """)
            
            # 2. Scheduled jobs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_scheduled_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    sql_code TEXT,
                    interval_str TEXT,
                    export_format TEXT,
                    partition_column TEXT,
                    export_filename TEXT,
                    last_run TIMESTAMP,
                    next_run TIMESTAMP,
                    status TEXT,
                    error_message TEXT
                );
            """)

            # 3. Scheduler logs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_scheduler_logs (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    job_name TEXT,
                    executed_at TIMESTAMP,
                    duration_ms REAL,
                    row_count INTEGER,
                    file_size_bytes INTEGER,
                    status TEXT,
                    error_message TEXT
                );
            """)

            # 4. API Endpoints table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_api_endpoints (
                    id TEXT PRIMARY KEY,
                    path TEXT UNIQUE,
                    description TEXT,
                    sql_code TEXT,
                    created_at TIMESTAMP,
                    security_enabled INTEGER DEFAULT 0,
                    rate_limit TEXT
                );
            """)

            # 5. API Metrics table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_api_metrics (
                    endpoint_path TEXT,
                    timestamp TIMESTAMP,
                    latency_ms REAL,
                    status_code INTEGER,
                    error_message TEXT
                );
            """)

            # 6. Query History table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _duckdb_studio_query_history (
                    id TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    rows_count INTEGER,
                    error_message TEXT
                );
            """)
            conn.commit()

            # Pre-seed default queries
            res = conn.execute("SELECT count(*) FROM _duckdb_studio_saved_queries").fetchone()
            if res and res[0] == 0:
                default_queries = [
                    (str(uuid.uuid4()), "Show Attached Databases", "Lists all attached databases, paths, and configurations", "SELECT database_name, path, readonly FROM duckdb_databases();", datetime.now().isoformat(), "Utility"),
                    (str(uuid.uuid4()), "List Tables and Views", "Lists all tables, views, and types in the current database", "SELECT database_name, schema_name, table_name, internal FROM duckdb_tables ORDER BY database_name, schema_name, table_name;", datetime.now().isoformat(), "Utility"),
                    (str(uuid.uuid4()), "Show Settings", "Lists all configuration settings of the DuckDB instance", "SELECT name, value, description FROM duckdb_settings() ORDER BY name;", datetime.now().isoformat(), "Utility"),
                    (str(uuid.uuid4()), "List Extensions", "Shows all loaded and installed DuckDB extensions and status", "SELECT extension_name, loaded, installed FROM duckdb_extensions() ORDER BY extension_name;", datetime.now().isoformat(), "Utility"),
                    (str(uuid.uuid4()), "Attach & Query DuckLake Database", "Template query showing how to attach an external DuckLake data lake catalog and query its parquet tables", "-- 🦆 Attach a DuckLake table format database\n-- Replace the paths below with your metadata DB file and Parquet data folder:\nATTACH 'ducklake:path/to/metadata.db' AS my_lakehouse (DATA_PATH 'path/to/data_parquet/');\n\n-- Now you can query tables in your lakehouse as usual:\n-- SELECT * FROM my_lakehouse.my_table LIMIT 10;", datetime.now().isoformat(), "Utility")
                ]
                conn.executemany("INSERT INTO _duckdb_studio_saved_queries VALUES (?, ?, ?, ?, ?, ?)", default_queries)
                conn.commit()

            # Seed default API endpoints if missing
            res_settings = conn.execute("SELECT 1 FROM _duckdb_studio_api_endpoints WHERE path = 'settings';").fetchone()
            if not res_settings:
                conn.execute("""
                    INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at)
                    VALUES (?, ?, ?, ?, ?);
                """, [
                    str(uuid.uuid4()),
                    'settings',
                    'Returns a list of DuckDB settings matching a prefix or pattern.',
                    'SELECT name, value, description FROM duckdb_settings() WHERE name LIKE $pattern ORDER BY name;',
                    datetime.now().isoformat()
                ])
                conn.commit()
                
            res_test2 = conn.execute("SELECT 1 FROM _duckdb_studio_api_endpoints WHERE path = 'test2';").fetchone()
            if not res_test2:
                conn.execute("""
                    INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at)
                    VALUES (?, ?, ?, ?, ?);
                """, [
                    str(uuid.uuid4()),
                    'test2',
                    'Dummy endpoint for testing parameters',
                    'SELECT 1 WHERE ($p1 IS NULL AND $p2 IS NULL AND $p3 IS NULL AND $p4 IS NULL AND $p5 IS NULL AND $p6 IS NULL AND $p7 IS NULL AND $p8 IS NULL AND $p9 IS NULL AND $p10 IS NULL AND $p11 IS NULL AND $p12 IS NULL);',
                    datetime.now().isoformat()
                ])
                conn.commit()
                
            res_products = conn.execute("SELECT 1 FROM _duckdb_studio_api_endpoints WHERE path = 'products';").fetchone()
            if not res_products:
                conn.execute("""
                    INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at)
                    VALUES (?, ?, ?, ?, ?);
                """, [
                    str(uuid.uuid4()),
                    'products',
                    'Returns a filtered list of product inventory matching category, price, and stock parameters.',
                    'SELECT product_id, name, category, price, stock FROM product_inventory WHERE 1=1 AND ($category IS NULL OR category = $category) AND ($min_price IS NULL OR price >= $min_price) AND ($max_price IS NULL OR price <= $max_price) AND ($min_stock IS NULL OR stock >= $min_stock) ORDER BY product_id;',
                    datetime.now().isoformat()
                ])
                conn.commit()

        try:
            self.migrate_from_duckdb()
        except Exception as e:
            print(f"ERROR: Exception during database migration: {e}")

    def migrate_from_duckdb(self):
        duckdb_path = get_main_db_path()
        if not os.path.exists(duckdb_path):
            return
        
        ddb_conn = duckdb.connect(duckdb_path, config=DB_CONFIG)
        
        def table_exists_in_duckdb(table_name):
            try:
                res = ddb_conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?;", [table_name]).fetchone()
                return res is not None
            except Exception:
                return False
        
        tables_to_migrate = [
            ('_duckdb_studio_saved_queries', [
                ('id', str), ('name', str), ('description', str), 
                ('sql_code', str), ('created_at', datetime), ('category', str)
            ]),
            ('_duckdb_studio_api_endpoints', [
                ('id', str), ('path', str), ('description', str), 
                ('sql_code', str), ('created_at', datetime), 
                ('security_enabled', bool), ('rate_limit', str)
            ]),
            ('_duckdb_studio_api_metrics', [
                ('endpoint_path', str), ('timestamp', datetime), 
                ('latency_ms', float), ('status_code', int), ('error_message', str)
            ]),
            ('_duckdb_studio_scheduled_jobs', [
                ('id', str), ('name', str), ('sql_code', str), ('interval_str', str), 
                ('export_format', str), ('partition_column', str), ('export_filename', str), 
                ('last_run', datetime), ('next_run', datetime), ('status', str), ('error_message', str)
            ]),
            ('_duckdb_studio_scheduler_logs', [
                ('id', str), ('job_id', str), ('job_name', str), ('executed_at', datetime), 
                ('duration_ms', float), ('row_count', int), ('file_size_bytes', int), 
                ('status', str), ('error_message', str)
            ])
        ]
        
        for table_name, cols in tables_to_migrate:
            if table_exists_in_duckdb(table_name):
                print(f"INFO: Migrating table {table_name} from DuckDB to SQLite...", flush=True)
                col_names = [c[0] for c in cols]
                col_types = [c[1] for c in cols]
                
                query = f"SELECT {', '.join(col_names)} FROM {table_name};"
                rows = ddb_conn.execute(query).fetchall()
                
                if rows:
                    with self.get_connection() as sqlite_conn:
                        placeholders = ', '.join(['?'] * len(col_names))
                        insert_query = f"INSERT OR IGNORE INTO {table_name} ({', '.join(col_names)}) VALUES ({placeholders});"
                        
                        normalized_rows = []
                        for row in rows:
                            norm_row = []
                            for val, col_type in zip(row, col_types):
                                if isinstance(val, bool) or col_type == bool:
                                    norm_row.append(1 if val else 0)
                                elif isinstance(val, datetime) or col_type == datetime:
                                    norm_row.append(val.isoformat() if val else None)
                                else:
                                    norm_row.append(val)
                            normalized_rows.append(tuple(norm_row))
                            
                        sqlite_conn.executemany(insert_query, normalized_rows)
                        sqlite_conn.commit()
                
                ddb_conn.execute(f"DROP TABLE IF EXISTS {table_name};")
                print(f"INFO: Successfully migrated and dropped table {table_name} from DuckDB.", flush=True)
        
        ddb_conn.close()

    def query_one(self, sql, params=()):
        with self.get_connection() as conn:
            res = conn.execute(sql, params).fetchone()
            return dict(res) if res else None

    def query_all(self, sql, params=()):
        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def execute(self, sql, params=()):
        with self.get_connection() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid


def get_studio_config_path():
    """Get the path to studio_config.yaml, using /config if running in Docker, otherwise config/studio_config.yaml."""
    if os.path.exists('/config') and os.path.isdir('/config'):
        return '/config/studio_config.yaml'
    if not os.path.exists('config'):
        os.makedirs('config', exist_ok=True)
    return 'config/studio_config.yaml'


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


APP_SETTINGS = load_app_settings()

