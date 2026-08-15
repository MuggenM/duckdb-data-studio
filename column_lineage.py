import os
import json
import sqlglot
import sqlglot.expressions as exp

def generate_column_lineage(target_dir=None):
    """
    Parse compiled dbt models and manifest.json using sqlglot AST parser 
    to extract column-level lineage (CLL) and write target/column_lineage.json.
    """
    if target_dir is None:
        possible_dirs = [
            "/app/dbt_project/target",
            "/home/coder/project/target",
            "dbt_project/target",
            "target"
        ]
        for d in possible_dirs:
            if os.path.exists(os.path.join(d, "manifest.json")):
                target_dir = d
                break
                
    if not target_dir or not os.path.exists(os.path.join(target_dir, "manifest.json")):
        print("WARNING: [column_lineage] manifest.json not found in target directories.", flush=True)
        return False

    manifest_path = os.path.join(target_dir, "manifest.json")
    try:
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
    except Exception as e:
        print(f"ERROR: [column_lineage] Failed to load manifest.json: {e}", flush=True)
        return False

    nodes = manifest.get("nodes", {})
    parent_map = manifest.get("parent_map", {})
    
    lineage_data = {
        "models": {}
    }

    for node_id, node in nodes.items():
        if not node_id.startswith("model."):
            continue
            
        model_name = node.get("name")
        schema_name = node.get("schema", "main")
        compiled_sql = node.get("compiled_code") or node.get("raw_code") or ""
        
        # Check compiled SQL file on disk if missing in manifest
        compiled_path = node.get("compiled_path")
        if compiled_path:
            full_compiled_path = os.path.join(os.path.dirname(target_dir), compiled_path)
            if os.path.exists(full_compiled_path):
                try:
                    with open(full_compiled_path, 'r') as cf:
                        compiled_sql = cf.read()
                except Exception:
                    pass

        column_dict = {}
        defined_columns = node.get("columns", {})
        
        if compiled_sql:
            try:
                # Parse SQL with DuckDB dialect
                parsed = sqlglot.parse_one(compiled_sql, read="duckdb")
                
                # Extract SELECT expressions
                if isinstance(parsed, exp.Select):
                    select_expressions = parsed.expressions
                    for expr in select_expressions:
                        alias = expr.alias_or_name
                        cols = [c.sql() for c in expr.find_all(exp.Column)]
                        column_dict[alias] = {
                            "name": alias,
                            "sources": sorted(list(set(cols))),
                            "expression": expr.sql(dialect="duckdb")
                        }
            except Exception as pe:
                # Fallback to manifest columns if AST parsing fails
                for col_name in defined_columns.keys():
                    column_dict[col_name] = {
                        "name": col_name,
                        "sources": [],
                        "expression": col_name
                    }
        else:
            for col_name in defined_columns.keys():
                column_dict[col_name] = {
                    "name": col_name,
                    "sources": [],
                    "expression": col_name
                }

        parents = parent_map.get(node_id, [])
        parent_names = []
        for p in parents:
            if p in nodes:
                parent_names.append(nodes[p].get("name"))

        lineage_data["models"][model_name] = {
            "name": model_name,
            "schema": schema_name,
            "parents": parent_names,
            "columns": column_dict
        }

    output_path = os.path.join(target_dir, "column_lineage.json")
    try:
        with open(output_path, 'w') as out_f:
            json.dump(lineage_data, out_f, indent=2)
        print(f"INFO: [column_lineage] Successfully generated column-level lineage at {output_path}", flush=True)
        return True
    except Exception as ex:
        print(f"ERROR: [column_lineage] Failed to write column_lineage.json: {ex}", flush=True)
        return False

if __name__ == "__main__":
    generate_column_lineage()
