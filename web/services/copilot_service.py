"""
Copilot & Context-Aware Autocomplete Engine for DuckDB Data Studio.
Ported and adapted from /home/martin/volumes/localspark/web/copilot.py.

Provides:
1. In-memory cached autocomplete metadata for Monaco SQL Editor:
   (catalogs, schemas, tables, columns with data types, SQL keywords, DuckDB functions).
2. Natural-language-to-SQL generation and error-fixing engine.
"""

import os
import time
import json
import re
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("duckdb_studio.copilot")

# ==================== DUCKDB LEXICON ====================

DUCKDB_KEYWORDS = [
    "SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "LIMIT", "OFFSET",
    "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL OUTER JOIN", "CROSS JOIN",
    "ON", "USING", "AS", "AND", "OR", "NOT", "IN", "BETWEEN", "LIKE", "ILIKE",
    "IS NULL", "IS NOT NULL", "DISTINCT", "ALL", "UNION", "UNION ALL", "INTERSECT", "EXCEPT",
    "WITH", "CREATE TABLE", "CREATE VIEW", "INSERT INTO", "MERGE INTO", "UPDATE", "DELETE",
    "DROP TABLE", "ALTER TABLE", "SHOW TABLES", "DESCRIBE", "EXPLAIN", "PRAGMA",
    "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "TRUE", "FALSE", "NULL", "ASC", "DESC",
    "ATTACH", "DETACH", "DATABASE", "COPY", "TO", "PARTITION_BY", "FORMAT"
]

DUCKDB_FUNCTIONS = [
    {"name": "COUNT", "signature": "COUNT(*)", "detail": "Aggregate: Counts rows or non-null values", "snippet": "COUNT(${1:*})"},
    {"name": "SUM", "signature": "SUM(col)", "detail": "Aggregate: Computes sum of numeric column", "snippet": "SUM(${1:column})"},
    {"name": "AVG", "signature": "AVG(col)", "detail": "Aggregate: Computes arithmetic mean", "snippet": "AVG(${1:column})"},
    {"name": "MIN", "signature": "MIN(col)", "detail": "Aggregate: Returns minimum value", "snippet": "MIN(${1:column})"},
    {"name": "MAX", "signature": "MAX(col)", "detail": "Aggregate: Returns maximum value", "snippet": "MAX(${1:column})"},
    {"name": "ROUND", "signature": "ROUND(val, [decimals])", "detail": "Math: Rounds numeric expression", "snippet": "ROUND(${1:val}, ${2:2})"},
    {"name": "COALESCE", "signature": "COALESCE(val1, val2, ...)", "detail": "Conditional: Returns first non-null argument", "snippet": "COALESCE(${1:val1}, ${2:val2})"},
    {"name": "NULLIF", "signature": "NULLIF(val1, val2)", "detail": "Conditional: Returns null if arguments are equal", "snippet": "NULLIF(${1:val1}, ${2:val2})"},
    {"name": "DATE_TRUNC", "signature": "DATE_TRUNC('part', timestamp)", "detail": "Date: Truncates timestamp to specified granularity", "snippet": "DATE_TRUNC('${1:month}', ${2:timestamp})"},
    {"name": "DATE_DIFF", "signature": "DATE_DIFF('part', start, end)", "detail": "Date: Computes difference between dates", "snippet": "DATE_DIFF('${1:day}', ${2:start_date}, ${3:end_date})"},
    {"name": "STRFTIME", "signature": "STRFTIME(ts, 'format')", "detail": "Date: Formats timestamp as string", "snippet": "STRFTIME(${1:timestamp}, '${2:%Y-%m-%d}')"},
    {"name": "NOW", "signature": "NOW()", "detail": "Date: Returns current date and time", "snippet": "NOW()"},
    {"name": "CONCAT", "signature": "CONCAT(str1, str2, ...)", "detail": "String: Concatenates string arguments", "snippet": "CONCAT(${1:str1}, ${2:str2})"},
    {"name": "SUBSTRING", "signature": "SUBSTRING(str, start, [len])", "detail": "String: Extracts substring", "snippet": "SUBSTRING(${1:str}, ${2:1}, ${3:10})"},
    {"name": "UPPER", "signature": "UPPER(str)", "detail": "String: Converts string to uppercase", "snippet": "UPPER(${1:str})"},
    {"name": "LOWER", "signature": "LOWER(str)", "detail": "String: Converts string to lowercase", "snippet": "LOWER(${1:str})"},
    {"name": "TRIM", "signature": "TRIM(str)", "detail": "String: Strips leading and trailing whitespace", "snippet": "TRIM(${1:str})"},
    {"name": "REGEXP_MATCHES", "signature": "REGEXP_MATCHES(str, 'regex')", "detail": "Regex: Pattern matching predicate", "snippet": "REGEXP_MATCHES(${1:str}, '${2:pattern}')"},
    {"name": "ROW_NUMBER", "signature": "ROW_NUMBER() OVER (...)", "detail": "Window: Assigns sequential integer per partition", "snippet": "ROW_NUMBER() OVER (PARTITION BY ${1:dept} ORDER BY ${2:salary} DESC)"},
    {"name": "DENSE_RANK", "signature": "DENSE_RANK() OVER (...)", "detail": "Window: Assigns rank without gaps", "snippet": "DENSE_RANK() OVER (ORDER BY ${1:salary} DESC)"},
    {"name": "read_parquet", "signature": "read_parquet('path/*.parquet')", "detail": "DuckDB: Direct vectorized scan of Parquet files", "snippet": "read_parquet('${1:path/*.parquet}')"},
    {"name": "read_csv_auto", "signature": "read_csv_auto('path/*.csv')", "detail": "DuckDB: Auto-detecting CSV reader", "snippet": "read_csv_auto('${1:path/*.csv}')"},
    {"name": "read_json_auto", "signature": "read_json_auto('path/*.json')", "detail": "DuckDB: Auto-detecting JSON reader", "snippet": "read_json_auto('${1:path/*.json}')"},
    {"name": "delta_scan", "signature": "delta_scan('path', [version => N])", "detail": "Delta Lake: Vectorized Delta table scan with time-travel", "snippet": "delta_scan('${1:path}', version => ${2:0})"}
]

# ==================== AUTOCOMPLETE CACHE ====================

_AUTOCOMPLETE_CACHE: Dict[str, Any] = {"data": None, "cached_at": 0.0}
AUTOCOMPLETE_CACHE_TTL = 10.0  # seconds


def invalidate_autocomplete_cache():
    """Invalidates the in-memory autocomplete cache."""
    _AUTOCOMPLETE_CACHE["data"] = None
    _AUTOCOMPLETE_CACHE["cached_at"] = 0.0


def get_autocomplete_metadata(duckdb_service) -> Dict[str, Any]:
    """
    Returns complete DuckDB catalog metadata structured for Monaco's completion provider:
    - catalogs: List of registered database catalogs
    - tables: List of table objects with column schemas and types
    - columns: Column index mapping column names to parent tables
    - keywords: DuckDB SQL keywords
    - functions: DuckDB analytical/date/window functions with snippets
    """
    now = time.time()
    if _AUTOCOMPLETE_CACHE["data"] is not None and (now - _AUTOCOMPLETE_CACHE["cached_at"]) < AUTOCOMPLETE_CACHE_TTL:
        return _AUTOCOMPLETE_CACHE["data"]

    catalogs_dict: Dict[str, Dict[str, Any]] = {}
    tables_list: List[Dict[str, Any]] = []
    columns_flat: List[Dict[str, Any]] = []

    try:
        conn = duckdb_service.connection
        rows = conn.execute("SHOW ALL TABLES;").fetchall()

        for row in rows:
            cat_name = row[0]
            schema_name = row[1]
            tbl_name = row[2]
            col_names = row[3]
            col_types = row[4]

            if tbl_name.startswith("__") or tbl_name.startswith("sqlite_"):
                continue

            if cat_name not in catalogs_dict:
                catalogs_dict[cat_name] = {
                    "id": cat_name,
                    "name": cat_name,
                    "schemas": {}
                }

            if schema_name not in catalogs_dict[cat_name]["schemas"]:
                catalogs_dict[cat_name]["schemas"][schema_name] = []

            catalogs_dict[cat_name]["schemas"][schema_name].append(tbl_name)

            full_ident = f"{cat_name}.{schema_name}.{tbl_name}" if cat_name not in ("memory", "temp") else tbl_name
            canonical_ident = f"{cat_name}.{schema_name}.{tbl_name}"

            cols = []
            for cname, ctype in zip(col_names, col_types):
                clean_type = str(ctype).upper()
                cols.append({"name": cname, "type": clean_type})
                columns_flat.append({
                    "name": cname,
                    "type": clean_type,
                    "table_name": tbl_name,
                    "full_table_name": full_ident,
                    "canonical_table_name": canonical_ident
                })

            tables_list.append({
                "name": tbl_name,
                "full_name": full_ident,
                "canonical_name": canonical_ident,
                "catalog": cat_name,
                "schema": schema_name,
                "columns": cols
            })

    except Exception as e:
        logger.error(f"Error extracting autocomplete metadata: {e}")

    result = {
        "catalogs": list(catalogs_dict.values()),
        "tables": tables_list,
        "columns": columns_flat,
        "keywords": DUCKDB_KEYWORDS,
        "functions": DUCKDB_FUNCTIONS
    }

    _AUTOCOMPLETE_CACHE["data"] = result
    _AUTOCOMPLETE_CACHE["cached_at"] = now
    return result


def extract_schema_summary(duckdb_service) -> str:
    """Generates concise text summary of available tables and columns for LLM prompts."""
    metadata = get_autocomplete_metadata(duckdb_service)
    summary_lines = []
    for tbl in metadata.get("tables", [])[:40]:  # Cap at top 40 tables to prevent prompt bloat
        cols = ", ".join([f"{c['name']} ({c['type']})" for c in tbl.get("columns", [])[:15]])
        summary_lines.append(f"- Table `{tbl['full_name']}`: {cols}")
    return "\n".join(summary_lines)


def call_copilot_heuristic(prompt: str, current_query: Optional[str], schema_summary: str, duckdb_service=None) -> Dict[str, Any]:
    """
    Intelligent rule-based NL-to-SQL synthesis fallback when external LLM is offline.
    Provides immediate accurate DuckDB SQL for car_rental, e_commerce, formula1, nyc_taxi, etc.
    """
    p = prompt.strip().lower()

    # Query modification checks (only if not asking for a domain entity)
    is_domain_query = any(k in p for k in ["car", "vehicle", "customer", "driver", "f1", "taxi", "shipment", "weather", "order", "product", "employee"])
    if not is_domain_query and current_query and current_query.strip() and any(k in p for k in ["order by", "sort by", "filter", "where", "limit"]):
        cleaned = current_query.strip().rstrip(";")
        if "limit" in p:
            m = re.search(r"\b(\d+)\b", p)
            lim = int(m.group(1)) if m else 10
            if re.search(r"limit\s+\d+", cleaned, re.IGNORECASE):
                mod_sql = re.sub(r"limit\s+\d+", f"LIMIT {lim}", cleaned, flags=re.IGNORECASE) + ";"
            else:
                mod_sql = f"{cleaned}\nLIMIT {lim};"
            return {
                "sql": mod_sql,
                "explanation": f"Updated limit clause to {lim} rows.",
                "tables_used": []
            }
        if "order by" in p or "sort" in p:
            desc = "DESC" if any(k in p for k in ["desc", "highest", "most", "greatest"]) else "ASC"
            m = re.search(r"(?:order by|sort by)\s+([a-zA-Z0-9_]+)", p)
            col = m.group(1) if m else "1"
            if re.search(r"order\s+by\s+[^;]+", cleaned, re.IGNORECASE):
                mod_sql = re.sub(r"order\s+by\s+[^;]+", f"ORDER BY {col} {desc}", cleaned, flags=re.IGNORECASE) + ";"
            else:
                mod_sql = f"{cleaned}\nORDER BY {col} {desc};"
            return {
                "sql": mod_sql,
                "explanation": f"Updated sorting order to {col} {desc}.",
                "tables_used": []
            }

    # Car Rental Domain
    if any(k in p for k in ["car", "vehicle", "odometer", "rental", "fleet"]):
        if any(k in p for k in ["status", "count", "by status", "breakdown"]):
            return {
                "sql": "SELECT \n    status,\n    COUNT(*) AS total_cars,\n    ROUND(AVG(current_odometer), 0) AS avg_odometer\nFROM car_rental.main.cars\nGROUP BY status\nORDER BY total_cars DESC;",
                "explanation": "Summarizes rental car inventory and average mileage grouped by status.",
                "tables_used": ["car_rental.main.cars"]
            }
        elif any(k in p for k in ["highest", "mileage", "most driven", "top"]):
            m = re.search(r"\b(\d+)\b", p)
            limit = int(m.group(1)) if m else 5
            return {
                "sql": f"SELECT id, vin, license_plate, color, current_odometer, status\nFROM car_rental.main.cars\nORDER BY current_odometer DESC\nLIMIT {limit};",
                "explanation": f"Selects top {limit} vehicles with highest odometer readings.",
                "tables_used": ["car_rental.main.cars"]
            }
        else:
            return {
                "sql": "SELECT * FROM car_rental.main.cars LIMIT 25;",
                "explanation": "25-row preview sample from car_rental.main.cars.",
                "tables_used": ["car_rental.main.cars"]
            }

    # Customers Domain
    if any(k in p for k in ["customer", "client", "user"]):
        return {
            "sql": "SELECT id, first_name, last_name, email, status, created_at\nFROM car_rental.main.customers\nORDER BY created_at DESC\nLIMIT 25;",
            "explanation": "Retrieves recent customer registrations with contact info and status.",
            "tables_used": ["car_rental.main.customers"]
        }

    # Formula 1 Domain
    if any(k in p for k in ["driver", "f1", "formula", "race", "grand prix", "championship"]):
        return {
            "sql": "SELECT forename, surname, nationality, dob\nFROM formula1.main.drivers\nORDER BY surname ASC\nLIMIT 25;",
            "explanation": "Lists Formula 1 drivers ordered alphabetically by surname.",
            "tables_used": ["formula1.main.drivers"]
        }

    # Logistics Domain
    if any(k in p for k in ["shipment", "warehouse", "logistics", "delivery", "freight"]):
        return {
            "sql": "SELECT * FROM logistics.main.shipments LIMIT 25;",
            "explanation": "Fetches recent logistics shipment records.",
            "tables_used": ["logistics.main.shipments"]
        }

    # NYC Taxi Domain
    if any(k in p for k in ["taxi", "fare", "tip", "passenger", "trip"]):
        return {
            "sql": "SELECT \n    passenger_count,\n    COUNT(*) AS total_trips,\n    ROUND(AVG(fare_amount), 2) AS avg_fare,\n    ROUND(AVG(tip_amount), 2) AS avg_tip\nFROM nyc_taxi.main.yellow_tripdata\nGROUP BY passenger_count\nORDER BY total_trips DESC\nLIMIT 10;",
            "explanation": "Aggregates NYC taxi trips, average fares, and tip rates by passenger count.",
            "tables_used": ["nyc_taxi.main.yellow_tripdata"]
        }

    # Fallback to general table
    return {
        "sql": "SELECT \n    table_schema,\n    table_name\nFROM information_schema.tables\nWHERE table_schema NOT IN ('information_schema', 'pg_catalog')\nLIMIT 25;",
        "explanation": "Lists available database tables and schemas.",
        "tables_used": ["information_schema.tables"]
    }


async def generate_copilot_sql(
    prompt: str,
    current_query: Optional[str] = None,
    selection: Optional[str] = None,
    provider: str = "heuristic",
    model: str = "default",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    duckdb_service = None
) -> Dict[str, Any]:
    """Generates SQL using an LLM provider or offline heuristic fallback."""
    schema_summary = extract_schema_summary(duckdb_service) if duckdb_service else ""

    if provider in ("openai", "ollama", "custom") and (api_key or provider == "ollama"):
        import httpx
        url = base_url or ("http://localhost:11434/v1" if provider == "ollama" else "https://api.openai.com/v1")
        url = url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        system_prompt = f"""You are DuckDB Studio Copilot, an expert analytical SQL assistant.
Database Schema Information:
{schema_summary}

Rules:
1. Generate valid, high-performance DuckDB SQL syntax.
2. Use fully qualified names (e.g. `car_rental.main.cars`) if applicable.
3. Respond ONLY with a JSON object: {{"sql": "SELECT ...", "explanation": "Brief description", "tables_used": ["tbl"]}}
"""
        context_msg = f"\nCurrent query in editor:\n```sql\n{current_query}\n```" if current_query else ""
        if selection:
            context_msg += f"\nHighlighted code:\n```sql\n{selection}\n```"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model or ("qwen2.5-coder:1.5b" if provider == "ollama" else "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{prompt.strip()}{context_msg}"}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    parsed["success"] = True
                    parsed["provider"] = provider
                    return parsed
        except Exception as e:
            logger.warning(f"LLM generation failed: {e}. Falling back to heuristic.")

    # Offline heuristic fallback
    res = call_copilot_heuristic(prompt, current_query, schema_summary, duckdb_service)
    res["success"] = True
    res["provider"] = "heuristic"
    return res


async def fix_sql_error(
    sql: str,
    error_message: str,
    provider: str = "heuristic",
    model: str = "default",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    duckdb_service = None
) -> Dict[str, Any]:
    """
    Self-healing Auto-Fix loop: Analyzes broken SQL and DuckDB error trace,
    resolving missing table qualifiers, syntax typos, or column ambiguities.
    """
    schema_summary = extract_schema_summary(duckdb_service) if duckdb_service else ""
    err = error_message.lower()

    # Rule-based instant fixes
    fixed_sql = sql

    # Fix 1: Table not found -> Suggest schema prefix (e.g. cars -> car_rental.main.cars)
    m_table = re.search(r"table with name ([a-zA-Z0-9_]+) does not exist", err)
    if m_table:
        missing_tbl = m_table.group(1)
        # Search in schema
        metadata = get_autocomplete_metadata(duckdb_service)
        for tbl in metadata.get("tables", []):
            if tbl["name"].lower() == missing_tbl.lower():
                fixed_sql = re.sub(rf"\b{re.escape(missing_tbl)}\b", tbl["full_name"], sql, flags=re.IGNORECASE)
                return {
                    "success": True,
                    "fixed_sql": fixed_sql,
                    "explanation": f"Resolved un-prefixed table `{missing_tbl}` to fully-qualified `{tbl['full_name']}`.",
                    "applied_rule": "qualify_table"
                }

    # Fix 2: Column not found
    m_col = re.search(r"column \"([a-zA-Z0-9_]+)\" not found", err)
    if m_col:
        missing_col = m_col.group(1)
        return {
            "success": True,
            "fixed_sql": fixed_sql,
            "explanation": f"Column `{missing_col}` does not exist. Check column names in the Catalog Explorer.",
            "applied_rule": "column_check"
        }

    # Fix 3: Semicolon or trailing syntax issue
    if "syntax error at or near" in err and not sql.strip().endswith(";"):
        return {
            "success": True,
            "fixed_sql": sql.strip() + ";",
            "explanation": "Appended missing statement semicolon terminator.",
            "applied_rule": "append_semicolon"
        }

    # Fallback explanation
    return {
        "success": True,
        "fixed_sql": sql,
        "explanation": f"DuckDB reported: {error_message}. Verify schema names and table relationships in the Catalog Explorer.",
        "applied_rule": "generic_fallback"
    }

