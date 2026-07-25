import re
import time
import datetime
import json
import duckdb
from fastapi import Request, Response, HTTPException
from fastapi.responses import StreamingResponse
from nicegui import app
from slowapi import Limiter
from slowapi.util import get_remote_address

from config_manager import SQLiteConfigManager, get_main_db_path, APP_SETTINGS
from db_explorer import DB_CONFIG, load_attached_databases_for_connection
from utils import (
    detect_parameters, 
    substitute_sql_parameters, 
    verify_jwt_token, 
    split_sql_trailing_clauses
)

# Initialize slowapi limiter
limiter = Limiter(key_func=get_remote_address)

def get_dynamic_rate_limit(request: Request = None) -> str:
    """Resolve dynamic rate-limit string per endpoint from DB, falling back to dynamic settings default."""
    if request is None:
        return APP_SETTINGS.get("default_rate_limit", "5/minute")
    path = request.url.path
    match = re.match(r'^/api/(.+?)(?:/stream)?$', path)
    if match:
        endpoint_path = match.group(1)
        if endpoint_path != "list-endpoints":
            try:
                config_db = SQLiteConfigManager()
                res = config_db.query_one("SELECT rate_limit FROM _duckdb_studio_api_endpoints WHERE path = ?;", [endpoint_path])
                if res and res['rate_limit'] and res['rate_limit'].strip():
                    return res['rate_limit'].strip()
            except Exception as e:
                print(f"WARNING: Dynamic rate limit lookup failed for {endpoint_path}: {e}", flush=True)
            
    return APP_SETTINGS.get("default_rate_limit", "5/minute")


@app.get("/api/list-endpoints")
def list_endpoints():
    config_db = SQLiteConfigManager()
    try:
        endpoints = config_db.query_all("SELECT path, description FROM _duckdb_studio_api_endpoints;")
        return [dict(row) for row in endpoints]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/{endpoint_path:path}/stream", include_in_schema=False)
@limiter.limit(get_dynamic_rate_limit)
def handle_streaming_endpoint(endpoint_path: str, request: Request):
    start_time = time.time()
    status_code = 200
    error_message = None
    config_db = SQLiteConfigManager()
    
    db_path = get_main_db_path()
    conn = None
    try:
        conn = duckdb.connect(db_path, config=DB_CONFIG)
        load_attached_databases_for_connection(conn)
        
        # Load the endpoint query from SQLite database
        res = config_db.query_one(
            "SELECT sql_code, security_enabled FROM _duckdb_studio_api_endpoints WHERE path = ?;",
            [endpoint_path]
        )
        
        if not res:
            status_code = 404
            error_message = f"API Endpoint '/api/{endpoint_path}' not found"
            raise HTTPException(status_code=404, detail=error_message)
            
        sql_code, security_enabled = res['sql_code'], bool(res['security_enabled'])
        
        # Substitute double-brace parameters ({{param}}) using query parameters
        double_brace_params = detect_parameters(sql_code)
        if double_brace_params:
            brace_values = {}
            for p in double_brace_params:
                val = request.query_params.get(p)
                brace_values[p] = val
            sql_code = substitute_sql_parameters(sql_code, brace_values)
        
        # Enforce JWT Authorization if enabled for this endpoint
        if security_enabled:
            auth_header = request.headers.get("Authorization")
            verify_jwt_token(auth_header, secret=APP_SETTINGS.get("jwt_secret", "duckdb_studio_secret_key_1337"))
            
        # Parse query parameters from request query params to bind them if the query has placeholders
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
        # Log telemetry metrics to SQLite
        try:
            latency_ms = (time.time() - start_time) * 1000.0
            config_db.execute("""
                INSERT INTO _duckdb_studio_api_metrics (endpoint_path, timestamp, latency_ms, status_code, error_message)
                VALUES (?, ?, ?, ?, ?);
            """, [endpoint_path + "/stream", datetime.datetime.now().isoformat(), latency_ms, status_code, error_message])
        except Exception as log_err:
            print(f"ERROR logging API streaming telemetry metrics: {log_err}", flush=True)


@app.get("/api/{endpoint_path:path}", include_in_schema=False)
@limiter.limit(get_dynamic_rate_limit)
def handle_dynamic_endpoint(endpoint_path: str, request: Request):
    start_time = time.time()
    status_code = 200
    error_message = None
    config_db = SQLiteConfigManager()
    
    db_path = get_main_db_path()
    conn = None
    try:
        conn = duckdb.connect(db_path, config=DB_CONFIG)
        load_attached_databases_for_connection(conn)
        
        # Load the endpoint query from SQLite database
        res = config_db.query_one(
            "SELECT sql_code, security_enabled FROM _duckdb_studio_api_endpoints WHERE path = ?;",
            [endpoint_path]
        )
        
        if not res:
            status_code = 404
            error_message = f"API Endpoint '/api/{endpoint_path}' not found"
            raise HTTPException(status_code=404, detail=error_message)
            
        sql_code, security_enabled = res['sql_code'], bool(res['security_enabled'])
        
        # Substitute double-brace parameters ({{param}}) using query parameters
        double_brace_params = detect_parameters(sql_code)
        if double_brace_params:
            brace_values = {}
            for p in double_brace_params:
                val = request.query_params.get(p)
                brace_values[p] = val
            sql_code = substitute_sql_parameters(sql_code, brace_values)
        
        # Enforce JWT Authorization if enabled for this endpoint
        if security_enabled:
            auth_header = request.headers.get("Authorization")
            verify_jwt_token(auth_header, secret=APP_SETTINGS.get("jwt_secret", "duckdb_studio_secret_key_1337"))
        
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
        placeholders = re.findall(r'\$([a-zA-Z0-9_]+)', sql_code)
        placeholders = list(dict.fromkeys(placeholders))
        
        # Build parameter dictionary from request query params
        bind_params = {}
        for param in placeholders:
            if param.lower() == 'limit':
                bind_params[param] = limit + 1
                continue
            if param.lower() == 'offset':
                bind_params[param] = offset
                continue
                
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
 
        lower_placeholders = [p.lower() for p in placeholders]
        if 'limit' in lower_placeholders:
            bind_params['limit'] = limit + 1
        if 'offset' in lower_placeholders:
            bind_params['offset'] = offset
 
        sql_clean = sql_code.strip()
        if sql_clean.endswith(';'):
            sql_clean = sql_clean[:-1].strip()
            
        sql_clean, trailing = split_sql_trailing_clauses(sql_clean)
        
        has_hard_limit = False
        if trailing:
            limit_match = re.search(r'(?i)\bLIMIT\s+(\d+)\b', trailing)
            if limit_match:
                has_hard_limit = True
                hard_limit = int(limit_match.group(1))
                if hard_limit > 10000:
                    trailing = re.sub(r'(?i)\bLIMIT\s+\d+\b', 'LIMIT 10000', trailing)
 
        has_limit_placeholder = 'limit' in lower_placeholders
        
        if not has_limit_placeholder and not has_hard_limit:
            sql_to_run = f"SELECT * FROM ({sql_clean}) LIMIT {limit + 1} OFFSET {offset};"
        else:
            if trailing:
                sql_to_run = sql_clean + "\n" + trailing.strip()
            else:
                sql_to_run = sql_code
            
        df = conn.execute(sql_to_run, bind_params).df()
        
        if len(df) > limit:
            has_more = True
            df = df.iloc[:limit]
        else:
            has_more = False
            
        records = df.to_dict(orient="records")
        
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
        if isinstance(e, HTTPException):
            status_code = e.status_code
            error_message = e.detail
            raise e
        status_code = 500
        error_message = str(e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            latency_ms = (time.time() - start_time) * 1000.0
            config_db.execute("""
                INSERT INTO _duckdb_studio_api_metrics (endpoint_path, timestamp, latency_ms, status_code, error_message)
                VALUES (?, ?, ?, ?, ?);
            """, [endpoint_path, datetime.datetime.now().isoformat(), latency_ms, status_code, error_message])
        except Exception as log_err:
            print(f"ERROR logging API telemetry metrics: {log_err}", flush=True)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def sync_fastapi_dynamic_routes():
    # Remove previously registered dynamic routes from the API schema list
    app.routes[:] = [r for r in app.routes if not (
        hasattr(r, 'path') and 
        r.path.startswith("/api/") and 
        r.path != "/api/list-endpoints" and
        "{endpoint_path}" not in r.path
    )]
    
    config_db = SQLiteConfigManager()
    try:
        endpoints = config_db.query_all("SELECT path, description FROM _duckdb_studio_api_endpoints;")
    except Exception as e:
        print(f"ERROR: failed to load api endpoints for route sync: {e}", flush=True)
        endpoints = []
        
    for ep in endpoints:
        path = ep['path']
        desc = ep['description'] or "Dynamic SQL API Endpoint"
        
        def make_handler(ep_path=path):
            async def dynamic_get(request: Request):
                return handle_dynamic_endpoint(ep_path, request)
            return dynamic_get
            
        def make_stream_handler(ep_path=path):
            async def dynamic_stream(request: Request):
                return handle_streaming_endpoint(ep_path, request)
            return dynamic_stream
            
        app.add_api_route(
            path=f"/api/{path}",
            endpoint=make_handler(),
            methods=["GET"],
            name=f"api_{path.replace('/', '_')}",
            description=desc
        )
        
        app.add_api_route(
            path=f"/api/{path}/stream",
            endpoint=make_stream_handler(),
            methods=["GET"],
            name=f"api_{path.replace('/', '_')}_stream",
            description=f"Streaming chunked CSV response for {desc}"
        )
        
    app.openapi_schema = None
