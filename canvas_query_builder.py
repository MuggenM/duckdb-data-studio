import os
import uuid
import json

class CanvasNode:
    def __init__(self, node_id: str, node_type: str, title: str, x: int = 100, y: int = 100, data: dict = None):
        self.id = str(node_id)
        self.type = node_type  # 'source', 'join', 'transform', 'filter', 'output'
        self.title = title
        self.x = x
        self.y = y
        self.data = data if data is not None else {}

    def get_icon(self) -> str:
        icons = {
            'source': 'storage',
            'join': 'hub',
            'transform': 'auto_awesome',
            'filter': 'filter_alt',
            'output': 'flag'
        }
        return icons.get(self.type, 'apps')

    def get_color_theme(self) -> dict:
        themes = {
            'source': {'border': 'border-indigo-500', 'badge': 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-200', 'icon_color': 'indigo'},
            'join': {'border': 'border-purple-500', 'badge': 'bg-purple-100 text-purple-800 dark:bg-purple-900/60 dark:text-purple-200', 'icon_color': 'purple'},
            'transform': {'border': 'border-emerald-500', 'badge': 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-200', 'icon_color': 'emerald'},
            'filter': {'border': 'border-amber-500', 'badge': 'bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200', 'icon_color': 'amber'},
            'output': {'border': 'border-rose-500', 'badge': 'bg-rose-100 text-rose-800 dark:bg-rose-900/60 dark:text-rose-200', 'icon_color': 'rose'}
        }
        return themes.get(self.type, {'border': 'border-slate-400', 'badge': 'bg-slate-100 text-slate-800', 'icon_color': 'primary'})

    def get_summary(self) -> str:
        d = self.data
        if self.type == 'source':
            db = d.get('database', 'main')
            tbl = d.get('table', 'unselected')
            alias = d.get('alias', 't1')
            cols = d.get('selected_columns', [])
            col_str = f" ({len(cols)} cols)" if cols else ""
            return f"Source: {db}.{tbl} AS {alias}{col_str}"
        elif self.type == 'join':
            jtype = d.get('type', 'LEFT JOIN')
            db = d.get('database', 'main')
            tbl = d.get('table', 'unselected')
            left = d.get('on_left', 't1.id')
            right = d.get('on_right', 't2.id')
            return f"{jtype} {db}.{tbl} ON {left}={right}"
        elif self.type == 'transform':
            exprs = d.get('expressions', [])
            if not exprs: return "No transformations configured"
            ex_summary = []
            for ex in exprs[:2]:
                fn = ex.get('func', 'NONE')
                col = ex.get('column', 'col')
                alias = ex.get('alias', '')
                ex_str = f"{fn}({col})" if fn != 'NONE' else col
                if alias: ex_str += f" AS {alias}"
                ex_summary.append(ex_str)
            res = ", ".join(ex_summary)
            if len(exprs) > 2: res += f" (+{len(exprs)-2} more)"
            return res
        elif self.type == 'filter':
            col = d.get('column', 'column')
            op = d.get('operator', '=')
            val = d.get('value', '')
            return f"WHERE {col} {op} {val}".strip()
        elif self.type == 'output':
            ord_col = d.get('order_column', '')
            ord_dir = d.get('order_direction', 'ASC')
            lim = d.get('limit', 100)
            res = f"LIMIT {lim}"
            if ord_col: res = f"ORDER BY {ord_col} {ord_dir} | {res}"
            return res
        return "Configure Node Properties"

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

def compile_drawflow_json_to_sql(drawflow_export: dict, mode: str = 'standard') -> str:
    """
    Parses Drawflow Client-Side JS exported JSON payload and compiles it into SQL.
    """
    if not drawflow_export or not isinstance(drawflow_export, dict):
        return "-- Drawflow Canvas is empty. Drag or add nodes to begin."

    df_modules = drawflow_export.get('drawflow', {})
    home_module = df_modules.get('Home', df_modules.get('home', {}))
    raw_nodes = home_module.get('data', {})

    if not raw_nodes:
        return "-- Canvas is empty. Drag or add Source Nodes to begin building your query pipeline."

    graph_nodes = {}
    connections = []

    for nid_str, raw in raw_nodes.items():
        node_name = raw.get('name', 'source').lower()
        node_data = raw.get('data', {})
        node_type = node_data.get('node_type', node_name)
        
        # Output connections
        outputs = raw.get('outputs', {})
        for out_key, out_val in outputs.items():
            for conn in out_val.get('connections', []):
                connections.append({
                    'from_node': str(nid_str),
                    'from_port': out_key,
                    'to_node': str(conn.get('node')),
                    'to_port': conn.get('output', 'input_1')
                })

        graph_nodes[str(nid_str)] = {
            'id': str(nid_str),
            'type': node_type,
            'title': raw.get('class', node_type).title(),
            'x': raw.get('pos_x', 0),
            'y': raw.get('pos_y', 0),
            'data': node_data
        }

    graph_dict = {
        'nodes': graph_nodes,
        'connections': connections
    }

    return compile_canvas_to_sql(graph_dict, mode=mode)

def compile_canvas_to_sql(graph_dict: dict, mode: str = 'standard') -> str:
    """
    Compiles a Canvas Node Graph into Standard DuckDB SQL or dbt Jinja SQL.
    """
    raw_nodes = graph_dict.get('nodes', {})
    connections = graph_dict.get('connections', [])

    if not raw_nodes:
        return "-- Canvas is empty. Add Source Nodes to begin building your query pipeline."

    if isinstance(raw_nodes, dict):
        nodes_list = list(raw_nodes.values())
    elif isinstance(raw_nodes, list):
        nodes_list = raw_nodes
    else:
        nodes_list = []

    if not nodes_list:
        return "-- Canvas is empty. Add Source Nodes to begin building your query pipeline."

    # Identify source nodes
    source_nodes = [n for n in nodes_list if n.get('type') == 'source']
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
    join_nodes = [n for n in nodes_list if n.get('type') == 'join']
    transform_nodes = [n for n in nodes_list if n.get('type') == 'transform']
    filter_nodes = [n for n in nodes_list if n.get('type') == 'filter']
    output_nodes = [n for n in nodes_list if n.get('type') == 'output']

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
                    elif fn == 'DATE_TRUNC': expr_str = f"DATE_TRUNC('month', {col})"
                    elif fn == 'ROUND': expr_str = f"ROUND({col}, 2)"
                    elif fn == 'COALESCE': expr_str = f"COALESCE({col}, 0)"
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
            c_clean = c.strip().strip('"').replace('"', '""')
            select_parts.append(f'  {p_alias}."{c_clean}"')
    else:
        select_parts.append("  *")

    # Add columns from secondary sources if present in joins
    for idx, s_node in enumerate(source_nodes[1:]):
        s_data = s_node.get('data', {})
        s_alias = s_data.get('alias', f"t{idx+2}")
        s_cols = s_data.get('selected_columns', [])
        for sc in s_cols:
            sc_clean = sc.strip().strip('"').replace('"', '""')
            select_parts.append(f'  {s_alias}."{sc_clean}"')

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
    source_table_map = {}
    for s_node in source_nodes:
        s_data = s_node.get('data', {})
        s_tbl = s_data.get('table', '').strip()
        s_al = s_data.get('alias', '').strip()
        s_db = s_data.get('database', '').strip()
        if s_tbl:
            source_table_map[s_tbl] = s_al
            if s_db:
                source_table_map[f"{s_db}.{s_tbl}"] = s_al

    join_strs = []
    for idx, j_node in enumerate(join_nodes):
        j_data = j_node.get('data', {})
        raw_joins = j_data.get('joins', [])
        if not raw_joins and j_data.get('table'):
            raw_joins = [j_data]

        # If join node has no joins configured, auto-populate from secondary source nodes on canvas
        if not raw_joins and len(source_nodes) > 1:
            raw_joins = []
            for s_idx, s_node in enumerate(source_nodes[1:]):
                s_data = s_node.get('data', {})
                s_tbl = s_data.get('table', '').strip()
                if s_tbl:
                    s_al = s_data.get('alias', '').strip() or f"t{s_idx+2}"
                    raw_joins.append({
                        'type': 'LEFT JOIN',
                        'database': s_data.get('database', p_db),
                        'schema': s_data.get('schema', 'main'),
                        'table': s_tbl,
                        'alias': s_al,
                        'on_left': f"{p_alias}.id",
                        'on_right': f"{s_al}.id"
                    })

        for j_sub_idx, item in enumerate(raw_joins):
            j_type = item.get('type', 'LEFT JOIN').upper()
            j_db = item.get('database', '').strip() or p_db
            j_schema = item.get('schema', 'main')
            j_tbl = item.get('table', '').strip()
            
            # Use source node alias if available and alias is generic or matches table
            s_alias_match = source_table_map.get(j_tbl) or source_table_map.get(f"{j_db}.{j_tbl}")
            j_alias = item.get('alias', '').strip()
            if not j_alias or (j_alias.startswith('t') and j_alias[1:].isdigit() and s_alias_match):
                j_alias = s_alias_match or j_alias or f"t{len(join_strs)+2}"
            if not j_alias:
                j_alias = f"t{len(join_strs)+2}"

            on_left = item.get('on_left', '').strip() or f"{p_alias}.id"
            on_right = item.get('on_right', '').strip() or f"{j_alias}.id"

            if not j_tbl:
                continue

            if mode == 'dbt':
                if j_db in ('dbt_workspace', 'main', 'default', '') or not j_db:
                    j_table_ref = f"{{{{ ref('{j_tbl}') }}}}"
                else:
                    j_table_ref = f"{{{{ source('{j_db}', '{j_tbl}') }}}}"
            else:
                j_table_ref = f"\"{j_db}\".\"{j_schema}\".\"{j_tbl}\"" if j_db else f"\"{j_tbl}\""

            if j_type == 'CROSS JOIN':
                join_line = f"CROSS JOIN {j_table_ref} AS {j_alias}"
            else:
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
                    val_str = val.strip()
                    if (val_str.startswith("'") and val_str.endswith("'")) or (val_str.startswith('"') and val_str.endswith('"')):
                        where_parts.append(f"{col} {op} {val_str}")
                    else:
                        escaped_val = val_str.replace("'", "''")
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
