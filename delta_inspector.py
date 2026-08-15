import os
import json
import boto3
import time
from datetime import datetime
from config_manager import load_app_settings

# Prevent AWS SDK / delta-rs from attempting IMDS metadata lookup (169.254.169.254)
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"

_TABLES_CACHE = {"timestamp": 0, "tables": []}

def get_s3_client():
    """Create a boto3 S3 client using environment variables or studio config."""
    endpoint_url = os.getenv("AWS_ENDPOINT_URL", "http://garage:3900")
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "GK2713753aca1d72db5325f212")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "afd53ab8d8e6f762973bab0b5a33998265530dee63cae200e1a8e065be2a4b6e")
    region_name = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    
    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region_name
    )

def discover_all_delta_tables(force_refresh=False):
    """Discover all Delta Lake table paths across configured S3 buckets (cached for 60s)."""
    now = time.time()
    if not force_refresh and _TABLES_CACHE["tables"] and (now - _TABLES_CACHE["timestamp"]) < 60:
        return _TABLES_CACHE["tables"]

    settings = load_app_settings()
    bucket_names = settings.get("s3_catalog_buckets", ["prodbucket", "devbucket"])
    s3_client = get_s3_client()
    
    discovered_tables = []
    for bucket in bucket_names:
        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket)
            for page in pages:
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if key.endswith('_delta_log/00000000000000000000.json') or ('/_delta_log/' in key and key.endswith('.json')):
                        table_prefix = key.split('/_delta_log/')[0]
                        s3_uri = f"s3://{bucket}/{table_prefix}"
                        if s3_uri not in [t['uri'] for t in discovered_tables]:
                            parts = table_prefix.split('/')
                            table_name = parts[-1] if parts else table_prefix
                            schema_name = parts[-2] if len(parts) > 1 else "main"
                            discovered_tables.append({
                                "bucket": bucket,
                                "prefix": table_prefix,
                                "schema": schema_name,
                                "name": table_name,
                                "uri": s3_uri,
                                "label": f"{bucket}: {schema_name}.{table_name} ({s3_uri})"
                            })
        except Exception as e:
            print(f"WARNING: Error discovering Delta tables in bucket '{bucket}': {e}", flush=True)
            
    res = sorted(discovered_tables, key=lambda x: x['label'])
    _TABLES_CACHE["tables"] = res
    _TABLES_CACHE["timestamp"] = now
    return res

def get_delta_table_history(s3_uri):
    """
    Parse commit logs (_delta_log/*.json) for a given Delta table URI.
    Returns commit history sorted descending by version number.
    """
    if not s3_uri.startswith("s3://"):
        return []
        
    parts = s3_uri.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    log_prefix = f"{prefix}/_delta_log/" if prefix else "_delta_log/"
    
    s3_client = get_s3_client()
    commits = []
    
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket, Prefix=log_prefix)
        
        for page in pages:
            for obj in page.get('Contents', []):
                key = obj['Key']
                filename = os.path.basename(key)
                if filename.endswith('.json') and filename[:-5].isdigit():
                    version = int(filename[:-5])
                    last_modified = obj['LastModified'].strftime('%Y-%m-%d %H:%M:%S') if 'LastModified' in obj else "N/A"
                    
                    # Read commit JSON content
                    try:
                        res = s3_client.get_object(Bucket=bucket, Key=key)
                        content = res['Body'].read().decode('utf-8')
                        
                        commit_info = {}
                        add_count = 0
                        remove_count = 0
                        
                        for line in content.strip().split('\n'):
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                                if 'commitInfo' in entry:
                                    commit_info = entry['commitInfo']
                                elif 'add' in entry:
                                    add_count += 1
                                elif 'remove' in entry:
                                    remove_count += 1
                            except Exception:
                                pass
                                
                        op = commit_info.get('operation', 'WRITE')
                        timestamp_ms = commit_info.get('timestamp')
                        if timestamp_ms:
                            try:
                                commit_time = datetime.fromtimestamp(timestamp_ms / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
                            except Exception:
                                commit_time = last_modified
                        else:
                            commit_time = last_modified
                            
                        commits.append({
                            "version": version,
                            "version_label": f"v{version}",
                            "timestamp": commit_time,
                            "operation": op,
                            "added_files": add_count,
                            "remove_files": remove_count,
                            "client": commit_info.get('engineInfo', commit_info.get('notebook', 'dbt / DuckDB')),
                            "mode": commit_info.get('operationMode', 'Overwrite' if remove_count > 0 else 'Append')
                        })
                    except Exception as err:
                        commits.append({
                            "version": version,
                            "version_label": f"v{version}",
                            "timestamp": last_modified,
                            "operation": "WRITE",
                            "added_files": 0,
                            "removed_files": 0,
                            "client": "DuckDB",
                            "mode": "Unknown"
                        })
    except Exception as ex:
        print(f"ERROR: Failed to fetch Delta log history for {s3_uri}: {ex}", flush=True)
        
    return sorted(commits, key=lambda x: x['version'], reverse=True)

if __name__ == "__main__":
    tables = discover_all_delta_tables()
    print(f"Discovered {len(tables)} Delta tables.")
    if tables:
        hist = get_delta_table_history(tables[0]['uri'])
        print(f"History for {tables[0]['uri']}: {hist}")
