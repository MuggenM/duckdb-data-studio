# 📖 DuckDB Studio & API Explorer Manual

Welcome to the official, complete user manual for **DuckDB Studio & API Explorer**. This document covers every screen, utility, and dynamic capability built into the application. It highlights all the latest system optimizations, permission fixes, and new features.

---

## 🚀 Live Studio Feature Traversal Walkthrough

Below is a live, automated walk-through demonstrating rapid traversal across all 7 workspace tabs inside **DuckDB Studio** in real-time:

<img src="./assets/feature_traversal.gif" width="650" />

---

## 🧭 Workspace Navigation Overview

DuckDB Studio organizes its workspaces into specialized tabs:

| Workspace Tab | Icon | Purpose |
| :--- | :---: | :--- |
| **[1. Explorer](#1-explorer)** | `query_stats` | Database schema catalog browser, visual query builder, optimized S3 connections, and SQL editor. |
| **[2. JupyterLab](#2-jupyterlab)** | `terminal` | Embedded JupyterLab notebook interface running on system python with `duckrun` integration. |
| **[3. Extensions](#3-extensions)** | `extension` | Graphical extension installer/loader for DuckDB's runtime libraries (spatial, httpfs, etc.). |
| **[4. Database Tools](#4-database-tools)** | `construction` | Data migration utilities including backup, structure restore, CSV/Parquet import, and scaling data seeders. |
| **[5. API Endpoints](#5-api-endpoints)** | `api` | SQL-to-REST API creator, dynamic auto-parameter parser, JWT security controls, and telemetry dashboard. |
| **[6. API Docs & Explorer](#6-api-docs--explorer)** | `menu_book` | OpenAPI interactive Swagger interface and dynamic testing sandbox. |
| **[7. Scheduler](#7-scheduler)** | `schedule` | Automated query scheduler, folder exporter, and telemetry logs retention clean-up agent. |
| **[8. Settings](#8-settings)** | `settings` | Global configurations for rate limiters, pagination size, JWT signature keys, and telemetry metrics. |

---

## 🛠️ Detailed Screen-by-Screen Walkthrough

### 1. Explorer

The core SQL IDE of DuckDB Studio. It features a dual-column layout dividing the database metadata tree and active SQL editor workspace.

<img src="./assets/explorer_tab.png" width="650" />

#### Catalog Tree & Schema Browser
Browse attached databases, schemas, tables, views, columns, and data types recursively. Clicking any table node automatically queries it and displays a preview in the query output pane.

<img src="./assets/schema_browser.gif" width="650" />

#### Visual Query Builder
Build projection queries dynamically by checking columns, configuring sort options (`ASC`/`DESC`), and adding filter clauses in the UI without writing raw SQL.

<img src="./assets/interactive_visual_query_builder.gif" width="650" />

#### SQL Console & Presets
* **Ad-Hoc Editor**: Write standard or complex SQL statements with hot-reloaded autocomplete and execute them using `Ctrl + Enter` or the **Run Query** button.
* **Save Presets**: Save SQL queries directly into internal storage. Saved queries can be loaded back into the editor with one click.
* **Session History**: Trace and review recently run query metrics, timing, and latencies.

#### Execution Plan Visualizer
* **Explain Plan**: Run logical and physical optimizer tracing on your query without executing it.
* **Explain Analyze**: Trace dynamic execution timing and profile node statistics inside the catalog.

#### Latest Features & Optimizations in Explorer:
* **S3 Delta Tables Catalog Sync**: On container startup and every 60 seconds in the background, S3 Delta table formats in the target bucket (`devbucket`) are automatically scanned and registered as views inside the `s3_delta_catalog` database.
* **HTTP Keep-Alive & Metadata Cache**: Every database connection is optimized with socket reuse (`http_keep_alive = true`), Parquet footer metadata memory caching (`enable_object_cache = true`), and socket timeouts (`http_timeout = 10`) to speed up S3/OneLake Delta reads and fail fast on network drops.

---

### 2. JupyterLab

An integrated interactive data science environment running on the host system python environment.

<img src="./assets/jupyterlab_tab.png" width="650" />

#### Python & Pandas Data Science
* **Notebooks**: Launch Jupyter notebook kernels to write advanced Python code alongside your DuckDB instance.
* **`duckrun` Integration**: Utilize `duckrun` inside notebooks to query Delta tables on S3 storage.
* **Local Dataframe Queries**: Register Pandas DataFrames directly in the underlying database session catalog using `conn.con.register("name", df)`.
* **S3 Secret Provisioning**: S3 credentials can be configured natively inside the notebook connection:
  ```python
  import duckrun
  conn = duckrun.connect()
  conn.con.execute("CREATE SECRET (TYPE S3, KEY_ID '...', SECRET '...', ENDPOINT 'garage:3900', USE_SSL false, URL_STYLE 'path')")
  ```

---

### 3. Extensions

A visual manager for DuckDB's runtime plugins, enabling one-click library installation and loading.

<img src="./assets/extensions.gif" width="650" />

#### Functions:
* **Extension Grid**: Visual status cards for extensions (`httpfs`, `postgres_scanner`, `sqlite_scanner`, `spatial`, `icu`, `json`, `ducklake`).
* **Interactive Badges**: Color indicators showing whether an extension is **Installed** (Grey) or **Loaded** (Green).
* **Actions**: Click **Install** to pull the binary directly from DuckDB's servers, and **Load** to initialize it into the active execution connection.

---

### 4. Database Tools

A backup, restore, file importer, and scalability benchmarker for DuckDB databases.

<img src="./assets/database_tools.gif" width="650" />

#### CSV/Parquet File Importing
Use the file picker to import CSV, JSON, or Parquet files directly from your workspace directory into your active database catalog:

<img src="./assets/import_file.gif" width="650" />

#### Catalog Backup & Restore
* **Backup Utilities**: Export the structural database catalog into clean SQL recovery files.
* **Restore Catalog**: Execute a selected backup SQL file to instantly restore schemas, tables, and views structure.
* **Scalability Seeding**: Move the record density slider from `100` to `100,000` rows and click **Trigger Seed Generation** to populate test tables, allowing you to validate performance metrics under realistic data scale.

---

### 5. API Endpoints

Turn any SQL select query into an active REST API microservice and monitor live metrics.

<img src="./assets/api_endpoints_tab.png" width="650" />

#### Dynamic REST APIs
* **Auto-Generated Query Parameters**: Use the `$parameter_name` notation to automatically capture dynamic query parameters from incoming requests (e.g. `?min_qty=10`).
* **Column Analysis Filter Creator**: Under the SQL editor, click **Analyze Columns for Auto-Params** to parse the schema of your target database table and auto-generate parameter logic based on dynamic ranges.
* **API Route Listing**: View all registered endpoints at `/api/list-endpoints` before catch-all wildcards hijack paths.

#### Security & Auth
* **JWT Authentication Toggle**: Enable or disable JWT verification on-the-fly. When enabled, requests require passing the Bearer token in the header (`Authorization: Bearer <token>`), verified using `HS256` symmetric signing with the global `STORAGE_SECRET`.

#### High-Performance Streaming
* **NDJSON Streaming**: Append `/stream` to any endpoint URL (e.g. `/api/orders/stream`) to stream massive datasets row-by-row in Newline Delimited JSON (`application/x-ndjson`) using HTTP chunked transfer encoding, maintaining a flat memory footprint.

#### Rate Limiting & Throttling
* **SlowAPI Integration**: Define specific request throttle limits per endpoint (e.g. `10/minute`, `100/hour`) to prevent server overload.

#### Telemetry Dashboard
* Monitor overall KPI metrics (Total requests, average response speeds, success rates) and audit detailed routes logs (Min/Max Latency, Success Ratios, and Trigger times) directly.

---

### 6. API Docs & Explorer

An embedded Swagger-style sandbox to document and run loops against dynamic APIs.

<img src="./assets/api_docs_tab.png" width="650" />

#### API Documentation Sandbox
* **Interactive Sandbox**: Auto-detects endpoint parameters and generates input forms inside the UI.
* **JWT Testing Sandbox**: Paste authorization tokens into the token field to test secured endpoints directly.
* **Loopback Executor**: Executes requests via internal HTTP loops, measuring request latency, status codes, and absolute URLs.
* **Formatted JSON View**: Renders dynamic query results as formatted syntax-highlighted JSON trees.

---

### 7. Scheduler

Automate your query reporting and data extraction pipelines.

<img src="./assets/scheduler_tab.png" width="650" />

#### Job Automation
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

### 8. Settings

A centralized control panel to manage global application parameters, safety overrides, telemetry settings, security keys, and external notebook credentials.

#### Configurations:
* **Rate Limiting & Safety Limits**: Configure default rate limits, maximum query row returns, and default pagination page sizes.
* **Security & JWT**: Customizes JWT signature secrets, issuer names, and audiences.
* **Telemetry Config**: Set retention duration for database telemetry metrics.
* **Jupyter Credentials**: Configure the Jupyter server URL and token.
