import atexit
import sys
import os

from dbt.adapters.duckdb.plugins import BasePlugin

class Plugin(BasePlugin):
    _registered = False

    def initialize(self, plugin_config):
        if not Plugin._registered:
            def _on_dbt_run_end():
                try:
                    print("\nINFO: [dbt post-run-hook] Triggering automatic S3 Delta Catalog Sync...", flush=True)
                    # Add project and app paths to sys.path if missing
                    proj_paths = ['/app', '/home/coder/project', os.getcwd()]
                    for p in proj_paths:
                        if p not in sys.path and os.path.exists(p):
                            sys.path.insert(0, p)
                            
                    import s3_catalog_sync
                    s3_catalog_sync.sync_catalog()
                except Exception as e:
                    print(f"ERROR: [dbt post-run-hook] S3 Catalog Sync failed: {e}", flush=True)

            atexit.register(_on_dbt_run_end)
            Plugin._registered = True
