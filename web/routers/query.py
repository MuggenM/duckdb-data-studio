import io
import csv
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from web.dependencies import get_duckdb_service
from web.services.duckdb_service import DuckDBService

router = APIRouter(prefix="/api/sql", tags=["Query Execution"])


class QueryRunRequest(BaseModel):
    sql: str
    limit: Optional[int] = 10000
    params: Optional[Dict[str, Any]] = None
    database: Optional[str] = "main"


class DetectParamsRequest(BaseModel):
    sql: str


class ExportRequest(BaseModel):
    sql: str
    format: Optional[str] = "csv"  # csv or parquet
    params: Optional[Dict[str, Any]] = None


@router.post("/run")
async def run_query(req: QueryRunRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Executes ad-hoc SQL query against DuckDB."""
    return service.execute_query(
        sql=req.sql,
        limit=req.limit or 10000,
        params=req.params,
        database_target=req.database or "main"
    )


@router.post("/detect-params")
async def detect_params(req: DetectParamsRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Detects parameters in SQL query template."""
    params = service.detect_parameters(req.sql)
    return {"params": params}


@router.get("/history")
async def get_history(limit: int = Query(50, ge=1, le=500), service: DuckDBService = Depends(get_duckdb_service)) -> List[Dict[str, Any]]:
    """Fetches recent query execution history."""
    return service.get_query_history(limit=limit)


@router.delete("/history/{history_id}")
async def delete_history_item(history_id: int, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Deletes a specific history record."""
    success = service.delete_query_history(history_id)
    return {"success": success}


@router.delete("/history")
async def clear_history(service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Clears all query history records."""
    success = service.clear_query_history()
    return {"success": success}


@router.post("/profile")
async def profile_query(req: QueryRunRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Executes a query with PRAGMA enable_profiling='json' and returns the profile tree."""
    return service.execute_profile(req.sql)


@router.post("/export")
async def export_query(req: ExportRequest, service: DuckDBService = Depends(get_duckdb_service)):
    """Exports query results directly as CSV or Parquet."""
    sql = req.sql.strip()
    if not sql:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    final_sql = service.substitute_parameters(sql, req.params or {})
    export_fmt = (req.format or "csv").lower()

    try:
        if export_fmt == "parquet":
            # Direct Arrow / Parquet stream
            df = service.connection.execute(final_sql).df()
            buf = io.BytesIO()
            df.to_parquet(buf, index=False)
            buf.seek(0)
            return StreamingResponse(
                buf,
                media_type="application/octet-stream",
                headers={"Content-Disposition": "attachment; filename=query_results.parquet"}
            )
        else:
            # CSV stream
            res = service.connection.execute(final_sql)
            columns = [desc[0] for desc in res.description] if res.description else ["result"]
            rows = res.fetchall()

            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(columns)
            writer.writerows(rows)
            buf.seek(0)

            return StreamingResponse(
                io.BytesIO(buf.getvalue().encode("utf-8")),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=query_results.csv"}
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


class SaveQueryRequest(BaseModel):
    name: str
    query: str
    description: Optional[str] = ""


@router.get("/saved")
async def get_saved_queries(service: DuckDBService = Depends(get_duckdb_service)) -> List[Dict[str, Any]]:
    """Retrieves all saved queries."""
    return service.get_saved_queries()


@router.post("/saved")
async def save_query(req: SaveQueryRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Saves a named query into the store."""
    if not req.name.strip() or not req.query.strip():
        raise HTTPException(status_code=400, detail="Name and query cannot be empty.")
    saved_id = service.save_query(req.name.strip(), req.query.strip(), req.description or "")
    return {"success": True, "id": saved_id}


@router.delete("/saved/{query_id}")
async def delete_saved_query(query_id: int, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Deletes a saved query by ID."""
    success = service.delete_saved_query(query_id)
    return {"success": success}
