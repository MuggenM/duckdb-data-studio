import os
import boto3
import duckdb

def sync_catalog(conn=None):
    access_key = os.environ.get("AWS_ACCESS_KEY_ID") or "GK2713753aca1d72db5325f212"
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or "afd53ab8d8e6f762973bab0b5a33998265530dee63cae200e1a8e065be2a4b6e"
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL") or "http://garage:3900"
    region_name = os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    
    print(f"INFO: Running S3 Delta Catalog Sync (Endpoint: {endpoint_url}, Key ID: {access_key})...", flush=True)
    
    # Connect to S3 using boto3
    try:
        s3 = boto3.resource(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name
        )
        bucket_name = "devbucket"
        bucket = s3.Bucket(bucket_name)
        
        # Discover Delta tables directories (supporting recursively nested schemas/subfolders before _delta_log)
        delta_tables = {}
        for obj in bucket.objects.filter(Prefix="tables/"):
            key = obj.key
            if "_delta_log/" in key:
                prefix_part = key.split("_delta_log/")[0].rstrip("/")
                parts = prefix_part.split("/")
                if len(parts) >= 3:
                    sub_parts = parts[1:]
                    view_name = "_".join(sub_parts)
                    s3_path = f"s3://{bucket_name}/{prefix_part}"
                    delta_tables[view_name] = s3_path
    except Exception as e:
        print(f"ERROR: Failed to scan S3 bucket: {e}", flush=True)
        return False
        
    print(f"INFO: Discovered Delta tables: {list(delta_tables.keys())}", flush=True)
    
    created_own_conn = False
    if conn is None:
        from config_manager import get_main_db_path
        from db_explorer import DB_CONFIG
        db_path = get_main_db_path()
        try:
            conn = duckdb.connect(db_path, config=DB_CONFIG)
            created_own_conn = True
        except Exception as conn_err:
            print(f"ERROR: Could not open connection to DuckDB: {conn_err}", flush=True)
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
        
        # Create catalog schema
        conn.execute("CREATE SCHEMA IF NOT EXISTS s3_delta_catalog;")
        
        # Get existing views to drop obsolete ones
        existing_views_rows = conn.execute("""
            SELECT table_name 
            FROM information_schema.views 
            WHERE table_schema = 's3_delta_catalog';
        """).fetchall()
        existing_views = {row[0] for row in existing_views_rows}
        
        # Create views for active tables
        for view_name, s3_path in delta_tables.items():
            conn.execute(f"CREATE OR REPLACE VIEW s3_delta_catalog.{view_name} AS SELECT * FROM delta_scan('{s3_path}');")
            print(f"INFO: Created/updated view s3_delta_catalog.{view_name} -> {s3_path}", flush=True)
            
        # Drop obsolete views
        for obsolete_view in existing_views - set(delta_tables.keys()):
            conn.execute(f"DROP VIEW IF EXISTS s3_delta_catalog.{obsolete_view};")
            print(f"INFO: Dropped obsolete view s3_delta_catalog.{obsolete_view}", flush=True)
            
        print("INFO: S3 Delta Catalog Sync completed successfully.", flush=True)
        return True
    except Exception as e:
        print(f"ERROR: Failed to update DuckDB views: {e}", flush=True)
        return False
    finally:
        if created_own_conn and conn:
            conn.close()

if __name__ == "__main__":
    sync_catalog()
