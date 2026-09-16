import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.dbt_service import (
    get_dbt_status,
    list_dbt_models,
    get_dbt_model_detail,
    run_dbt_cli,
    _load_runs_history,
    preview_dbt_model_data,
    preview_cte_step,
    add_dbt_test,
    delete_dbt_test,
    add_dbt_source,
    delete_dbt_source,
    create_dbt_model,
    update_dbt_model_code,
    delete_dbt_model,
    list_available_delta_tables,
    preview_dbt_source_data,
)

logger = logging.getLogger("duckdb_studio.dbt_router")
router = APIRouter(prefix="/api/dbt", tags=["dbt"])

class DbtRunRequest(BaseModel):
    action: str = "run"
    select: Optional[str] = None
    full_refresh: bool = False
    target: str = "dev"

class DbtAddTestRequest(BaseModel):
    model_name: str
    column_name: Optional[str] = None
    test_type: str = "not_null"
    parameters: Optional[Dict[str, Any]] = None
    sql_text: Optional[str] = None
    test_name: Optional[str] = None

class DbtDeleteTestRequest(BaseModel):
    model_name: str
    column_name: Optional[str] = None
    test_type: Optional[str] = None
    test_name: Optional[str] = None

class DbtSourceAddRequest(BaseModel):
    source_name: str = "formula1"
    table_name: str
    description: Optional[str] = None

class DbtSourceDeleteRequest(BaseModel):
    source_name: str
    table_name: str

class DbtModelCreateRequest(BaseModel):
    name: str
    layer: str = "staging"
    materialization: str = "view"
    sql_content: Optional[str] = None
    description: Optional[str] = ""

class DbtModelUpdateRequest(BaseModel):
    sql_content: str
    description: Optional[str] = None

@router.get("/status")
async def get_status():
    return get_dbt_status()

@router.get("/models")
async def get_models():
    return list_dbt_models()

@router.get("/models/{model_name}")
async def get_model(model_name: str):
    detail = get_dbt_model_detail(model_name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")
    return detail

@router.post("/run")
async def run_dbt(payload: DbtRunRequest):
    return run_dbt_cli(
        action=payload.action,
        select=payload.select,
        full_refresh=payload.full_refresh,
        target=payload.target
    )

@router.get("/runs")
async def get_runs():
    return {"runs": _load_runs_history()}

@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    runs = _load_runs_history()
    matched = next((r for r in runs if r["run_id"] == run_id), None)
    if not matched:
        raise HTTPException(status_code=404, detail="Run record not found")
    return matched

@router.get("/preview/{model_name}")
async def preview_model(model_name: str, limit: int = 50):
    return preview_dbt_model_data(model_name, limit=limit)

@router.get("/cte-preview/{model_name}/{cte_name}")
async def preview_cte(model_name: str, cte_name: str, limit: int = 50):
    return preview_cte_step(model_name, cte_name, limit=limit)

@router.post("/tests")
async def add_test(payload: DbtAddTestRequest):
    try:
        return add_dbt_test(
            model_name=payload.model_name,
            column_name=payload.column_name,
            test_type=payload.test_type,
            parameters=payload.parameters,
            sql_text=payload.sql_text,
            test_name=payload.test_name
        )
    except Exception as e:
        logger.error(f"Error adding dbt test: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/tests")
async def delete_test(payload: DbtDeleteTestRequest):
    try:
        return delete_dbt_test(
            model_name=payload.model_name,
            column_name=payload.column_name,
            test_type=payload.test_type,
            test_name=payload.test_name
        )
    except Exception as e:
        logger.error(f"Error deleting dbt test: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/available-delta-tables")
async def get_available_delta_tables():
    return {"tables": list_available_delta_tables()}

@router.post("/sources")
async def add_source(payload: DbtSourceAddRequest):
    try:
        return add_dbt_source(
            source_name=payload.source_name,
            table_name=payload.table_name,
            description=payload.description
        )
    except Exception as e:
        logger.error(f"Error adding dbt source: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/sources")
async def delete_source(payload: DbtSourceDeleteRequest):
    try:
        return delete_dbt_source(
            source_name=payload.source_name,
            table_name=payload.table_name
        )
    except Exception as e:
        logger.error(f"Error deleting dbt source: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/sources/{source_name}/{table_name}/preview")
async def preview_source(source_name: str, table_name: str, limit: int = 50):
    return preview_dbt_source_data(source_name, table_name, limit=limit)

@router.post("/models")
async def create_model(payload: DbtModelCreateRequest):
    try:
        return create_dbt_model(
            name=payload.name,
            layer=payload.layer,
            materialization=payload.materialization,
            sql_content=payload.sql_content,
            description=payload.description
        )
    except Exception as e:
        logger.error(f"Error creating dbt model: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/models/{model_name}")
async def update_model_code(model_name: str, payload: DbtModelUpdateRequest):
    try:
        return update_dbt_model_code(
            model_name=model_name,
            sql_content=payload.sql_content,
            description=payload.description
        )
    except Exception as e:
        logger.error(f"Error updating dbt model: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/models/{model_name}")
async def delete_model(model_name: str):
    try:
        return delete_dbt_model(model_name)
    except Exception as e:
        logger.error(f"Error deleting dbt model: {e}")
        raise HTTPException(status_code=400, detail=str(e))
