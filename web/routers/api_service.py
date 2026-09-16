import os
import re
import time
import json
import sqlite3
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from web.dependencies import get_duckdb_service
from web.services.duckdb_service import DuckDBService, clean_json_value

logger = logging.getLogger("duckdb_studio.api_service")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DB_PATH = os.path.join(CONFIG_DIR, "studio_config.db")

router = APIRouter(tags=["Dynamic REST API Microservices"])


def init_api_tables():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dynamic_endpoints (
                    path TEXT PRIMARY KEY,
                    sql_code TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    parameters TEXT DEFAULT '[]',
                    rate_limit TEXT DEFAULT '60/minute',
                    security_enabled BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    status_code INTEGER NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
    except Exception as e:
        logger.error(f"Error initializing API SQLite tables: {e}")

init_api_tables()


class EndpointCreateRequest(BaseModel):
    path: str
    sql_code: str
    description: Optional[str] = ""
    parameters: Optional[List[str]] = []
    rate_limit: Optional[str] = "60/minute"
    security_enabled: Optional[bool] = False


@router.get("/api/v1/endpoints")
async def list_endpoints() -> List[Dict[str, Any]]:
    """Lists all active dynamic REST API microservices with their performance stats."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("""
            SELECT e.*, 
                   COUNT(m.id) AS total_invocations,
                   COALESCE(ROUND(AVG(m.latency_ms), 2), 0) AS avg_latency_ms
            FROM dynamic_endpoints e
            LEFT JOIN api_metrics m ON e.path = m.path
            GROUP BY e.path
            ORDER BY e.updated_at DESC;
        """)
        results = []
        for r in cursor.fetchall():
            d = dict(r)
            try:
                d["parameters"] = json.loads(d["parameters"]) if isinstance(d["parameters"], str) else d["parameters"]
            except Exception:
                d["parameters"] = []
            results.append(d)
        return results


@router.post("/api/v1/endpoints")
async def save_endpoint(req: EndpointCreateRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Compiles and registers a new dynamic REST API endpoint from SQL."""
    clean_path = req.path.strip().lstrip("/").replace(" ", "-")
    if not clean_path or not req.sql_code.strip():
        raise HTTPException(status_code=400, detail="Path and SQL code are required.")

    # Auto-detect parameters if not provided
    detected_params = req.parameters or service.detect_parameters(req.sql_code)
    params_json = json.dumps(detected_params)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO dynamic_endpoints (path, sql_code, description, parameters, rate_limit, security_enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(path) DO UPDATE SET
                sql_code=excluded.sql_code,
                description=excluded.description,
                parameters=excluded.parameters,
                rate_limit=excluded.rate_limit,
                security_enabled=excluded.security_enabled,
                updated_at=CURRENT_TIMESTAMP;
        """, (clean_path, req.sql_code.strip(), req.description or "", params_json, req.rate_limit or "60/minute", 1 if req.security_enabled else 0))
        conn.commit()

    return {"success": True, "path": clean_path, "url": f"/api/v1/routes/{clean_path}"}


@router.delete("/api/v1/endpoints/{path:path}")
async def delete_endpoint(path: str) -> Dict[str, Any]:
    """Deletes a dynamic REST API endpoint."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM dynamic_endpoints WHERE path = ?;", (path,))
        conn.commit()
    return {"success": True}


@router.get("/api/v1/endpoints/metrics")
async def get_metrics() -> Dict[str, Any]:
    """Returns live aggregated KPI performance telemetry for dynamic REST APIs."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        total_reqs = conn.execute("SELECT COUNT(*) AS total FROM api_metrics;").fetchone()["total"]
        avg_lat = conn.execute("SELECT COALESCE(ROUND(AVG(latency_ms), 2), 0) AS avg FROM api_metrics;").fetchone()["avg"]
        success_reqs = conn.execute("SELECT COUNT(*) AS total FROM api_metrics WHERE status_code = 200;").fetchone()["total"]
        success_rate = round((success_reqs / total_reqs * 100), 1) if total_reqs > 0 else 100.0

        routes = conn.execute("""
            SELECT path, COUNT(*) as invocations, ROUND(AVG(latency_ms), 2) as avg_latency,
                   MAX(timestamp) as last_invoked
            FROM api_metrics
            GROUP BY path
            ORDER BY invocations DESC LIMIT 20;
        """).fetchall()

        return {
            "total_requests": total_reqs,
            "average_latency_ms": avg_lat,
            "success_rate_pct": success_rate,
            "routes": [dict(r) for r in routes]
        }


@router.post("/api/v1/endpoints/metrics/reset")
async def reset_metrics() -> Dict[str, Any]:
    """Truncates the API telemetry logs."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM api_metrics;")
        conn.commit()
    return {"success": True}


# Dynamic catch-all invocation handler for compiled endpoints
@router.api_route("/api/v1/routes/{endpoint_path:path}", methods=["GET", "POST"])
async def execute_dynamic_endpoint(endpoint_path: str, request: Request, service: DuckDBService = Depends(get_duckdb_service)):
    """
    Executes compiled SQL query dynamically with auto-parsed query parameters,
    safe pagination (limit & offset), and telemetry latency logging.
    """
    start_t = time.time()
    clean_path = endpoint_path.strip().lstrip("/")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM dynamic_endpoints WHERE path = ?;", (clean_path,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Dynamic API endpoint '/api/v1/routes/{clean_path}' not found.")

    sql_template = row["sql_code"]

    # Parse parameters from query string and JSON body
    params = dict(request.query_params)
    if request.method == "POST":
        try:
            body_json = await request.json()
            if isinstance(body_json, dict):
                params.update(body_json)
        except Exception:
            pass

    # Extract limit & offset for pagination
    limit = min(int(params.pop("limit", 100)), 10000)
    offset = max(int(params.pop("offset", 0)), 0)

    # Substitute parameters
    substituted_sql = service.substitute_parameters(sql_template, params)

    # Wrap in pagination
    paged_sql = f"SELECT * FROM ({substituted_sql.rstrip(';')}) LIMIT {limit} OFFSET {offset};"

    status_code = 200
    try:
        cursor = service.connection.cursor()
        res = cursor.execute(paged_sql)
        duration_ms = round((time.time() - start_t) * 1000, 2)
        columns = [d[0] for d in res.description] if res.description else ["result"]
        rows = res.fetchall()

        # Sanitize for compliant JSON output
        clean_rows = [{columns[i]: clean_json_value(cell) for i, cell in enumerate(r)} for r in rows]

        # Log metrics
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO api_metrics (path, latency_ms, status_code) VALUES (?, ?, ?);",
                         (clean_path, duration_ms, status_code))
            conn.commit()

        return {
            "endpoint": clean_path,
            "status": "success",
            "count": len(clean_rows),
            "limit": limit,
            "offset": offset,
            "latency_ms": duration_ms,
            "data": clean_rows
        }
    except Exception as e:
        duration_ms = round((time.time() - start_t) * 1000, 2)
        status_code = 500
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO api_metrics (path, latency_ms, status_code) VALUES (?, ?, ?);",
                         (clean_path, duration_ms, status_code))
            conn.commit()
        raise HTTPException(status_code=500, detail=f"Query execution error in dynamic endpoint: {str(e)}")
