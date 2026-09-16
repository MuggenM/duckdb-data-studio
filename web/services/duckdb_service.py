import os
import re
import time
import math
import json
import sqlite3
import datetime
import decimal
import logging
from typing import Dict, Any, List, Optional, Union
from collections import defaultdict

import duckdb
import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger("duckdb_studio.service")
logging.basicConfig(level=logging.INFO)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATABASES_DIR = os.path.join(BASE_DIR, "databases")
DUCKLAKE_DIR = os.path.join(BASE_DIR, "ducklake")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(DATABASES_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)

DB_CONFIG = {
    'custom_user_agent': 'DuckDB-Studio/2.0 (FastAPI-Monaco)',
    'threads': '4',
    'memory_limit': '2GB',
    'preserve_insertion_order': 'false',
    'http_keep_alive': 'true',
    'enable_object_cache': 'true',
    'http_timeout': '10'
}


def clean_json_value(v: Any) -> Any:
    """
    Sanitizes individual values for RFC 7159/8259 compliant JSON serialization.
    Replaces NaNs/Infinities with None and serializes datetimes/decimals cleanly.
    """
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass

    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    elif isinstance(v, (np.floating,)):
        if np.isnan(v) or np.isinf(v):
            return None
        return float(v)
    elif isinstance(v, (np.integer,)):
        return int(v)
    elif isinstance(v, (np.bool_,)):
        return bool(v)
    elif isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    elif isinstance(v, decimal.Decimal):
        if v.is_nan() or v.is_infinite():
            return None
        return float(v)
    elif isinstance(v, bytes):
        return v.hex()
    elif isinstance(v, (list, tuple, set)):
        return [clean_json_value(x) for x in v]
    elif isinstance(v, dict):
        return {str(k): clean_json_value(sub_v) for k, sub_v in v.items()}
    return v


def resolve_db_path(raw_path: str) -> str:
    """Resolves container-internal paths to actual host or local filesystem paths."""
    if not raw_path:
        return raw_path
    if os.path.exists(raw_path):
        return raw_path
    if raw_path.startswith("/databases/"):
        rel = raw_path[len("/databases/"):]
        candidate = os.path.join(DATABASES_DIR, rel)
        if os.path.exists(candidate):
            return candidate
    if raw_path.startswith("/ducklake/"):
        rel = raw_path[len("/ducklake/"):]
        candidate = os.path.join(DUCKLAKE_DIR, rel)
        if os.path.exists(candidate):
            return candidate
    return raw_path


class DuckDBService:
    _instance = None

    def __init__(self, main_db_path: Optional[str] = None):
        if main_db_path is None:
            default_main = os.path.join(DATABASES_DIR, "main.duckdb")
            main_db_path = default_main
        self.main_db_path = main_db_path
        self._conn = None
        self._history_db_path = os.path.join(CONFIG_DIR, "studio_config.db")
        self._init_history_db()

    @classmethod
    def get_instance(cls) -> "DuckDBService":
        if cls._instance is None:
            cls._instance = DuckDBService()
        return cls._instance

    def _init_history_db(self):
        """Initializes SQLite query history database."""
        try:
            with sqlite3.connect(self._history_db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS query_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        query TEXT NOT NULL,
                        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        duration_ms REAL DEFAULT 0,
                        row_count INTEGER DEFAULT 0,
                        is_success BOOLEAN DEFAULT 1,
                        error_message TEXT,
                        database_target TEXT DEFAULT 'main'
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS saved_queries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        query TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize query history SQLite store: {e}")

    def save_query(self, name: str, query: str, description: str = "") -> int:
        """Saves a named SQL query to SQLite store."""
        try:
            with sqlite3.connect(self._history_db_path) as conn:
                cursor = conn.execute("""
                    INSERT INTO saved_queries (name, query, description, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP);
                """, (name, query, description))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Failed to save query '{name}': {e}")
            raise e

    def get_saved_queries(self) -> List[Dict[str, Any]]:
        """Retrieves all saved queries."""
        try:
            with sqlite3.connect(self._history_db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, name, query, description, created_at, updated_at
                    FROM saved_queries
                    ORDER BY id DESC;
                """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to fetch saved queries: {e}")
            return []

    def delete_saved_query(self, query_id: int) -> bool:
        """Deletes a saved query by ID."""
        try:
            with sqlite3.connect(self._history_db_path) as conn:
                conn.execute("DELETE FROM saved_queries WHERE id = ?;", (query_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to delete saved query {query_id}: {e}")
            return False

    def log_query_history(self, query: str, duration_ms: float, row_count: int, is_success: bool, error_message: Optional[str] = None, database_target: str = 'main'):
        """Logs an executed statement to SQLite query history."""
        try:
            with sqlite3.connect(self._history_db_path) as conn:
                conn.execute("""
                    INSERT INTO query_history (query, duration_ms, row_count, is_success, error_message, database_target)
                    VALUES (?, ?, ?, ?, ?, ?);
                """, (query, duration_ms, row_count, 1 if is_success else 0, error_message, database_target))
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to log query history: {e}")

    def get_query_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves recent query execution history."""
        try:
            with sqlite3.connect(self._history_db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, query, executed_at, duration_ms, row_count, is_success, error_message, database_target
                    FROM query_history
                    ORDER BY id DESC
                    LIMIT ?;
                """, (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to fetch query history: {e}")
            return []

    def delete_query_history(self, history_id: int) -> bool:
        """Deletes a specific history record."""
        try:
            with sqlite3.connect(self._history_db_path) as conn:
                conn.execute("DELETE FROM query_history WHERE id = ?;", (history_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to delete query history item {history_id}: {e}")
            return False

    def clear_query_history(self) -> bool:
        """Clears all query history records."""
        try:
            with sqlite3.connect(self._history_db_path) as conn:
                conn.execute("DELETE FROM query_history;")
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to clear query history: {e}")
            return False

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Lazily connects to DuckDB with graceful fallback to in-memory/read-only mode."""
        if self._conn is None:
            self._connect()
        return self._conn

    def _connect(self):
        """Creates DuckDB connection and auto-attaches configured databases."""
        try:
            # First attempt: Open main database directly
            if os.path.exists(self.main_db_path):
                try:
                    self._conn = duckdb.connect(self.main_db_path, read_only=False, config=DB_CONFIG)
                except duckdb.IOException as lock_err:
                    logger.info(f"Main database is locked by another process ({lock_err}). Opening in READ_ONLY mode.")
                    self._conn = duckdb.connect(self.main_db_path, read_only=True, config=DB_CONFIG)
            else:
                self._conn = duckdb.connect(self.main_db_path, config=DB_CONFIG)
        except Exception as e:
            logger.warning(f"Could not open file database at '{self.main_db_path}': {e}. Connecting to in-memory DuckDB.")
            self._conn = duckdb.connect(":memory:", config=DB_CONFIG)

        # Standard settings
        try:
            self._conn.execute("SET http_keep_alive=true;")
            self._conn.execute("SET enable_object_cache=true;")
            self._conn.execute("SET http_timeout=10;")
        except Exception as e:
            logger.warning(f"Failed to configure HTTP/S3 settings: {e}")

        # Auto-attach databases
        self._attach_all_configured_databases()

    def _attach_all_configured_databases(self):
        """Reads attached_databases.yaml and attaches all databases with read_only fallback."""
        config_path = os.path.join(CONFIG_DIR, "attached_databases.yaml")
        if not os.path.exists(config_path):
            return

        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            if not config or "databases" not in config:
                return

            for db in config.get("databases", []):
                db_name = db.get("name")
                db_type = (db.get("type") or "duckdb").lower()
                db_path = resolve_db_path(db.get("path"))
                options = db.get("options", {})
                is_read_only = db.get("read_only", False)

                if not db_name or not db_path:
                    continue

                self.attach_database(db_name, db_type, db_path, options, is_read_only)
        except Exception as e:
            logger.error(f"Error loading attached databases from {config_path}: {e}")

    def attach_database(self, db_name: str, db_type: str, db_path: str, options: dict = None, read_only: bool = False) -> bool:
        """Attaches a single external or local database to the DuckDB connection."""
        resolved_path = resolve_db_path(db_path)
        options = options or {}
        read_only_clause = " (READ_ONLY)" if read_only else ""

        try:
            if db_type == "ducklake":
                try:
                    self._conn.execute("INSTALL ducklake; LOAD ducklake;")
                except Exception:
                    pass
                data_path = options.get("data_path", "data_parquet/")
                sql = f"ATTACH 'ducklake:{resolved_path}' AS {db_name} (DATA_PATH '{data_path}');"
            elif db_type == "sqlite":
                try:
                    self._conn.execute("INSTALL sqlite; LOAD sqlite;")
                except Exception:
                    pass
                sql = f"ATTACH '{resolved_path}' AS {db_name} (TYPE sqlite{', READ_ONLY' if read_only else ''});"
            elif db_type == "postgres":
                try:
                    self._conn.execute("INSTALL postgres; LOAD postgres;")
                except Exception:
                    pass
                sql = f"ATTACH '{resolved_path}' AS {db_name} (TYPE postgres{', READ_ONLY' if read_only else ''});"
            else:  # duckdb
                # Try read-write first, fallback to read-only if locked
                try:
                    self._conn.execute(f"ATTACH '{resolved_path}' AS {db_name}{read_only_clause};")
                    logger.info(f"Attached {db_type} database '{db_name}' ({resolved_path})")
                    return True
                except duckdb.IOException as lock_e:
                    logger.info(f"Database '{db_name}' is locked. Attaching with (READ_ONLY)...")
                    self._conn.execute(f"ATTACH '{resolved_path}' AS {db_name} (READ_ONLY);")
                    logger.info(f"Attached {db_type} database '{db_name}' in READ_ONLY mode.")
                    return True

            self._conn.execute(sql)
            logger.info(f"Attached {db_type} database '{db_name}' successfully.")
            return True
        except Exception as e:
            logger.warning(f"Failed to attach database '{db_name}' ({db_type}): {e}")
            return False

    def detect_parameters(self, sql: str) -> List[str]:
        """Detects template parameters in SQL (e.g. {{ min_val }} or $min_val)."""
        curly_params = re.findall(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}', sql)
        dollar_params = re.findall(r'(?<!\$)\$([a-zA-Z0-9_]+)', sql)
        seen = set()
        params = []
        for p in curly_params + dollar_params:
            if p not in seen:
                seen.add(p)
                params.append(p)
        return params

    def substitute_parameters(self, sql: str, param_values: Dict[str, Any]) -> str:
        """Substitutes parameters securely into SQL template."""
        if not param_values:
            return sql

        def clean_val(val):
            if val is None:
                return "NULL"
            if isinstance(val, (int, float)):
                return str(val)
            s_val = str(val).replace("'", "''")
            return f"'{s_val}'"

        res_sql = sql
        for param_name, raw_val in param_values.items():
            replacement = clean_val(raw_val)
            escaped_name = re.escape(param_name)
            pattern_curly = r'\{\{\s*' + escaped_name + r'\s*\}\}'
            pattern_dollar = r'(?<!\$)\$' + escaped_name + r'\b'
            res_sql = re.sub(pattern_curly, replacement, res_sql)
            res_sql = re.sub(pattern_dollar, replacement, res_sql)
        return res_sql

    def execute_query(self, sql: str, limit: int = 10000, params: Optional[Dict[str, Any]] = None, database_target: str = 'main') -> Dict[str, Any]:
        """
        Executes an ad-hoc SQL query against DuckDB.
        Returns columns, sanitized rows, duration_ms, row count, and error state.
        """
        raw_sql = sql.strip()
        if not raw_sql:
            return {
                "success": False,
                "error": "Query cannot be empty.",
                "columns": [],
                "rows": [],
                "duration_ms": 0.0,
                "row_count": 0,
                "is_select": False
            }

        # Substitute parameters if provided
        final_sql = self.substitute_parameters(raw_sql, params or {})
        start_time = time.time()

        try:
            cursor = self.connection.cursor()
            result = cursor.execute(final_sql)
            duration_ms = round((time.time() - start_time) * 1000, 2)

            if result.description is not None:
                # SELECT or RETURNING statement
                columns = [desc[0] for desc in result.description]
                fetched_rows = result.fetchmany(limit)
                is_truncated = len(fetched_rows) >= limit
                
                # Sanitize rows for JSON compatibility
                clean_rows = [[clean_json_value(cell) for cell in row] for row in fetched_rows]
                row_count = len(clean_rows)

                self.log_query_history(
                    query=raw_sql,
                    duration_ms=duration_ms,
                    row_count=row_count,
                    is_success=True,
                    database_target=database_target
                )

                return {
                    "success": True,
                    "columns": columns,
                    "rows": clean_rows,
                    "row_count": row_count,
                    "duration_ms": duration_ms,
                    "is_select": True,
                    "truncated": is_truncated,
                    "error": None
                }
            else:
                # DDL / DML statement
                affected = result.rowcount if hasattr(result, "rowcount") else 0
                self.log_query_history(
                    query=raw_sql,
                    duration_ms=duration_ms,
                    row_count=affected,
                    is_success=True,
                    database_target=database_target
                )
                return {
                    "success": True,
                    "columns": ["Result"],
                    "rows": [[f"Statement executed successfully. Affected rows: {affected}"]],
                    "row_count": affected,
                    "duration_ms": duration_ms,
                    "is_select": False,
                    "truncated": False,
                    "error": None
                }

        except Exception as e:
            duration_ms = round((time.time() - start_time) * 1000, 2)
            error_str = str(e)
            self.log_query_history(
                query=raw_sql,
                duration_ms=duration_ms,
                row_count=0,
                is_success=False,
                error_message=error_str,
                database_target=database_target
            )
            return {
                "success": False,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "duration_ms": duration_ms,
                "is_select": False,
                "truncated": False,
                "error": error_str
            }

    def execute_profile(self, sql: str) -> Dict[str, Any]:
        """Executes a query with DuckDB PRAGMA profiling enabled and returns the JSON profile."""
        try:
            cursor = self.connection.cursor()
            cursor.execute("PRAGMA enable_profiling = 'json';")
            cursor.execute(sql)
            profile_json_str = cursor.execute("SELECT current_setting('profiling_output');").fetchone()[0]
            cursor.execute("PRAGMA disable_profiling;")
            try:
                profile_obj = json.loads(profile_json_str)
                return {"success": True, "profile": profile_obj}
            except Exception:
                return {"success": True, "profile_raw": profile_json_str}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_catalog_tree(self) -> Dict[str, Any]:
        """
        Builds a comprehensive catalog hierarchy:
        {
          "databases": [
            {
              "name": "car_rental",
              "schemas": [
                {
                  "name": "main",
                  "tables": [
                    {
                      "name": "cars",
                      "type": "BASE TABLE",
                      "full_name": "car_rental.main.cars",
                      "columns": [{"name": "id", "type": "UBIGINT"}, ...]
                    }
                  ]
                }
              ]
            }
          ]
        }
        """
        tree = {"databases": []}
        try:
            rows = self.connection.execute("SHOW ALL TABLES;").fetchall()
            db_map = defaultdict(lambda: defaultdict(list))

            for row in rows:
                db_name, schema_name, tbl_name, col_names, col_types = row[0], row[1], row[2], row[3], row[4]
                if tbl_name.startswith("__") or tbl_name.startswith("sqlite_"):
                    continue

                full_name = f"{db_name}.{schema_name}.{tbl_name}" if db_name not in ("memory", "temp") else tbl_name
                cols = [{"name": cname, "type": str(ctype).upper()} for cname, ctype in zip(col_names, col_types)]
                
                db_map[db_name][schema_name].append({
                    "name": tbl_name,
                    "full_name": full_name,
                    "columns": cols
                })

            for db_name, schemas in db_map.items():
                schema_list = []
                for s_name, tables in schemas.items():
                    schema_list.append({
                        "name": s_name,
                        "tables": sorted(tables, key=lambda t: t["name"])
                    })
                tree["databases"].append({
                    "name": db_name,
                    "schemas": sorted(schema_list, key=lambda s: s["name"])
                })

            tree["databases"].sort(key=lambda d: d["name"])
        except Exception as e:
            logger.error(f"Error generating catalog tree: {e}")

        return tree
