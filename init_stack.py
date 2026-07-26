#!/usr/bin/env python3
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime

def print_step(msg):
    print(f"[*] {msg}...", flush=True)

def print_success(msg):
    print(f"[+] {msg} succeeded.", flush=True)

def print_error(msg):
    print(f"[-] {msg}", flush=True)

def init_folders():
    print_step("Creating required application directories")
    dirs = [
        'databases',
        'config/certs',
        'exports',
        'scratch',
        'shared/garage',
        'shared/garage/meta',
        'shared/garage/data'
    ]
    for d in dirs:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            print(f"  Created directory: {d}")
        else:
            print(f"  Directory exists: {d}")
    print_success("Directory provisioning")

def init_tls():
    print_step("Checking SSL/TLS certificate provisioning for Duckgres PGWire")
    key_path = 'config/certs/server.key'
    cert_path = 'config/certs/server.crt'
    
    if not os.path.exists(key_path) or not os.path.exists(cert_path):
        print("  Generating self-signed SSL/TLS certificate...")
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
            print_success("Self-signed TLS certificate generation")
        except Exception as e:
            print_error(f"Failed to generate self-signed TLS certificate: {e}")
    else:
        print("  Certificates already exist.")
        print_success("TLS certificate verification")

def init_sqlite():
    print_step("Initializing SQLite config database app_config.db")
    db_path = 'config/app_config.db'
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        
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

        # Seed default queries
        res = conn.execute("SELECT count(*) FROM _duckdb_studio_saved_queries").fetchone()
        if res and res[0] == 0:
            print("  Seeding default analytical query templates...")
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
            print("  Seeding system settings API endpoint...")
            conn.execute("""
                INSERT INTO _duckdb_studio_api_endpoints (id, path, description, sql_code, created_at)
                VALUES (?, ?, ?, ?, ?);
            """, [
                str(uuid.uuid4()),
                'settings',
                'Returns a list of DuckDB settings matching a prefix or pattern.',
                "SELECT name, value, description FROM duckdb_settings() WHERE name LIKE '%' || :name_prefix || '%' ORDER BY name LIMIT 100;",
                datetime.now().isoformat()
            ])
            conn.commit()
            
        print_success("SQLite app_config.db initialization")
        conn.close()
    except Exception as e:
        print_error(f"Failed to initialize SQLite config: {e}")

def fix_dbt_project_permissions():
    print_step("Fixing dbt project file permissions for code-server")
    dbt_project_dir = '/app/dbt_project'
    if os.path.exists(dbt_project_dir):
        try:
            # Change ownership to UID 1000 (coder inside container)
            subprocess.run(["chown", "-R", "1000:1000", dbt_project_dir], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            for root, dirs, files in os.walk(dbt_project_dir):
                for d in dirs:
                    try:
                        os.chmod(os.path.join(root, d), 0o777)
                    except PermissionError:
                        pass
                for f in files:
                    try:
                        os.chmod(os.path.join(root, f), 0o666)
                    except PermissionError:
                        pass
            print_success("dbt project permissions correction")
        except Exception as e:
            print_error(f"Failed to correct dbt project permissions: {e}")
    else:
        print("  dbt_project directory does not exist, skipping permissions check.")

def main():
    print("=========================================================")
    print("      🦆 DUCKDB DATA STUDIO STACK INITIALIZATION 🦆       ")
    print("=========================================================")
    init_folders()
    init_tls()
    init_sqlite()
    fix_dbt_project_permissions()
    print("=========================================================")
    print("[+] Stack initialization complete. Ready to launch!")
    print("=========================================================")

if __name__ == '__main__':
    main()
