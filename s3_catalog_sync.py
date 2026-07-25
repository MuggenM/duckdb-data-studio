import os
import boto3
import duckdb

def sync_catalog():
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
        
        # Discover Delta tables directories (supporting schemas like /tables/main/stg_orders)
        delta_tables = {}
        for obj in bucket.objects.filter(Prefix="tables/"):
            key = obj.key
            if "_delta_log/" in key:
                parts = key.split("/")
                # Expecting 'tables/<schema>/<table_name>/_delta_log/...' -> len(parts) >= 4
                if len(parts) >= 4:
                    schema_name = parts[1]
                    table_name = parts[2]
                    view_name = f"{schema_name}_{table_name}"
                    s3_path = f"s3://{bucket_name}/tables/{schema_name}/{table_name}"
                    delta_tables[view_name] = s3_path
    except Exception as e:
        print(f"ERROR: Failed to scan S3 bucket: {e}", flush=True)
        return False
        
    print(f"INFO: Discovered Delta tables: {list(delta_tables.keys())}", flush=True)
    
    # Connect to DuckDB database
    from config_manager import get_main_db_path
    from db_explorer import DB_CONFIG
    db_path = get_main_db_path()
    
    conn = None
    try:
        conn = duckdb.connect(db_path, config=DB_CONFIG)
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
            
        conn.commit()
        print("INFO: S3 Delta Catalog Sync completed successfully.", flush=True)
        return True
    except Exception as e:
        print(f"ERROR: Failed to update DuckDB views: {e}", flush=True)
        return False
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    sync_catalog()
