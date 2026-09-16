from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, List
from web.dependencies import get_duckdb_service
from web.services.duckdb_service import DuckDBService
from web.services.copilot_service import invalidate_autocomplete_cache

router = APIRouter(prefix="/api/catalog", tags=["Catalog"])

@router.get("/tree")
async def get_catalog_tree(service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Returns full catalog tree containing attached databases, schemas, tables, and columns."""
    return service.get_catalog_tree()

@router.get("/databases")
async def get_databases(service: DuckDBService = Depends(get_duckdb_service)) -> List[Dict[str, Any]]:
    """Returns list of attached databases."""
    try:
        df = service.connection.execute("SELECT database_name, path, type, readonly FROM duckdb_databases();").df()
        res = []
        for _, row in df.iterrows():
            db_name = row['database_name']
            if db_name in ('system', 'temp'):
                continue
            res.append({
                'name': db_name,
                'path': row['path'],
                'type': str(row['type']).upper(),
                'read_only': bool(row['readonly'])
            })
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{database}/tables")
async def get_database_tables(database: str, service: DuckDBService = Depends(get_duckdb_service)) -> List[Dict[str, Any]]:
    """Returns all tables within a specific attached database."""
    try:
        rows = service.connection.execute(f"SHOW TABLES FROM {database};").fetchall()
        return [{"name": r[0]} for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/refresh")
async def refresh_catalog(service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Refreshes catalog metadata and invalidates autocomplete cache."""
    invalidate_autocomplete_cache()
    tree = service.get_catalog_tree()
    return {"success": True, "tree": tree}
