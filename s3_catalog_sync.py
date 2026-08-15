import os
import boto3
import duckdb

def sync_catalog(conn=None):
    access_key = os.environ.get("AWS_ACCESS_KEY_ID") or "GK2713753aca1d72db5325f212"
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or "afd53ab8d8e6f762973bab0b5a33998265530dee63cae200e1a8e065be2a4b6e"
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL") or "http://garage:3900"
    region_name = os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    
    print(f"INFO: Running S3 Delta Catalog Sync to dbt_workspace.duckdb (Endpoint: {endpoint_url})...", flush=True)
    
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
        
        # Discover Delta tables directories (schema / table taxonomy under tables/)
        delta_tables = {}
        for obj in bucket.objects.filter(Prefix="tables/"):
            key = obj.key
            if "_delta_log/" in key:
                prefix_part = key.split("_delta_log/")[0].rstrip("/")
                parts = prefix_part.split("/")
                # Expecting format: tables/<schema_name>/<table_name> or tables/<schema_name>/.../<table_name>
                if len(parts) >= 3:
                    schema_name = parts[1]
                    table_name = "_".join(parts[2:])
                    s3_path = f"s3://{bucket_name}/{prefix_part}"
                    delta_tables[(schema_name, table_name)] = s3_path
    except Exception as e:
        print(f"ERROR: Failed to scan S3 bucket: {e}", flush=True)
        return False
        
    print(f"INFO: Discovered Delta tables: {[f'{s}.{t}' for s, t in delta_tables.keys()]}", flush=True)
    
    # Target database for catalog views is dbt_workspace.duckdb
    target_db_path = "/databases/dbt_workspace.duckdb"
    
    created_own_conn = False
    if conn is None:
        try:
            conn = duckdb.connect(target_db_path)
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
                
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}.{table_name};")
            conn.execute(f"CREATE OR REPLACE VIEW {schema_name}.{table_name} AS SELECT * FROM delta_scan('{s3_path}');")
            active_views.add((schema_name, table_name))
            print(f"INFO: Created/updated view {schema_name}.{table_name} -> {s3_path}", flush=True)
            
        print("INFO: S3 Delta Catalog Sync to dbt_workspace.duckdb completed successfully.", flush=True)
        return True
    except Exception as e:
        print(f"ERROR: Failed to update DuckDB views in dbt_workspace.duckdb: {e}", flush=True)
        return False
    finally:
        if created_own_conn and conn:
            conn.close()

if __name__ == "__main__":
    sync_catalog()
