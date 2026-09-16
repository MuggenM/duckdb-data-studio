import os
import time
import asyncio
import datetime
import sqlite3
import logging
from typing import Dict, Any, List, Optional
import duckdb

logger = logging.getLogger("duckdb_studio.scheduler")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
DB_PATH = os.path.join(CONFIG_DIR, "studio_config.db")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)


def calculate_next_run(interval_str: str, now: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Calculates next execution datetime from interval specification."""
    if now is None:
        now = datetime.datetime.now()
    interval_lower = interval_str.lower()
    if "minute" in interval_lower and "5" not in interval_lower and "15" not in interval_lower:
        return now + datetime.timedelta(minutes=1)
    elif "5" in interval_lower:
        return now + datetime.timedelta(minutes=5)
    elif "15" in interval_lower:
        return now + datetime.timedelta(minutes=15)
    elif "hour" in interval_lower and "12" not in interval_lower:
        return now + datetime.timedelta(hours=1)
    elif "12" in interval_lower:
        return now + datetime.timedelta(hours=12)
    elif "daily" in interval_lower:
        tomorrow = now + datetime.timedelta(days=1)
        return datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0)
    return now + datetime.timedelta(minutes=5)


class SchedulerService:
    _instance = None

    def __init__(self):
        self._init_db()
        self._is_running = False

    @classmethod
    def get_instance(cls) -> "SchedulerService":
        if cls._instance is None:
            cls._instance = SchedulerService()
        return cls._instance

    def _init_db(self):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS scheduled_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        sql_code TEXT NOT NULL,
                        interval_str TEXT NOT NULL DEFAULT 'Every Hour',
                        export_format TEXT NOT NULL DEFAULT 'parquet',
                        partition_column TEXT DEFAULT '',
                        export_filename TEXT DEFAULT '',
                        status TEXT DEFAULT 'Active',
                        next_run TEXT,
                        last_run TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS scheduler_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id INTEGER,
                        job_name TEXT,
                        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        duration_ms REAL DEFAULT 0,
                        row_count INTEGER DEFAULT 0,
                        output_path TEXT,
                        status TEXT DEFAULT 'Success',
                        error_message TEXT
                    );
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize scheduler SQLite tables: {e}")

    def list_jobs(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM scheduled_jobs ORDER BY id DESC;")
            return [dict(r) for r in cursor.fetchall()]

    def create_or_update_job(self, data: Dict[str, Any]) -> int:
        job_id = data.get("id")
        name = data.get("name", "Untitled Job").strip()
        sql_code = data.get("sql_code", "").strip()
        interval_str = data.get("interval_str", "Every Hour")
        export_format = data.get("export_format", "parquet").lower()
        partition_col = data.get("partition_column", "").strip()
        export_filename = data.get("export_filename", "").strip()
        status = data.get("status", "Active")
        next_run = calculate_next_run(interval_str).isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            if job_id:
                conn.execute("""
                    UPDATE scheduled_jobs
                    SET name=?, sql_code=?, interval_str=?, export_format=?, partition_column=?, export_filename=?, status=?, next_run=?
                    WHERE id=?;
                """, (name, sql_code, interval_str, export_format, partition_col, export_filename, status, next_run, job_id))
                return job_id
            else:
                cursor = conn.execute("""
                    INSERT INTO scheduled_jobs (name, sql_code, interval_str, export_format, partition_column, export_filename, status, next_run)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (name, sql_code, interval_str, export_format, partition_col, export_filename, status, next_run))
                conn.commit()
                return cursor.lastrowid

    def delete_job(self, job_id: int) -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM scheduled_jobs WHERE id=?;", (job_id,))
            conn.commit()
            return True

    def toggle_job_status(self, job_id: int) -> Dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT status FROM scheduled_jobs WHERE id=?;", (job_id,)).fetchone()
            if not row:
                return {"success": False, "error": "Job not found."}
            new_status = "Paused" if row["status"] == "Active" else "Active"
            conn.execute("UPDATE scheduled_jobs SET status=? WHERE id=?;", (new_status, job_id))
            conn.commit()
            return {"success": True, "status": new_status}

    def list_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM scheduler_logs ORDER BY id DESC LIMIT ?;", (limit,))
            return [dict(r) for r in cursor.fetchall()]

    async def execute_job(self, job: Dict[str, Any], duckdb_service) -> Dict[str, Any]:
        """Executes a single scheduled job and exports results to disk."""
        job_id = job.get("id")
        job_name = job.get("name")
        sql_code = job.get("sql_code", "").strip()
        export_fmt = (job.get("export_format") or "parquet").lower()
        partition_col = (job.get("partition_column") or "").strip()
        filename_base = (job.get("export_filename") or f"job_{job_id}_{int(time.time())}").strip()

        start_t = time.time()
        output_path = ""
        try:
            timestamp_suffix = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            conn = duckdb_service.connection

            if partition_col:
                # Partitioned export directory
                output_path = os.path.join(EXPORTS_DIR, f"{filename_base}_{timestamp_suffix}")
                os.makedirs(output_path, exist_ok=True)
                export_sql = f"COPY ({sql_code.rstrip(';')}) TO '{output_path}' (FORMAT {export_fmt.upper()}, PARTITION_BY ({partition_col}));"
            else:
                # Single file export
                ext = "parquet" if export_fmt == "parquet" else ("csv" if export_fmt == "csv" else "json")
                output_path = os.path.join(EXPORTS_DIR, f"{filename_base}_{timestamp_suffix}.{ext}")
                export_sql = f"COPY ({sql_code.rstrip(';')}) TO '{output_path}' (FORMAT {export_fmt.upper()});"

            conn.execute(export_sql)
            duration_ms = round((time.time() - start_t) * 1000, 2)

            # Record success log
            with sqlite3.connect(DB_PATH) as db:
                db.execute("""
                    INSERT INTO scheduler_logs (job_id, job_name, duration_ms, row_count, output_path, status)
                    VALUES (?, ?, ?, ?, ?, 'Success');
                """, (job_id, job_name, duration_ms, 0, output_path))
                # Update job last_run and next_run
                next_run = calculate_next_run(job.get("interval_str", "Every Hour")).isoformat()
                db.execute("UPDATE scheduled_jobs SET last_run=?, next_run=? WHERE id=?;",
                           (datetime.datetime.now().isoformat(), next_run, job_id))
                db.commit()

            return {"success": True, "output_path": output_path, "duration_ms": duration_ms}
        except Exception as e:
            duration_ms = round((time.time() - start_t) * 1000, 2)
            err_msg = str(e)
            logger.error(f"Scheduler job {job_name} failed: {err_msg}")
            with sqlite3.connect(DB_PATH) as db:
                db.execute("""
                    INSERT INTO scheduler_logs (job_id, job_name, duration_ms, row_count, output_path, status, error_message)
                    VALUES (?, ?, ?, ?, ?, 'Failed', ?);
                """, (job_id, job_name, duration_ms, 0, output_path, err_msg))
                db.commit()
            return {"success": False, "error": err_msg}

    async def scheduler_loop(self, duckdb_service):
        """Asynchronous background scheduler loop running inside FastAPI."""
        logger.info("Background ETL Scheduler loop started.")
        self._is_running = True
        while self._is_running:
            try:
                now = datetime.datetime.now().isoformat()
                with sqlite3.connect(DB_PATH) as db:
                    db.row_factory = sqlite3.Row
                    cursor = db.execute("""
                        SELECT * FROM scheduled_jobs 
                        WHERE status = 'Active' AND (next_run <= ? OR next_run IS NULL);
                    """, (now,))
                    due_jobs = [dict(r) for r in cursor.fetchall()]

                for job in due_jobs:
                    logger.info(f"Running scheduled ETL job: {job['name']}")
                    await self.execute_job(job, duckdb_service)

            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")

            # Sleep 30 seconds between checks
            await asyncio.sleep(30)
