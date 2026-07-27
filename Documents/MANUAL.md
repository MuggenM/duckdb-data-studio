# 📖 DuckDB Studio & API Explorer Manual

Welcome to the official, complete user manual for **DuckDB Studio & API Explorer**. This document covers every screen, utility, and dynamic capability built into the application. It highlights all the latest system optimizations, permission fixes, and new features.

---

## 🚀 Live Studio Feature Traversal Walkthrough

Below is a live, automated walk-through demonstrating rapid traversal across all workspace tabs inside **DuckDB Studio** in real-time, showing the modernized interface:

<img src="./assets/feature_traversal.gif" width="650" />

---

## 🧭 Workspace Navigation Overview

DuckDB Studio organizes its workspaces into specialized tabs:

| Workspace Tab | Icon | Purpose |
| :--- | :---: | :--- |
| **[1. Explorer](#1-explorer)** | `query_stats` | Database schema catalog browser, visual query builder, optimized S3 connections, and SQL editor. |
| **[2. JupyterLab](#2-jupyterlab)** | `terminal` | Embedded JupyterLab notebook interface running on system python with `duckrun` integration. |
| **[3. dbt Code Server](#3-dbt-code-server)** | `vscode_blue` | Integrated VS Code editor environment pre-configured for dbt model development. |
| **[4. Extensions](#4-extensions)** | `extension` | Graphical extension installer/loader for DuckDB's runtime libraries (spatial, httpfs, etc.). |
| **[5. Database Tools](#5-database-tools)** | `construction` | Data migration utilities including backup, structure restore, CSV/Parquet import, and scaling data seeders. |
| **[6. API Endpoints](#6-api-endpoints)** | `api` | SQL-to-REST API creator, dynamic auto-parameter parser, JWT security controls, and telemetry dashboard. |
| **[7. API Docs & Explorer](#7-api-docs--explorer)** | `menu_book` | OpenAPI interactive Swagger interface and dynamic testing sandbox. |
| **[8. Scheduler](#8-scheduler)** | `schedule` | Automated query scheduler, folder exporter, and telemetry logs retention clean-up agent. |
| **[9. Settings](#9-settings)** | `settings` | Global system settings and credentials manager. |

---

## 🛠️ Detailed Screen-by-Screen Walkthrough

### 1. Explorer

The core SQL IDE of DuckDB Studio. It features a dual-column layout dividing the database metadata tree and active SQL editor workspace.

<img src="./assets/explorer_tab.png" width="650" />

#### Live Explorer Walkthrough:
Below is a walk-through demonstrating database schema catalog browser traversal, visual query building, and sql query execution:

<img src="./assets/explorer_traversal.gif" width="650" />

#### Key Capabilities:
* **Ad-Hoc Editor**: Write standard or complex SQL statements with hot-reloaded autocomplete and execute them using `Ctrl + Enter` or the **Run Query** button.
* **Database Catalog Tree**: Visually trace attached databases, schemas, tables, views, columns, and data types. Click any table node to automatically preview its data.
* **Visual Query Builder**: Build projection queries dynamically by checking columns, configuring sort options (`ASC`/`DESC`), and adding filter clauses in the UI.
* **Save Presets**: Save SQL queries directly into internal storage. Saved queries can be loaded back into the editor with one click.
* **Session History**: Trace and review recently run query metrics, timing, and latencies.
* **Execution Plan Visualizer**: Run logical and physical optimizer tracing via **Explain Plan** or trace dynamic execution profiling with **Explain Analyze**.

#### Latest Features & Optimizations:
* **S3 Delta Tables Catalog Sync**: On container startup and every 60 seconds in the background, S3 Delta table formats in the target bucket (`devbucket`) are automatically scanned and registered as views inside the `s3_delta_catalog` database.
* **HTTP Keep-Alive & Metadata Cache**: Every database connection is optimized with socket reuse (`http_keep_alive = true`), Parquet footer metadata memory caching (`enable_object_cache = true`), and socket timeouts (`http_timeout = 10`) to speed up S3/OneLake Delta reads and fail fast on network drops.

---

### 2. JupyterLab

An integrated interactive data science environment running on the host system python environment.

<img src="./assets/jupyterlab_tab.png" width="650" />

#### Live JupyterLab Walkthrough:
<img src="./assets/jupyterlab.gif" width="650" />

#### Python & Pandas Data Science:
* **Notebooks**: Launch Jupyter notebook kernels to write advanced Python code alongside your DuckDB instance.
* **`duckrun` Integration**: Utilize `duckrun` inside notebooks to query Delta tables on S3 storage.
* **Local Dataframe Queries**: Register Pandas DataFrames directly in the underlying database catalog using `conn.con.register("name", df)`.
* **S3 Secret Provisioning**: S3 credentials can be configured natively inside the notebook connection:
  ```python
  import duckrun
  conn = duckrun.connect()
  conn.con.execute("CREATE SECRET (TYPE S3, KEY_ID '...', SECRET '...', ENDPOINT 'garage:3900', USE_SSL false, URL_STYLE 'path')")
  ```

---

### 3. dbt Code Server

An embedded full-featured VS Code IDE server (`dbt-code-server` container running on port `8443` and routed through `editor.localhost:8880`), custom-tailored for dbt (data build tool) development against DuckDB/S3 Delta table catalogs.

<img src="./assets/code_editor_tab.png" width="650" />

#### Integrated dbt Development Workflow:
This tab exposes the complete `dbt_project/` workspace folder, enabling developers to build, compile, and document modular SQL models out-of-the-box.
* **dbt-core Engine**: The container has `dbt-core` and the `dbt-duckdb` adapter installed natively.
* **dbt Power User Integration**: Pre-loaded with the **dbt Power User** extension (`innoverio.vscode-dbt-power-user`), providing:
  * **Model Compilation & Running**: Compile models on-the-fly (`Ctrl + '`) and run individual queries directly inside the workspace view.
  * **Lineage & Dependency Trees**: Generates visual dependency graphs mapping relationships between your staging, intermediate, and dimensional models.
  * **Interactive Code Autocomplete**: Auto-completes dbt Jinja macros such as `ref()`, `source()`, and `config()`.
* **Automatic sqlfmt Formatting**: Configured to use the system-installed `shandy-sqlfmt[jinjafmt]` formatter. Whenever a Jinja-SQL file is saved, it is automatically formatted according to the pre-configured workspace rules (`.vscode/settings.json` default formatting engine).

#### Robust Compatibility Workarounds (Included):
* **Self-Healing Ownership**: Automatically runs a recursive `chown` to assign workspace files to the container's unprivileged user (`coder` UID `1000`) and a `chmod` to grant world-write access, resolving rootless Docker permission conflicts on startup.
* **Python 3.13 Serialization Fix**: Includes an automatic startup patch that overrides the extension's JSON encoder, preventing compilation crashes caused by thread lock serialization changes on Python 3.13.

---

### 4. Extensions

A visual manager for DuckDB's runtime plugins, enabling one-click library installation and loading.

<img src="./assets/extensions_tab.png" width="650" />

#### Functions:
* **Extension Grid**: Visual status cards for extensions (`httpfs`, `postgres_scanner`, `sqlite_scanner`, `spatial`, `icu`, `json`, `ducklake`).
* **Interactive Badges**: Color indicators showing whether an extension is **Installed** (Grey) or **Loaded** (Green).
* **Actions**: Click **Install** to pull the binary directly from DuckDB's servers, and **Load** to initialize it into the active execution connection.

---

### 5. Database Tools

A backup, restore, file importer, and scalability benchmarker for DuckDB databases.

<img src="./assets/database_tools_tab.png" width="650" />

#### Capabilities:
* **CSV/Parquet File Importing**: Use the file picker to import CSV, JSON, or Parquet files directly from your workspace directory into your active database catalog.
* **Catalog Backup & Restore**: Export the structural database catalog into recovery SQL files and execute them to restore schemas, tables, and views structure.
* **Scalability Seeding**: Move the record density slider from `100` to `100,000` rows and click **Trigger Seed Generation** to populate test tables, allowing you to validate performance metrics under realistic data scale.

---

### 6. API Endpoints

Turn any SQL select query into an active REST API microservice and monitor live metrics.

<img src="./assets/api_endpoints_tab.png" width="650" />

#### Live API Endpoints Walkthrough:
<img src="./assets/api_endpoints.gif" width="650" />

#### Dynamic REST APIs:
* **Auto-Generated Query Parameters**: Use the `$parameter_name` notation to automatically capture dynamic query parameters from incoming requests (e.g. `?min_qty=10`).
* **Column Analysis Filter Creator**: Under the SQL editor, click **Analyze Columns for Auto-Params** to parse the schema of your target database table and auto-generate parameter logic based on dynamic ranges.
* **API Route Listing**: View all registered endpoints at `/api/list-endpoints` before catch-all wildcards hijack paths.

#### Security & Auth:
* **JWT Authentication Toggle**: Enable or disable JWT verification on-the-fly. When enabled, requests require passing the Bearer token in the header (`Authorization: Bearer <token>`), verified using `HS256` symmetric signing with the global `STORAGE_SECRET`.

#### High-Performance Streaming:
* **NDJSON Streaming**: Append `/stream` to any endpoint URL (e.g. `/api/orders/stream`) to stream massive datasets row-by-row in Newline Delimited JSON (`application/x-ndjson`) using HTTP chunked transfer encoding, maintaining a flat memory footprint.

#### Rate Limiting & Throttling:
* **SlowAPI Integration**: Define specific request throttle limits per endpoint (e.g. `10/minute`, `100/hour`) to prevent server overload.

#### Telemetry Dashboard:
* Monitor overall KPI metrics (Total requests, average response speeds, success rates) and audit detailed routes logs (Min/Max Latency, Success Ratios, and Trigger times) directly.

---

### 7. API Docs & Explorer

An embedded Swagger-style sandbox to document and run loops against dynamic APIs.

<img src="./assets/api_docs_tab.png" width="650" />

#### Live API Docs Walkthrough:
<img src="./assets/api_docs_explorer.gif" width="650" />

#### Interactive Docs Sandbox:
* **Interactive Sandbox**: Auto-detects endpoint parameters and generates input forms inside the UI.
* **JWT Testing Sandbox**: Paste authorization tokens into the token field to test secured endpoints directly.
* **Loopback Executor**: Executes requests via internal HTTP loops, measuring request latency, status codes, and absolute URLs.
* **Formatted JSON View**: Renders dynamic query results as formatted syntax-highlighted JSON trees.

---

### 8. Scheduler

Automate your query reporting and data extraction pipelines.

<img src="./assets/scheduler_tab.png" width="650" />

#### Live Scheduler Walkthrough:
<img src="./assets/scheduler.gif" width="650" />

#### Job Automation:
* **Preset Loader**: Pull queries from Saved Queries into the form with one click.
* **Interval Scheduler**: Configure intervals (`Every Minute`, `Every Hour`, `Daily`, etc.) to trigger background tasks.
* **Export Configurations**: Select Parquet, CSV, or JSON formats. Type in a partition column (e.g. `category`) to partition the folder natively using DuckDB's fast `PARTITION_BY` system.
* **Automation Grid**: Toggle job statuses, manually run queries instantly with visual toast notifications, and trace rows/file sizes inside the execution logs history grid.

#### Periodic Telemetry Cleanup (New Optimization):
* An hourly background task cleans up SQLite logs to keep the config database size small. It automatically prunes:
  * API metrics (`_duckdb_studio_api_metrics`) older than 7 days.
  * Scheduler run logs (`_duckdb_studio_scheduler_logs`) older than 7 days.
  * SQL console execution history (`_duckdb_studio_query_history`) older than 7 days.

---

### 9. Settings

A centralized control panel to manage global application parameters, safety overrides, telemetry settings, security keys, and external notebook credentials.

#### Configurations:
* **Rate Limiting & Safety Limits**: Configure default rate limits, maximum query row returns, and default pagination page sizes.
* **Security & JWT**: Customizes JWT signature secrets, issuer names, and audiences.
* **Telemetry Config**: Set retention duration for database telemetry metrics.
* **Jupyter Credentials**: Configure the Jupyter server URL and token.
