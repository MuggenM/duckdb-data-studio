import os
import boto3
import duckdb

def load_catalog_settings():
    config_paths = ['/config/studio_config.yaml', 'config/studio_config.yaml']
    settings = {
        "s3_catalog_buckets": os.environ.get("S3_CATALOG_BUCKETS", "prodbucket, devbucket"),
        "s3_catalog_database": os.environ.get("S3_CATALOG_DATABASE", "/databases/dbt_workspace.duckdb")
    }
    for path in config_paths:
        if os.path.exists(path):
            try:
                import yaml
                with open(path, 'r') as f:
                    config = yaml.safe_load(f)
                if config and isinstance(config, dict):
                    app_settings = config.get('settings', {})
                    if isinstance(app_settings, dict):
                        if app_settings.get('s3_catalog_buckets'):
                            settings['s3_catalog_buckets'] = app_settings['s3_catalog_buckets']
                        if app_settings.get('s3_catalog_database'):
                            settings['s3_catalog_database'] = app_settings['s3_catalog_database']
            except Exception:
                pass
    return settings

def sync_catalog(conn=None):
    access_key = os.environ.get("AWS_ACCESS_KEY_ID") or "GK2713753aca1d72db5325f212"
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or "afd53ab8d8e6f762973bab0b5a33998265530dee63cae200e1a8e065be2a4b6e"
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL") or "http://garage:3900"
    region_name = os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    
    catalog_settings = load_catalog_settings()
    buckets_raw = catalog_settings.get("s3_catalog_buckets", ["prodbucket", "devbucket"])
    if isinstance(buckets_raw, list):
        target_bucket_names = [str(b).strip() for b in buckets_raw if str(b).strip()]
    else:
        target_bucket_names = [b.strip() for b in str(buckets_raw).split(",") if b.strip()]
        
    target_db_path = catalog_settings.get("s3_catalog_database", "/databases/dbt_workspace.duckdb")
    base_name = os.path.basename(target_db_path)
    if base_name.lower().endswith('.duckdb'):
        base_name = base_name[:-7]
    if not base_name:
        base_name = "dbt_workspace"
        
    if not os.path.exists("/databases") and os.path.exists("/app/databases"):
        target_db_path = f"/app/databases/{base_name}.duckdb"
    else:
        target_db_path = f"/databases/{base_name}.duckdb"
        
    db_dir = os.path.dirname(target_db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
        
    if not os.path.exists(target_db_path):
        print(f"INFO: Catalog database file '{target_db_path}' does not exist. Creating new database...", flush=True)
        
    print(f"INFO: Running S3 Delta Catalog Sync to {target_db_path} for buckets: {target_bucket_names} (Endpoint: {endpoint_url})...", flush=True)
    
    # Connect to S3 using boto3
    try:
        s3 = boto3.resource(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name
        )
        
        delta_tables = {}
        all_buckets = list(s3.buckets.all())
        
        # Filter buckets to scan based on user settings
        if target_bucket_names and "*" not in target_bucket_names and "all" not in [b.lower() for b in target_bucket_names]:
            buckets_to_scan = [b for b in all_buckets if b.name in target_bucket_names]
        else:
            buckets_to_scan = all_buckets
            
        first_bucket = target_bucket_names[0] if target_bucket_names else "prodbucket"
        buckets_to_scan.sort(key=lambda b: 0 if b.name == first_bucket else 1)
        
        for b in buckets_to_scan:
            bucket_name = b.name
            print(f"INFO: Scanning bucket '{bucket_name}' for Delta tables...", flush=True)
            
            for obj in b.objects.all():
                key = obj.key
                if "_delta_log/" in key:
                    prefix_part = key.split("_delta_log/")[0].rstrip("/")
                    parts = prefix_part.split("/")
                    
                    if len(parts) >= 3:
                        folder_type = parts[0]
                        raw_schema = parts[1]
                        table_name = "_".join(parts[2:])
                        s3_path = f"s3://{bucket_name}/{prefix_part}"
                        
                        if bucket_name == first_bucket and folder_type == "tables":
                            schema_name = raw_schema
                            delta_tables[(schema_name, table_name)] = s3_path
                        elif folder_type == "dev_tables":
                            schema_name = f"dev_{raw_schema}"
                            delta_tables[(schema_name, table_name)] = s3_path
                        elif folder_type == "tables":
                            schema_name = raw_schema
                            if (schema_name, table_name) not in delta_tables:
                                delta_tables[(schema_name, table_name)] = s3_path
                        else:
                            schema_name = f"{bucket_name}_{raw_schema}"
                            if (schema_name, table_name) not in delta_tables:
                                delta_tables[(schema_name, table_name)] = s3_path

    except Exception as e:
        print(f"ERROR: Failed to scan S3 buckets: {e}", flush=True)
        return False
        
    print(f"INFO: Discovered Delta tables count: {len(delta_tables)}", flush=True)
    
    created_own_conn = False
    if conn is None:
        try:
            conn = duckdb.connect(target_db_path)
            created_own_conn = True
        except Exception:
            try:
                conn = duckdb.connect(target_db_path, read_only=True)
                created_own_conn = True
            except Exception as conn_err:
                print(f"ERROR: Could not open connection to {target_db_path}: {conn_err}", flush=True)
                return False
            
    try:
        try:
            conn.execute("SET http_keep_alive=true;")
            conn.execute("SET enable_object_cache=true;")
            conn.execute("SET http_timeout=10;")
        except Exception as set_ex:
            print(f"WARNING: failed to configure HTTP/S3 settings in sync script: {set_ex}", flush=True)
            
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute("INSTALL delta; LOAD delta;")
        
        # Create S3 secrets natively in DuckDB
        conn.execute(f"""
            CREATE OR REPLACE SECRET s3_sync_secret (
                TYPE S3,
                KEY_ID '{access_key}',
                SECRET '{secret_key}',
                ENDPOINT '{endpoint_url.replace("http://", "").replace("https://", "")}',
                REGION '{region_name}',
                USE_SSL false,
                URL_STYLE 'path'
            );
        """)
        
        # Track active schemas and views
        schemas_created = set()
        active_views = set()
        
        for (schema_name, table_name), s3_path in delta_tables.items():
            if schema_name not in schemas_created:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name};")
                schemas_created.add(schema_name)
                
            conn.execute(f"DROP VIEW IF EXISTS {schema_name}.{table_name};")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}.{table_name};")
            conn.execute(f"CREATE OR REPLACE VIEW {schema_name}.{table_name} AS SELECT * FROM delta_scan('{s3_path}');")
            active_views.add((schema_name, table_name))
            print(f"INFO: Created/updated view {schema_name}.{table_name} -> {s3_path}", flush=True)
            
        print(f"INFO: S3 Delta Catalog Sync to {target_db_path} completed successfully.", flush=True)
        return True
    except Exception as e:
        print(f"ERROR: Failed to update DuckDB views in {target_db_path}: {e}", flush=True)
        return False
    finally:
        if created_own_conn and conn:
            conn.close()

if __name__ == "__main__":
    sync_catalog()
