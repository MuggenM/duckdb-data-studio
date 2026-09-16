{% macro sync_s3_catalog() %}
  {% if execute %}
    {{ log("INFO: Running post-run S3 Catalog Sync hook...", info=True) }}
    {% set python_script = "/app/s3_catalog_sync.py" %}
    {% set alt_python_script = "/home/coder/project/s3_catalog_sync.py" %}
    {% set script_path = python_script if modules.os.path.exists(python_script) else alt_python_script %}
    
    {% if modules.os.path.exists(script_path) %}
      {% set res = modules.subprocess.run(["python3", script_path], capture_output=True, text=True) %}
      {{ log(res.stdout, info=True) }}
      {% if res.stderr %}
        {{ log(res.stderr, info=True) }}
      {% endif %}
    {% else %}
      {{ log("WARNING: s3_catalog_sync.py not found at " ~ script_path, info=True) }}
    {% endif %}
  {% endif %}
{% endmacro %}
