import os
import asyncio
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from web.routers import catalog, query, copilot, scheduler, api_service, delta, canvas, dbt
from web.dependencies import get_duckdb_service
from web.services.scheduler_service import SchedulerService

logger = logging.getLogger("duckdb_studio")
logging.basicConfig(level=logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(
    title="DuckDB Data Studio",
    description="High-Performance Visual IDE, Catalog Explorer & Microservices Engine for DuckDB",
    version="2.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Files & Templates
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Include API Routers
app.include_router(catalog.router)
app.include_router(query.router)
app.include_router(copilot.router)
app.include_router(scheduler.router)
app.include_router(api_service.router)
app.include_router(delta.router)
app.include_router(canvas.router)
app.include_router(dbt.router)


@app.on_event("startup")
async def on_startup():
    """Initializes DuckDB service and starts background automation loops on boot."""
    try:
        service = get_duckdb_service()
        tree = service.get_catalog_tree()
        db_count = len(tree.get("databases", []))
        logger.info(f"DuckDB Studio 2.0 initialized. Attached databases: {db_count}")

        # Start Background ETL Scheduler loop
        scheduler_srv = SchedulerService.get_instance()
        asyncio.create_task(scheduler_srv.scheduler_loop(service))
    except Exception as e:
        logger.error(f"Error during DuckDB Studio startup: {e}")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Renders the single-page Alpine.js + Tailwind + Monaco studio dashboard."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health():
    """Healthcheck endpoint reporting status and database attachments."""
    service = get_duckdb_service()
    tree = service.get_catalog_tree()
    return {
        "status": "online",
        "version": "2.0.0",
        "databases": [d["name"] for d in tree.get("databases", [])]
    }
