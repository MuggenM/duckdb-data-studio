from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, Optional
from pydantic import BaseModel
from web.dependencies import get_duckdb_service
from web.services.duckdb_service import DuckDBService
from web.services.copilot_service import (
    get_autocomplete_metadata,
    generate_copilot_sql,
    fix_sql_error
)

router = APIRouter(tags=["AI Copilot & SQL"])


class CopilotGenerateRequest(BaseModel):
    prompt: str
    current_query: Optional[str] = None
    selection: Optional[str] = None
    provider: Optional[str] = "heuristic"
    model: Optional[str] = "default"
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class CopilotFixRequest(BaseModel):
    sql: str
    error_message: str
    provider: Optional[str] = "heuristic"
    model: Optional[str] = "default"
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@router.get("/api/sql/autocomplete-metadata")
async def autocomplete_metadata(service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """
    Returns DuckDB schema, functions, and keywords formatted for Monaco Editor autocomplete.
    Cached in-memory with automatic TTL.
    """
    return get_autocomplete_metadata(service)


@router.post("/api/copilot/generate")
async def copilot_generate(req: CopilotGenerateRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Generates DuckDB SQL from natural language instructions."""
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")
    return await generate_copilot_sql(
        prompt=req.prompt,
        current_query=req.current_query,
        selection=req.selection,
        provider=req.provider or "heuristic",
        model=req.model or "default",
        api_key=req.api_key,
        base_url=req.base_url,
        duckdb_service=service
    )


@router.post("/api/copilot/fix")
async def copilot_fix(req: CopilotFixRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Self-healing Auto-Fix loop: Resolves broken SQL based on DuckDB error trace."""
    if not req.sql.strip() or not req.error_message.strip():
        raise HTTPException(status_code=400, detail="SQL and error_message are required.")
    return await fix_sql_error(
        sql=req.sql,
        error_message=req.error_message,
        provider=req.provider or "heuristic",
        model=req.model or "default",
        api_key=req.api_key,
        base_url=req.base_url,
        duckdb_service=service
    )
