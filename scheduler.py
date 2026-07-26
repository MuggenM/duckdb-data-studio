import os
import time
import datetime
import duckdb
import uuid
from config_manager import SQLiteConfigManager, get_main_db_path
from db_explorer import DB_CONFIG, load_attached_databases_for_connection

def calculate_next_run(interval: str, now=None):
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

def run_background_scheduler(db_name=None):
    print("INFO: Starting Background Query Scheduler Thread...", flush=True)
    config_db = SQLiteConfigManager()
    
    export_dir = "/home/martin/volumes/duckdb-studio/exports"
    try:
        os.makedirs(export_dir, exist_ok=True)
    except Exception as e:
        print(f"ERROR: Failed to create export directory: {e}", flush=True)
        
    db_path = db_name if db_name else get_main_db_path()
    
    # Track S3 Delta catalog sync and database cleanup timing
    last_s3_sync = datetime.datetime.min
    last_cleanup = datetime.datetime.min
    
    while True:
        try:
            now = datetime.datetime.now()
            
            # Periodically synchronize S3 Delta tables as DuckDB views (every 60 seconds)
            if (now - last_s3_sync).total_seconds() >= 60:
                try:
                    from s3_catalog_sync import sync_catalog
                    sync_catalog()
                    last_s3_sync = now
                except Exception as sync_ex:
                    print(f"ERROR: Background S3 Delta Catalog Sync failed: {sync_ex}", flush=True)
                    
            # Periodically clean up telemetry and logs older than 7 days (every 1 hour)
            if (now - last_cleanup).total_seconds() >= 3600:
                try:
                    cutoff_time = (now - datetime.timedelta(days=7)).isoformat()
                    config_db.execute("DELETE FROM _duckdb_studio_api_metrics WHERE timestamp < ?;", [cutoff_time])
                    config_db.execute("DELETE FROM _duckdb_studio_scheduler_logs WHERE executed_at < ?;", [cutoff_time])
                    config_db.execute("DELETE FROM _duckdb_studio_query_history WHERE timestamp < ?;", [cutoff_time])
                    print("INFO: Telemetry and logs cleanup completed (pruned data older than 7 days).", flush=True)
                    last_cleanup = now
                except Exception as clean_ex:
                    print(f"ERROR: Background telemetry cleanup failed: {clean_ex}", flush=True)
            # Fetch active jobs from SQLite
            jobs = config_db.query_all("""
                SELECT 
                    id, name, sql_code, interval_str, export_format, 
                    partition_column, export_filename, next_run 
                FROM _duckdb_studio_scheduled_jobs 
                WHERE status = 'Active' AND (next_run <= ? OR next_run IS NULL);
            """, [now.isoformat()])
            
            if jobs:
                ddb_conn = duckdb.connect(db_path, config=DB_CONFIG)
                try:
                    ddb_conn.execute("SET http_keep_alive=true;")
                    ddb_conn.execute("SET enable_object_cache=true;")
                    ddb_conn.execute("SET http_timeout=10;")
                except Exception as set_ex:
                    print(f"WARNING: failed to configure HTTP/S3 settings in scheduler jobs: {set_ex}", flush=True)
                try:
                    for job in jobs:
                        j_id = job['id']
                        j_name = job['name']
                        j_sql = job['sql_code']
                        j_interval = job['interval_str']
                        j_format = job['export_format']
                        j_part_col = job['partition_column']
                        j_filename = job['export_filename']
                        
                        start_time = time.time()
                        row_count = 0
                        file_size = 0
                        run_status = "Success"
                        run_err = None
                        
                        try:
                            copy_options = f"FORMAT '{j_format.upper()}'"
                            if j_part_col and j_part_col.strip():
                                copy_options += f", PARTITION_BY '{j_part_col.strip()}'"
                                dest_path = os.path.join(export_dir, j_filename + f"_{j_format.lower()}_partitioned")
                                os.makedirs(dest_path, exist_ok=True)
                            else:
                                ext = j_format.lower()
                                dest_path = os.path.join(export_dir, f"{j_filename}.{ext}")
                            
                            load_attached_databases_for_connection(ddb_conn)
                            ddb_conn.execute(f"COPY ({j_sql.strip().rstrip(';')}) TO '{dest_path}' ({copy_options});")
                            
                            count_df = ddb_conn.execute(f"SELECT COUNT(*) FROM ({j_sql.strip().rstrip(';')});").fetchone()
                            row_count = count_df[0] if count_df else 0
                            
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
                        next_run_time = calculate_next_run(j_interval, now)
                        
                        config_db.execute("""
                            UPDATE _duckdb_studio_scheduled_jobs 
                            SET last_run = ?, next_run = ?, status = ?, error_message = ? 
                            WHERE id = ?;
                        """, [now.isoformat(), next_run_time.isoformat(), "Active", run_err, j_id])
                        
                        log_id = str(uuid.uuid4())
                        config_db.execute("""
                            INSERT INTO _duckdb_studio_scheduler_logs (id, job_id, job_name, executed_at, duration_ms, row_count, file_size_bytes, status, error_message)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """, [log_id, j_id, j_name, now.isoformat(), duration_ms, row_count, file_size, run_status, run_err])
                finally:
                    ddb_conn.close()
            
        except Exception as conn_ex:
            print(f"ERROR: Background Scheduler encountered database error: {conn_ex}", flush=True)
            
        time.sleep(10)
