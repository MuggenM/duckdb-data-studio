import time
import duckdb
from collections import defaultdict

DB_CONFIG = {
    'custom_user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    'threads': '4',
    'memory_limit': '2GB',
    'preserve_insertion_order': 'false'
}

class DuckDBExplorer:
    def __init__(self, db_file):
        self.db_file = db_file
        self._conn = None
        self._cursor = None
        
    @property
    def conn(self):
        if self._conn is None:
            self._conn = duckdb.connect(self.db_file, config=DB_CONFIG)
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
                data = result.fetchmany(10000)
                columns = [desc[0] for desc in result.description]
                return {
                    'data': data,
                    'columns': columns,
                    'is_select': True,
                    'duration_ms': duration_ms,
                    'affected_rows': len(data),
                    'truncated': len(data) >= 10000
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

    def save_custom_query(self, name, description, sql_code, category='Analytical'):
        import uuid
        from datetime import datetime
        from config_manager import SQLiteConfigManager
        config_db = SQLiteConfigManager()
        q_id = str(uuid.uuid4())
        try:
            config_db.execute("""
                INSERT INTO _duckdb_studio_saved_queries (id, name, description, sql_code, created_at, category)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (q_id, name, description, sql_code, datetime.now().isoformat(), category))
            return True
        except Exception as e:
            print(f"Error saving custom query: {e}")
            return False

    def list_saved_queries(self):
        from datetime import datetime
        from config_manager import SQLiteConfigManager
        config_db = SQLiteConfigManager()
        try:
            rows = config_db.query_all("SELECT id, name, description, sql_code, created_at, category FROM _duckdb_studio_saved_queries ORDER BY created_at DESC")
            res = []
            for row in rows:
                created_at_val = row['created_at']
                if isinstance(created_at_val, str):
                    try:
                        created_at_val = datetime.fromisoformat(created_at_val)
                    except Exception:
                        created_at_val = datetime.now()
                res.append({
                    'id': row['id'],
                    'name': row['name'],
                    'description': row['description'],
                    'sql_code': row['sql_code'],
                    'created_at': created_at_val,
                    'category': row['category'] if row['category'] else 'Analytical'
                })
            return res
        except Exception as e:
            print(f"Error listing saved queries: {e}")
            return []

    def delete_saved_query(self, q_id):
        from config_manager import SQLiteConfigManager
        config_db = SQLiteConfigManager()
        try:
            config_db.execute("DELETE FROM _duckdb_studio_saved_queries WHERE id = ?", (q_id,))
            return True
        except Exception as e:
            print(f"Error deleting saved query: {e}")
            return False

    def update_saved_query(self, q_id, sql_code, name=None, description=None, category=None):
        from config_manager import SQLiteConfigManager
        config_db = SQLiteConfigManager()
        try:
            if name is not None:
                config_db.execute("""
                    UPDATE _duckdb_studio_saved_queries 
                    SET sql_code = ?, name = ?, description = ?, category = ? 
                    WHERE id = ?
                """, (sql_code, name, description, category, q_id))
            else:
                config_db.execute("""
                    UPDATE _duckdb_studio_saved_queries 
                    SET sql_code = ? 
                    WHERE id = ?
                """, (sql_code, q_id))
            return True
        except Exception as e:
            print(f"Error updating saved query: {e}")
            return False

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


def get_config_path():
    """Get the path to attached_databases.yaml, using /config if running in Docker, otherwise config/attached_databases.yaml."""
    import os
    if os.path.exists('/config') and os.path.isdir('/config'):
        return '/config/attached_databases.yaml'
    if not os.path.exists('config'):
        os.makedirs('config', exist_ok=True)
    return 'config/attached_databases.yaml'


def load_attached_databases_for_connection(conn):
    """Load and attach all databases stored in attached_databases.yaml to the given connection."""
    import os
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
    import os
    import yaml
    config_path = get_config_path()
    
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
    import os
    import yaml
    config_path = get_config_path()
    if not os.path.exists(config_path):
        return False
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

