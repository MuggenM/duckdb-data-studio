import os
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from delta_inspector import discover_all_delta_tables, get_delta_table_history
from delta_optimizer import get_table_health_metrics, run_vacuum_delta_table, run_compaction_delta_table
from web.dependencies import get_duckdb_service
from web.services.duckdb_service import DuckDBService

logger = logging.getLogger("duckdb_studio.delta")
router = APIRouter(prefix="/api/delta", tags=["Delta Lake"])


class VacuumRequest(BaseModel):
    table_uri: str
    retention_hours: Optional[int] = 168
    dry_run: Optional[bool] = True


class OptimizeRequest(BaseModel):
    table_uri: str
    target_file_size_mb: Optional[int] = 128


@router.get("/tables")
async def list_delta_tables(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Discovers all Delta Lake tables in configured S3/Garage buckets and local directories."""
    try:
        tables = discover_all_delta_tables(force_refresh=force_refresh)
        return tables
    except Exception as e:
        logger.error(f"Failed to discover delta tables: {e}")
        return []


@router.get("/inspect")
async def inspect_table(table_uri: str) -> Dict[str, Any]:
    """Inspects detailed schema, version history, file count, and health of a Delta table."""
    if not table_uri:
        raise HTTPException(status_code=400, detail="table_uri is required.")
    try:
        history = get_delta_table_history(table_uri)
        health = get_table_health_metrics(table_uri)
        return {
            "success": True,
            "table_uri": table_uri,
            "history": history,
            "health": health
        }
    except Exception as e:
        logger.error(f"Error inspecting delta table {table_uri}: {e}")
        return {"success": False, "error": str(e)}


@router.post("/vacuum")
async def vacuum_table(req: VacuumRequest) -> Dict[str, Any]:
    """Executes VACUUM operation to remove tombstoned/stale files older than retention hours."""
    if not req.table_uri:
        raise HTTPException(status_code=400, detail="table_uri is required.")
    try:
        res = run_vacuum_delta_table(
            s3_uri=req.table_uri,
            retention_hours=req.retention_hours or 168,
            dry_run=req.dry_run
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vacuum failed: {str(e)}")


@router.post("/optimize")
async def optimize_table(req: OptimizeRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Compacts small Parquet files in a Delta table into optimized target file sizes."""
    if not req.table_uri:
        raise HTTPException(status_code=400, detail="table_uri is required.")
    try:
        res = run_compaction_delta_table(
            duckdb_conn=service.connection,
            s3_uri=req.table_uri,
            target_file_size_mb=req.target_file_size_mb or 128
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimize compaction failed: {str(e)}")
