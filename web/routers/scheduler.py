from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from web.dependencies import get_duckdb_service
from web.services.duckdb_service import DuckDBService
from web.services.scheduler_service import SchedulerService

router = APIRouter(prefix="/api/scheduler", tags=["ETL Scheduler"])


class ScheduledJobRequest(BaseModel):
    id: Optional[int] = None
    name: str
    sql_code: str
    interval_str: Optional[str] = "Every Hour"
    export_format: Optional[str] = "parquet"
    partition_column: Optional[str] = ""
    export_filename: Optional[str] = ""
    status: Optional[str] = "Active"


@router.get("/jobs")
async def list_jobs() -> List[Dict[str, Any]]:
    service = SchedulerService.get_instance()
    return service.list_jobs()


@router.post("/jobs")
async def save_job(req: ScheduledJobRequest) -> Dict[str, Any]:
    if not req.name.strip() or not req.sql_code.strip():
        raise HTTPException(status_code=400, detail="Job name and SQL code are required.")
    service = SchedulerService.get_instance()
    job_id = service.create_or_update_job(req.dict())
    return {"success": True, "id": job_id}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int) -> Dict[str, Any]:
    service = SchedulerService.get_instance()
    success = service.delete_job(job_id)
    return {"success": success}


@router.post("/jobs/{job_id}/toggle")
async def toggle_job(job_id: int) -> Dict[str, Any]:
    service = SchedulerService.get_instance()
    return service.toggle_job_status(job_id)


@router.post("/jobs/{job_id}/run")
async def run_job_now(job_id: int, duckdb_service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    service = SchedulerService.get_instance()
    jobs = [j for j in service.list_jobs() if j["id"] == job_id]
    if not jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    res = await service.execute_job(jobs[0], duckdb_service)
    return res


@router.get("/logs")
async def list_logs(limit: int = 50) -> List[Dict[str, Any]]:
    service = SchedulerService.get_instance()
    return service.list_logs(limit=limit)
