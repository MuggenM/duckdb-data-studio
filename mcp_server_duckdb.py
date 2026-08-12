import os
import sys
import duckdb
from mcp.server.mcpserver import MCPServer

# Initialize DuckDB Data Studio Standalone Stdio MCP Server
mcp_server = MCPServer(
    "DuckDB Data Studio (Stdio)",
    instructions="Official Stdio MCP Server for DuckDB Data Studio workspace engine."
)

DB_PATH = os.environ.get("DUCKDB_PATH", "/home/martin/volumes/duckdb-studio/databases/main.duckdb")

def get_connection():
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
        db_dir = os.path.dirname(DB_PATH)
        if os.path.exists(db_dir):
            for fname in os.listdir(db_dir):
                if fname.endswith(".duckdb") and fname != os.path.basename(DB_PATH):
                    dbname = os.path.splitext(fname)[0]
                    dbpath = os.path.join(db_dir, fname)
                    try:
                        conn.execute(f"ATTACH DATABASE '{dbpath}' AS {dbname};")
                    except Exception:
                        pass
        return conn
    except Exception:
        return duckdb.connect(":memory:")

@mcp_server.tool()
def execute_sql(sql: str) -> str:
    """Execute a DuckDB SQL query against DuckDB Data Studio workspace databases.
    
    Args:
        sql: DuckDB SQL query string (e.g. 'SELECT * FROM main_db.main.sales_transactions LIMIT 10;')
    """
    sql_clean = sql.strip()
    if not sql_clean:
        return "Error: Empty SQL query provided."
    import time
    start_t = time.time()
    try:
        conn = get_connection()
        rel = conn.execute(sql_clean)
        elapsed_ms = round((time.time() - start_t) * 1000, 1)
        if rel.description:
            cols = [desc[0] for desc in rel.description]
            rows = rel.fetchmany(50)
            row_count = len(rows)
            headers = " | ".join(cols)
            separators = " | ".join(["---"] * len(cols))
            table_lines = [f"| {headers} |", f"| {separators} |"]
            for r in rows:
                vals = " | ".join(str(val) for val in r)
                table_lines.append(f"| {vals} |")
            table_md = "\n".join(table_lines)
            conn.close()
            return f"⚡ Query executed successfully in {elapsed_ms}ms ({row_count} rows returned):\n\n{table_md}"
        else:
            conn.close()
            return f"⚡ Query executed successfully in {elapsed_ms}ms (DML/DDL output)."
    except Exception as ex:
        return f"⚠️ DuckDB Execution Error: {ex}"

@mcp_server.tool()
def list_databases_and_tables() -> str:
    """List all attached databases, schemas, tables, and column data types in the DuckDB Data Studio workspace."""
    try:
        conn = get_connection()
        cols_rows = conn.execute("""
            SELECT database_name, schema_name, table_name, column_name, data_type 
            FROM duckdb_columns 
            WHERE database_name NOT IN ('temp', 'system')
            ORDER BY database_name, schema_name, table_name, column_index;
        """).fetchall()
        conn.close()
        
        if not cols_rows:
            return "No tables found in attached databases."
            
        dbs = {}
        for db, sch, tbl, col, dtype in cols_rows:
            db_key = f"{db}.{sch}"
            if db_key not in dbs:
                dbs[db_key] = {}
            if tbl not in dbs[db_key]:
                dbs[db_key][tbl] = []
            dbs[db_key][tbl].append(f"{col} ({dtype})")
            
        summary = ["DuckDB Workspace Catalog Breakdown:"]
        for db_key, tables in dbs.items():
            summary.append(f"\n📂 Database/Schema: `{db_key}`")
            for tbl, cols in tables.items():
                summary.append(f"  • Table `{tbl}`: {', '.join(cols)}")
        return "\n".join(summary)
    except Exception as ex:
        return f"Error listing workspace catalog: {ex}"

@mcp_server.tool()
def describe_table(table_name: str, database_name: str = "main_db", schema_name: str = "main") -> str:
    """Get column specifications, nullability, and total row count for a table.
    
    Args:
        table_name: Name of the table
        database_name: Database name (defaults to main_db)
        schema_name: Schema name (defaults to main)
    """
    try:
        conn = get_connection()
        fq_name = f"{database_name}.{schema_name}.{table_name}"
        count_row = conn.execute(f"SELECT COUNT(*) FROM {fq_name};").fetchone()
        row_cnt = count_row[0] if count_row else 0
        
        col_rows = conn.execute("""
            SELECT column_name, data_type, is_nullable 
            FROM duckdb_columns 
            WHERE database_name = ? AND schema_name = ? AND table_name = ?
            ORDER BY column_index;
        """, [database_name, schema_name, table_name]).fetchall()
        conn.close()
        
        if not col_rows:
            return f"Table `{fq_name}` not found in catalog."
            
        lines = [f"📊 Schema Specification for `{fq_name}` (Total Rows: {row_cnt}):\n"]
        lines.append("| Column Name | Data Type | Nullable |")
        lines.append("| --- | --- | --- |")
        for cname, dtype, nullable in col_rows:
            lines.append(f"| {cname} | {dtype} | {nullable} |")
        return "\n".join(lines)
    except Exception as ex:
        return f"Error describing table `{database_name}.{schema_name}.{table_name}`: {ex}"

@mcp_server.tool()
def export_query_results(sql: str, file_path: str, file_format: str = "parquet") -> str:
    """Export SQL query results to a Parquet, CSV, or JSON file.
    
    Args:
        sql: DuckDB SQL query string to export
        file_path: Destination file path (e.g. /home/martin/volumes/duckdb-studio/exports/sales.parquet)
        file_format: Export format: 'parquet', 'csv', or 'json' (default: parquet)
    """
    fmt = file_format.lower().strip()
    if fmt not in ("parquet", "csv", "json"):
        return f"Error: Unsupported format '{file_format}'. Supported formats: parquet, csv, json."
    import os
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    try:
        conn = get_connection()
        import time
        start_t = time.time()
        copy_stmt = f"COPY ({sql.strip().rstrip(';')}) TO '{file_path}' (FORMAT '{fmt.upper()}');"
        conn.execute(copy_stmt)
        elapsed_ms = round((time.time() - start_t) * 1000, 1)
        conn.close()
        size_bytes = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        size_mb = round(size_bytes / (1024 * 1024), 2)
        return f"⚡ Export Successful in {elapsed_ms}ms!\n  • Output File: `{file_path}`\n  • Format: `{fmt.upper()}`\n  • Size: {size_bytes} bytes ({size_mb} MB)"
    except Exception as ex:
        return f"⚠️ Export Error: {ex}"

@mcp_server.tool()
def attach_database(database_name: str, file_path: str, read_only: bool = True) -> str:
    """Attach a DuckDB or SQLite database file to the workspace.
    
    Args:
        database_name: Alias name for the database (e.g. my_analytics)
        file_path: Absolute path to database file
        read_only: Connect in read-only mode (default: True)
    """
    try:
        conn = get_connection()
        ro_flag = "TRUE" if read_only else "FALSE"
        conn.execute(f"ATTACH DATABASE '{file_path}' AS {database_name} (READ_ONLY {ro_flag});")
        tables = conn.execute(f"SELECT table_name FROM duckdb_tables WHERE database_name = '{database_name}';").fetchall()
        tbl_names = [t[0] for t in tables]
        conn.close()
        return f"⚡ Database `{database_name}` attached successfully from `{file_path}`!\n  • Attached Tables ({len(tbl_names)}): {', '.join(tbl_names) if tbl_names else 'None'}"
    except Exception as ex:
        return f"⚠️ Attach Error: {ex}"

@mcp_server.tool()
def explain_query(sql: str, analyze: bool = False) -> str:
    """Get the DuckDB physical execution plan for a SQL query.
    
    Args:
        sql: DuckDB SQL query string
        analyze: Run EXPLAIN ANALYZE for actual runtime profiling (default: False)
    """
    try:
        conn = get_connection()
        prefix = "EXPLAIN ANALYZE " if analyze else "EXPLAIN "
        rel = conn.execute(prefix + sql.strip().rstrip(';'))
        rows = rel.fetchall()
        conn.close()
        plan_lines = [r[1] if len(r) > 1 else str(r[0]) for r in rows]
        plan_text = "\n".join(plan_lines)
        return f"⚡ Query Execution Plan ({'EXPLAIN ANALYZE' if analyze else 'EXPLAIN'}):\n\n```\n{plan_text}\n```"
    except Exception as ex:
        return f"⚠️ Explain Error: {ex}"

@mcp_server.tool()
def get_system_info() -> str:
    """Get system version, attached database paths, and workspace configuration."""
    try:
        import duckdb
        conn = get_connection()
        dbs = conn.execute("SELECT database_name, path FROM duckdb_databases();").fetchall()
        conn.close()
        db_lines = [f"  • `{db}`: `{path}`" for db, path in dbs if db not in ('temp', 'system')]
        return (
            f"⚡ DuckDB Data Studio Workspace System Info:\n"
            f"  • DuckDB Engine Version: {duckdb.__version__}\n"
            f"  • Attached Databases ({len(db_lines)}):\n" + "\n".join(db_lines)
        )
    except Exception as ex:
        return f"Error fetching system info: {ex}"

if __name__ == "__main__":
    mcp_server.run(transport="stdio")
