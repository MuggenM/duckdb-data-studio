import os
import uuid
import json

class CanvasNode:
    def __init__(self, node_id: str, node_type: str, title: str, x: int = 100, y: int = 100, data: dict = None):
        self.id = node_id
        self.type = node_type  # 'source', 'join', 'transform', 'filter', 'output'
        self.title = title
        self.x = x
        self.y = y
        self.data = data if data is not None else {}

class CanvasGraph:
    def __init__(self):
        self.nodes = {}  # node_id -> CanvasNode
        self.connections = []  # list of dicts: {'from_node', 'from_port', 'to_node', 'to_port'}

    def add_node(self, node: CanvasNode):
        self.nodes[node.id] = node

    def remove_node(self, node_id: str):
        if node_id in self.nodes:
            del self.nodes[node_id]
        self.connections = [c for c in self.connections if c['from_node'] != node_id and c['to_node'] != node_id]

    def add_connection(self, from_node: str, to_node: str, from_port: str = 'out', to_port: str = 'in'):
        conn = {'from_node': from_node, 'from_port': from_port, 'to_node': to_node, 'to_port': to_port}
        if conn not in self.connections:
            self.connections.append(conn)

    def remove_connection(self, from_node: str, to_node: str):
        self.connections = [c for c in self.connections if not (c['from_node'] == from_node and c['to_node'] == to_node)]

    def to_dict(self):
        return {
            'nodes': {nid: {'id': n.id, 'type': n.type, 'title': n.title, 'x': n.x, 'y': n.y, 'data': n.data} for nid, n in self.nodes.items()},
            'connections': self.connections
        }

def compile_canvas_to_sql(graph_dict: dict, mode: str = 'standard') -> str:
    """
    Compiles a Canvas Node Graph into Standard DuckDB SQL or dbt Jinja SQL.
    """
    nodes = graph_dict.get('nodes', {})
    connections = graph_dict.get('connections', [])

    if not nodes:
        return "-- Canvas is empty. Add Source Nodes to begin building your query pipeline."

    # Identify source nodes
    source_nodes = [n for n in nodes.values() if n.get('type') == 'source']
    if not source_nodes:
        return "-- Please add at least one Source Node (Database & Table) to the canvas."

    primary_source = source_nodes[0]
    p_data = primary_source.get('data', {})
    p_db = p_data.get('database', 'sqlite_lakehouse')
    p_schema = p_data.get('schema', 'main')
    p_table = p_data.get('table', '')
    p_alias = p_data.get('alias', 't1')
    p_cols = p_data.get('selected_columns', [])

    if not p_table:
        return "-- Please select a database and table in the primary Source Node."

    # Find connected joins, transforms, filters, and output
    join_nodes = [n for n in nodes.values() if n.get('type') == 'join']
    transform_nodes = [n for n in nodes.values() if n.get('type') == 'transform']
    filter_nodes = [n for n in nodes.values() if n.get('type') == 'filter']
    output_nodes = [n for n in nodes.values() if n.get('type') == 'output']

    # 1. SELECT clause
    select_parts = []
    
    # Check if there are transform nodes with custom column expressions
    transform_exprs = []
    for tn in transform_nodes:
        t_data = tn.get('data', {})
        exprs = t_data.get('expressions', [])
        for ex in exprs:
            col = ex.get('column', '').strip()
            fn = ex.get('func', 'NONE').upper()
            alias = ex.get('alias', '').strip()
            if col:
                if fn and fn != 'NONE':
                    if fn == 'UPPER': expr_str = f"UPPER({col})"
                    elif fn == 'LOWER': expr_str = f"LOWER({col})"
                    elif fn == 'SUM': expr_str = f"SUM({col})"
                    elif fn == 'COUNT': expr_str = f"COUNT({col})"
                    elif fn == 'AVG': expr_str = f"AVG({col})"
                    elif fn == 'MIN': expr_str = f"MIN({col})"
                    elif fn == 'MAX': expr_str = f"MAX({col})"
                    elif fn == 'COUNT_DISTINCT': expr_str = f"COUNT(DISTINCT {col})"
                    else: expr_str = f"{fn}({col})"
                else:
                    expr_str = col

                if alias and alias != col:
                    transform_exprs.append(f"  {expr_str} AS \"{alias}\"")
                else:
                    transform_exprs.append(f"  {expr_str}")

    if transform_exprs:
        select_parts.extend(transform_exprs)
    elif p_cols:
        for c in p_cols:
            select_parts.append(f"  {p_alias}.{c}")
    else:
        select_parts.append(f"  {p_alias}.*")

    # Add columns from secondary sources if present in joins
    for idx, s_node in enumerate(source_nodes[1:]):
        s_data = s_node.get('data', {})
        s_alias = s_data.get('alias', f"t{idx+2}")
        s_cols = s_data.get('selected_columns', [])
        for sc in s_cols:
            select_parts.append(f"  {s_alias}.{sc}")

    select_str = "SELECT\n" + ",\n".join(select_parts)

    # 2. FROM clause
    if mode == 'dbt':
        if p_db in ('dbt_workspace', 'main', 'default', '') or not p_db:
            from_table = f"{{{{ ref('{p_table}') }}}}"
        else:
            from_table = f"{{{{ source('{p_db}', '{p_table}') }}}}"
    else:
        from_table = f"\"{p_db}\".\"{p_schema}\".\"{p_table}\""

    from_str = f"FROM {from_table} AS {p_alias}"

    # 3. JOIN clauses
    join_strs = []
    for idx, j_node in enumerate(join_nodes):
        j_data = j_node.get('data', {})
        j_type = j_data.get('type', 'LEFT JOIN').upper()
        j_db = j_data.get('database', 'car_rental')
        j_schema = j_data.get('schema', 'main')
        j_tbl = j_data.get('table', '')
        j_alias = j_data.get('alias', f"t{idx+2}")
        on_left = j_data.get('on_left', f"{p_alias}.id")
        on_right = j_data.get('on_right', f"{j_alias}.id")

        if not j_tbl:
            continue

        if mode == 'dbt':
            if j_db in ('dbt_workspace', 'main', 'default', '') or not j_db:
                j_table_ref = f"{{{{ ref('{j_tbl}') }}}}"
            else:
                j_table_ref = f"{{{{ source('{j_db}', '{j_tbl}') }}}}"
        else:
            j_table_ref = f"\"{j_db}\".\"{j_schema}\".\"{j_tbl}\""

        join_line = f"{j_type} {j_table_ref} AS {j_alias} ON {on_left} = {on_right}"
        join_strs.append(join_line)

    joins_str = "\n".join(join_strs) if join_strs else ""

    # 4. WHERE clause
    where_parts = []
    for fn in filter_nodes:
        f_data = fn.get('data', {})
        col = f_data.get('column', '').strip()
        op = f_data.get('operator', '=').strip().upper()
        val = str(f_data.get('value', '')).strip()

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

    # 5. ORDER BY & LIMIT (from Output Node or defaults)
    ord_str = ""
    limit_str = "LIMIT 100"
    if output_nodes:
        o_data = output_nodes[0].get('data', {})
        ord_col = o_data.get('order_column', '').strip()
        ord_dir = o_data.get('order_direction', 'ASC').upper()
        lim_val = o_data.get('limit', 100)
        if ord_col:
            ord_str = f"ORDER BY {ord_col} {ord_dir}"
        if lim_val:
            limit_str = f"LIMIT {int(lim_val)}"

    # Combine all parts
    sql_parts = [select_str, from_str]
    if joins_str: sql_parts.append(joins_str)
    if where_str: sql_parts.append(where_str)
    if ord_str: sql_parts.append(ord_str)
    if limit_str: sql_parts.append(limit_str)

    return "\n".join(sql_parts) + ";"
