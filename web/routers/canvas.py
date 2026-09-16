import os
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from canvas_query_builder import compile_canvas_to_sql, compile_drawflow_json_to_sql
from web.dependencies import get_duckdb_service
from web.services.duckdb_service import DuckDBService

logger = logging.getLogger("duckdb_studio.canvas")
router = APIRouter(prefix="/api/canvas", tags=["Canvas Pipeline Studio"])


class CanvasCompileRequest(BaseModel):
    nodes: Optional[List[Dict[str, Any]]] = []
    connections: Optional[List[Dict[str, Any]]] = []
    drawflow: Optional[Dict[str, Any]] = None
    mode: Optional[str] = "standard"  # 'standard' or 'dbt'


@router.post("/compile-sql")
async def compile_canvas_sql(req: CanvasCompileRequest) -> Dict[str, Any]:
    """Compiles visual canvas nodes/drawflow into high-performance DuckDB SQL."""
    try:
        if req.drawflow:
            sql = compile_drawflow_json_to_sql(req.drawflow, mode=req.mode or "standard")
        else:
            graph_dict = {"nodes": req.nodes or [], "connections": req.connections or []}
            sql = compile_canvas_to_sql(graph_dict, mode=req.mode or "standard")
        return {"success": True, "sql": sql}
    except Exception as e:
        logger.error(f"Error compiling canvas SQL: {e}")
        return {"success": False, "error": str(e), "sql": "-- Error compiling canvas nodes"}


@router.post("/preview")
async def preview_canvas_pipeline(req: CanvasCompileRequest, service: DuckDBService = Depends(get_duckdb_service)) -> Dict[str, Any]:
    """Compiles canvas pipeline and executes a preview against DuckDB."""
    try:
        if req.drawflow:
            sql = compile_drawflow_json_to_sql(req.drawflow, mode=req.mode or "standard")
        else:
            graph_dict = {"nodes": req.nodes or [], "connections": req.connections or []}
            sql = compile_canvas_to_sql(graph_dict, mode=req.mode or "standard")

        result = service.execute_query(sql, limit=100)
        result["compiled_sql"] = sql
        return result
    except Exception as e:
        logger.error(f"Canvas preview execution error: {e}")
        return {"success": False, "error": str(e), "columns": [], "rows": []}


@router.get("/templates")
async def get_canvas_templates() -> List[Dict[str, Any]]:
    """Returns starter pipeline canvas templates."""
    return [
        {
            "name": "Single Source with Filter & Aggregation",
            "description": "Loads car_rental.main.cars, applies status filter, computes average odometer, and limits output.",
            "nodes": [
                {
                    "id": "1", "type": "source", "title": "Cars Fleet", "x": 100, "y": 150,
                    "data": {"database": "car_rental", "schema": "main", "table": "cars", "alias": "c", "selected_columns": ["id", "vin", "status", "current_odometer"]}
                },
                {
                    "id": "2", "type": "filter", "title": "Active Filter", "x": 380, "y": 150,
                    "data": {"column": "c.status", "operator": "=", "value": "'ACTIVE'"}
                },
                {
                    "id": "3", "type": "transform", "title": "Metrics", "x": 660, "y": 150,
                    "data": {"expressions": [{"func": "COUNT", "column": "*", "alias": "total_active_cars"}, {"func": "AVG", "column": "c.current_odometer", "alias": "avg_mileage"}]}
                },
                {
                    "id": "4", "type": "output", "title": "Results Output", "x": 940, "y": 150,
                    "data": {"limit": 50, "order_column": "", "order_direction": "ASC"}
                }
            ],
            "connections": [
                {"from_node": "1", "to_node": "2"},
                {"from_node": "2", "to_node": "3"},
                {"from_node": "3", "to_node": "4"}
            ]
        },
        {
            "name": "Two-Table Relational Join Pipeline",
            "description": "Joins cars with customers and aggregates rental metrics.",
            "nodes": [
                {
                    "id": "1", "type": "source", "title": "Cars Source", "x": 100, "y": 100,
                    "data": {"database": "car_rental", "schema": "main", "table": "cars", "alias": "c", "selected_columns": ["id", "vin", "status"]}
                },
                {
                    "id": "2", "type": "source", "title": "Customers Source", "x": 100, "y": 280,
                    "data": {"database": "car_rental", "schema": "main", "table": "customers", "alias": "cust", "selected_columns": ["id", "first_name", "last_name", "email"]}
                },
                {
                    "id": "3", "type": "join", "title": "Inner Join", "x": 450, "y": 190,
                    "data": {"type": "LEFT JOIN", "database": "car_rental", "schema": "main", "table": "customers", "alias": "cust", "on_left": "c.id", "on_right": "cust.id"}
                },
                {
                    "id": "4", "type": "output", "title": "Joined Output", "x": 800, "y": 190,
                    "data": {"limit": 100, "order_column": "c.id", "order_direction": "ASC"}
                }
            ],
            "connections": [
                {"from_node": "1", "to_node": "3"},
                {"from_node": "2", "to_node": "3"},
                {"from_node": "3", "to_node": "4"}
            ]
        }
    ]
