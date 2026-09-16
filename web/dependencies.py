from web.services.duckdb_service import DuckDBService

def get_duckdb_service() -> DuckDBService:
    """Dependency provider returning singleton DuckDBService instance."""
    return DuckDBService.get_instance()
