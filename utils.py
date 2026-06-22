import re

def split_sql_trailing_clauses(sql, keywords=None):
    if keywords is None:
        keywords = ['ORDER BY', 'LIMIT', 'OFFSET']
    sql_upper = sql.upper()
    depth = 0
    in_single_quote = False
    in_double_quote = False
    in_comment = False
    in_block_comment = False
    keyword_idx = -1
    i = 0
    n = len(sql)
    while i < n:
        if in_comment:
            if sql[i] == '\n':
                in_comment = False
            i += 1
            continue
        if in_block_comment:
            if i + 1 < n and sql[i:i+2] == '*/':
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_single_quote:
            if sql[i] == "'":
                if i + 1 < n and sql[i+1] == "'":
                    i += 2
                else:
                    in_single_quote = False
                    i += 1
            else:
                i += 1
            continue
        if in_double_quote:
            if sql[i] == '"':
                in_double_quote = False
            i += 1
            continue
            
        if i + 1 < n and sql[i:i+2] == '--':
            in_comment = True
            i += 2
            continue
        if i + 1 < n and sql[i:i+2] == '/*':
            in_block_comment = True
            i += 2
            continue
        if sql[i] == "'":
            in_single_quote = True
            i += 1
            continue
        if sql[i] == '"':
            in_double_quote = True
            i += 1
            continue

        if sql[i] == '(':
            depth += 1
        elif sql[i] == ')':
            depth -= 1
        elif depth == 0:
            for kw in keywords:
                kw_len = len(kw)
                if i + kw_len <= n and sql_upper[i:i+kw_len] == kw:
                    prev_char_ok = (i == 0 or not sql_upper[i-1].isalnum() and sql_upper[i-1] != '_')
                    next_char_ok = (i + kw_len == n or not sql_upper[i+kw_len].isalnum() and sql_upper[i+kw_len] != '_')
                    if prev_char_ok and next_char_ok:
                        keyword_idx = i
                        break
            if keyword_idx != -1:
                break
        i += 1
        
    if keyword_idx != -1:
        return sql[:keyword_idx].rstrip(), " " + sql[keyword_idx:].strip()
    return sql, ""

def has_top_level_where(sql):
    sql_upper = sql.upper()
    depth = 0
    in_single_quote = False
    in_double_quote = False
    in_comment = False
    in_block_comment = False
    i = 0
    n = len(sql)
    while i < n:
        if in_comment:
            if sql[i] == '\n':
                in_comment = False
            i += 1
            continue
        if in_block_comment:
            if i + 1 < n and sql[i:i+2] == '*/':
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_single_quote:
            if sql[i] == "'":
                if i + 1 < n and sql[i+1] == "'":
                    i += 2
                else:
                    in_single_quote = False
                    i += 1
            else:
                i += 1
            continue
        if in_double_quote:
            if sql[i] == '"':
                in_double_quote = False
            i += 1
            continue
            
        if i + 1 < n and sql[i:i+2] == '--':
            in_comment = True
            i += 2
            continue
        if i + 1 < n and sql[i:i+2] == '/*':
            in_block_comment = True
            i += 2
            continue
        if sql[i] == "'":
            in_single_quote = True
            i += 1
            continue
        if sql[i] == '"':
            in_double_quote = True
            i += 1
            continue

        if sql[i] == '(':
            depth += 1
        elif sql[i] == ')':
            depth -= 1
        elif depth == 0:
            if i + 5 <= n and sql_upper[i:i+5] == 'WHERE':
                prev_char_ok = (i == 0 or not sql_upper[i-1].isalnum() and sql_upper[i-1] != '_')
                next_char_ok = (i + 5 == n or not sql_upper[i+5].isalnum() and sql_upper[i+5] != '_')
                if prev_char_ok and next_char_ok:
                    return True
        i += 1
    return False

def format_column_projection_query(cols, fq_name, limit=100):
    col_names = [f'"{c[0]}"' for c in cols]
    max_line_width = 80
    lines = ["SELECT"]
    
    current_line = []
    current_len = 4
    
    for i, col in enumerate(col_names):
        is_last = (i == len(col_names) - 1)
        suffix = "" if is_last else ","
        col_with_suffix = col + suffix
        col_len = len(col_with_suffix)
        
        if current_line and (current_len + 2 + col_len > max_line_width):
            lines.append("    " + ", ".join(current_line) + ",")
            current_line = [col]
            current_len = 4 + len(col)
        else:
            current_line.append(col)
            if len(current_line) == 1:
                current_len = 4 + len(col)
            else:
                current_len += 2 + len(col)
                
    if current_line:
        lines.append("    " + ", ".join(current_line))
        
    lines.append(f"FROM {fq_name}")
    lines.append(f"LIMIT {limit};")
    return "\n".join(lines)

def detect_parameters(sql: str) -> list:
    if not sql:
        return []
    matches = re.findall(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}', sql)
    seen = set()
    unique_params = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            unique_params.append(m)
    return unique_params

def substitute_sql_parameters(sql: str, param_values: dict) -> str:
    if not sql:
        return sql
    
    def replacer(match):
        param_name = match.group(1)
        val = param_values.get(param_name, "")
        if val is None:
            val = ""
        val_str = str(val)
        if re.match(r'^-?\d+(\.\d+)?$', val_str):
            return val_str
        escaped_val = val_str.replace("'", "''")
        return f"'{escaped_val}'"
        
    return re.sub(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}', replacer, sql)

def sanitize_table_name(name):
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)

def verify_jwt_token(auth_header: str, secret: str):
    from fastapi import HTTPException
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    try:
        if " " not in auth_header:
            raise ValueError("Invalid auth header format")
        token_type, token = auth_header.split(" ", 1)
        if token_type.lower() != "bearer":
            raise ValueError("Token must be a Bearer token")
            
        import jwt
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")
