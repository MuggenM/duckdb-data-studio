import os
import re

def generate_visual_query(config: dict) -> str:
    """
    Generate standard DuckDB SQL or dbt Jinja Model SQL from a structured Visual Builder configuration.
    
    config keys:
      - mode: 'standard' or 'dbt'
      - primary: {'database': str, 'schema': str, 'table': str, 'alias': str}
      - joins: list of {'type': str, 'database': str, 'schema': str, 'table': str, 'alias': str, 'on_left': str, 'on_right': str}
      - columns: list of {'expr': str, 'alias': str}
      - filters: list of {'column': str, 'operator': str, 'value': str}
      - group_by: list of str
      - order_by: list of {'column': str, 'direction': str}
      - limit: int or None
    """
    mode = config.get('mode', 'standard').lower()
    primary = config.get('primary', {})
    joins = config.get('joins', [])
    columns = config.get('columns', [])
    filters = config.get('filters', [])
    group_by = config.get('group_by', [])
    order_by = config.get('order_by', [])
    limit = config.get('limit', 100)

    if not primary or not primary.get('table'):
        return "-- Please select a primary database and table to build your query."

    # 1. SELECT clause
    select_parts = []
    if columns:
        for col in columns:
            expr = col.get('expr', '').strip()
            alias = col.get('alias', '').strip()
            if not expr:
                continue
            if alias and alias != expr:
                select_parts.append(f"  {expr} AS \"{alias}\"")
            else:
                select_parts.append(f"  {expr}")
    else:
        p_alias = primary.get('alias', '').strip()
        tbl_ref = f"{p_alias}.*" if p_alias else "*"
        select_parts.append(f"  {tbl_ref}")

    select_str = "SELECT\n" + ",\n".join(select_parts)

    # 2. FROM clause
    db_name = primary.get('database', 'main')
    schema_name = primary.get('schema', 'main')
    table_name = primary.get('table', '')
    p_alias = primary.get('alias', 't1')

    if mode == 'dbt':
        if db_name in ('dbt_workspace', 'main', 'default', '') or not db_name:
            from_table = f"{{{{ ref('{table_name}') }}}}"
        else:
            from_table = f"{{{{ source('{db_name}', '{table_name}') }}}}"
    else:
        from_table = f"\"{db_name}\".\"{schema_name}\".\"{table_name}\""

    if p_alias:
        from_str = f"FROM {from_table} AS {p_alias}"
    else:
        from_str = f"FROM {from_table}"

    # 3. JOIN clauses
    join_strs = []
    for idx, j in enumerate(joins):
        j_type = j.get('type', 'INNER JOIN').upper()
        j_db = j.get('database', db_name)
        j_schema = j.get('schema', 'main')
        j_table = j.get('table', '')
        j_alias = j.get('alias', f"t{idx+2}")
        on_left = j.get('on_left', '')
        on_right = j.get('on_right', '')

        if not j_table:
            continue

        if mode == 'dbt':
            if j_db in ('dbt_workspace', 'main', 'default', '') or not j_db:
                j_table_ref = f"{{{{ ref('{j_table}') }}}}"
            else:
                j_table_ref = f"{{{{ source('{j_db}', '{j_table}') }}}}"
        else:
            j_table_ref = f"\"{j_db}\".\"{j_schema}\".\"{j_table}\""

        join_line = f"{j_type} {j_table_ref} AS {j_alias}"
        if on_left and on_right:
            join_line += f" ON {on_left} = {on_right}"
        join_strs.append(join_line)

    joins_str = "\n".join(join_strs) if join_strs else ""

    # 4. WHERE clause
    where_parts = []
    for f in filters:
        col = f.get('column', '').strip()
        op = f.get('operator', '=').strip().upper()
        val = str(f.get('value', '')).strip()

        if not col:
            continue

        if op in ('IS NULL', 'IS NOT NULL'):
            where_parts.append(f"{col} {op}")
        elif val:
            if op in ('LIKE', 'ILIKE', 'NOT LIKE'):
                escaped_val = val.replace("'", "''")
                where_parts.append(f"{col} {op} '%{escaped_val}%'")
            elif op == 'IN':
                in_items = [v.strip().replace("'", "''") for v in val.split(',')]
                in_str = ", ".join([f"'{v}'" if not v.isdigit() else v for v in in_items])
                where_parts.append(f"{col} IN ({in_str})")
            else:
                try:
                    float(val)
                    where_parts.append(f"{col} {op} {val}")
                except ValueError:
                    escaped_val = val.replace("'", "''")
                    where_parts.append(f"{col} {op} '{escaped_val}'")

    where_str = "WHERE\n  " + "\n  AND ".join(where_parts) if where_parts else ""

    # 5. GROUP BY clause
    gb_str = ""
    if group_by:
        gb_str = "GROUP BY " + ", ".join(group_by)

    # 6. ORDER BY clause
    ord_parts = []
    for o in order_by:
        o_col = o.get('column', '').strip()
        o_dir = o.get('direction', 'ASC').upper()
        if o_col:
            ord_parts.append(f"{o_col} {o_dir}")
    ord_str = "ORDER BY " + ", ".join(ord_parts) if ord_parts else ""

    # 7. LIMIT clause
    limit_str = f"LIMIT {int(limit)}" if limit and int(limit) > 0 else ""

    # Combine all parts
    sql_parts = [select_str, from_str]
    if joins_str:
        sql_parts.append(joins_str)
    if where_str:
        sql_parts.append(where_str)
    if gb_str:
        sql_parts.append(gb_str)
    if ord_str:
        sql_parts.append(ord_str)
    if limit_str:
        sql_parts.append(limit_str)

    return "\n".join(sql_parts) + ";"
