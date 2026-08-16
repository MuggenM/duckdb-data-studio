import os
import json
import duckdb

ATTACHED_CONFIG_FILE = "config/attached_databases.json"

def get_config_filepath():
    os.makedirs("config", exist_ok=True)
    return ATTACHED_CONFIG_FILE

def load_saved_attachments():
    filepath = get_config_filepath()
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Failed to load saved db attachments: {e}", flush=True)
    return []

def save_attachment_config(attachment_info):
    attachments = load_saved_attachments()
    # Remove existing entry with same alias
    attachments = [a for a in attachments if a.get('alias') != attachment_info['alias']]
    attachments.append(attachment_info)
    filepath = get_config_filepath()
    try:
        with open(filepath, 'w') as f:
            json.dump(attachments, f, indent=2)
    except Exception as e:
        print(f"ERROR: Failed to save db attachment config: {e}", flush=True)

def remove_attachment_config(alias):
    attachments = load_saved_attachments()
    attachments = [a for a in attachments if a.get('alias') != alias]
    filepath = get_config_filepath()
    try:
        with open(filepath, 'w') as f:
            json.dump(attachments, f, indent=2)
    except Exception as e:
        print(f"ERROR: Failed to remove db attachment config: {e}", flush=True)

def get_attached_databases(duckdb_conn):
    """Fetch currently attached databases from DuckDB metadata."""
    try:
        df = duckdb_conn.execute("SELECT database_name, path, type, readonly FROM duckdb_databases();").df()
        res = []
        for _, row in df.iterrows():
            db_name = row['database_name']
            if db_name in ('system', 'temp', 'memory'):
                continue
            res.append({
                'alias': db_name,
                'path': row['path'],
                'type': row['type'].upper(),
                'read_only': bool(row['readonly'])
            })
        return res
    except Exception as e:
        print(f"ERROR: Fetching attached databases: {e}", flush=True)
        return []

def ensure_extension_loaded(duckdb_conn, ext_name):
    """Ensure DuckDB extension is installed and loaded."""
    try:
        duckdb_conn.execute(f"INSTALL {ext_name}; LOAD {ext_name};")
    except Exception as e:
        print(f"WARNING: Installing extension '{ext_name}': {e}", flush=True)

def attach_database(duckdb_conn, db_type, alias, params, read_only=True):
    """
    Attach an external database (PostgreSQL, SQLite, Iceberg, MotherDuck) to DuckDB connection.
    """
    db_type_upper = db_type.upper()
    read_only_str = "READ_ONLY" if read_only else ""

    if db_type_upper == 'POSTGRESQL':
        ensure_extension_loaded(duckdb_conn, 'postgres')
        host = params.get('host', 'localhost')
        port = params.get('port', '5432')
        dbname = params.get('dbname', '')
        user = params.get('user', '')
        password = params.get('password', '')
        
        conn_str = f"dbname={dbname} host={host} port={port} user={user} password={password}"
        sql = f"ATTACH '{conn_str}' AS {alias} (TYPE POSTGRES"
        if read_only:
            sql += ", READ_ONLY"
        sql += ");"

        duckdb_conn.execute(sql)
        save_attachment_config({
            'alias': alias,
            'db_type': 'POSTGRESQL',
            'params': {'host': host, 'port': port, 'dbname': dbname, 'user': user},
            'read_only': read_only
        })

    elif db_type_upper == 'SQLITE':
        ensure_extension_loaded(duckdb_conn, 'sqlite')
        filepath = params.get('filepath', '')
        if not filepath:
            raise ValueError("Filepath is required for SQLite attachment.")
            
        sql = f"ATTACH '{filepath}' AS {alias} (TYPE SQLITE"
        if read_only:
            sql += ", READ_ONLY"
        sql += ");"

        duckdb_conn.execute(sql)
        save_attachment_config({
            'alias': alias,
            'db_type': 'SQLITE',
            'params': {'filepath': filepath},
            'read_only': read_only
        })

    elif db_type_upper == 'ICEBERG':
        ensure_extension_loaded(duckdb_conn, 'iceberg')
        ensure_extension_loaded(duckdb_conn, 'httpfs')
        s3_path = params.get('s3_path', '')
        if not s3_path:
            raise ValueError("S3 path is required for Iceberg attachment.")
            
        duckdb_conn.execute("SET AWS_EC2_METADATA_DISABLED=true;")
        duckdb_conn.execute("""
            CREATE OR REPLACE SECRET attach_s3_secret (
                TYPE S3,
                KEY_ID 'GK2713753aca1d72db5325f212',
                SECRET 'afd53ab8d8e6f762973bab0b5a33998265530dee63cae200e1a8e065be2a4b6e',
                ENDPOINT 'garage:3900',
                REGION 'us-east-1',
                USE_SSL false,
                URL_STYLE 'path'
            );
        """)

        sql = f"ATTACH '{s3_path}' AS {alias} (TYPE ICEBERG"
        if read_only:
            sql += ", READ_ONLY"
        sql += ");"

        duckdb_conn.execute(sql)
        save_attachment_config({
            'alias': alias,
            'db_type': 'ICEBERG',
            'params': {'s3_path': s3_path},
            'read_only': read_only
        })

    elif db_type_upper == 'MOTHERDUCK':
        ensure_extension_loaded(duckdb_conn, 'motherduck')
        token = params.get('token', '')
        if token:
            duckdb_conn.execute(f"SET motherduck_token='{token}';")
            
        md_dbname = params.get('dbname', '')
        md_str = f"md:{md_dbname}" if md_dbname else "md:"
        sql = f"ATTACH '{md_str}' AS {alias};"

        duckdb_conn.execute(sql)
        save_attachment_config({
            'alias': alias,
            'db_type': 'MOTHERDUCK',
            'params': {'dbname': md_dbname},
            'read_only': read_only
        })

    else:
        raise ValueError(f"Unsupported database provider: {db_type}")

    return True

def detach_database(duckdb_conn, alias):
    """Detach an attached database from DuckDB and remove saved configuration."""
    try:
        duckdb_conn.execute(f"DETACH {alias};")
        remove_attachment_config(alias)
        return True
    except Exception as e:
        print(f"ERROR: Detaching database '{alias}': {e}", flush=True)
        return False

def auto_reconnect_saved_databases(duckdb_conn):
    """Auto-reconnect saved attachments from config at startup."""
    saved = load_saved_attachments()
    reconnected = 0
    for s in saved:
        try:
            alias = s['alias']
            db_type = s['db_type']
            params = s.get('params', {})
            read_only = s.get('read_only', True)
            attach_database(duckdb_conn, db_type, alias, params, read_only=read_only)
            reconnected += 1
        except Exception as ex:
            print(f"WARNING: Could not auto-reconnect attached database '{s.get('alias')}': {ex}", flush=True)
    return reconnected
