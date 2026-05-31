# 📖 DuckDB Studio & API Explorer Manual

Welcome to the official, complete user manual for **DuckDB Studio & API Explorer**. This document covers every single screen, utility, and dynamic capability built into the application.

> [!NOTE]
> *Visual Reference Note*: The high-fidelity mockups in `./assets/` represent conceptual UI layout designs. The interactive Markdown maps inside this manual represent the exact fields, buttons, and visual flows implemented in the active Python codebase.

---

## 🧭 Navigating the Workspace Tabs

DuckDB Studio organizes its toolset into specialized workspace tabs:

| Workspace Tab | Icon | Purpose |
| :--- | :---: | :--- |
| [1. Explorer](#1-explorer) | `query_stats` | Database schema catalog, visual column structure query builder, and ad-hoc SQL console. |
| [2. JupyterLab](#2-jupyterlab) | `terminal` | Embedded Jupyter console terminal for writing integrated Python and pandas data science notebooks. |
| [3. Extensions](#3-extensions) | `extension` | Graphical DuckDB extension repository enabling one-click library installation and loading. |
| [4. Database Tools](#4-database-tools) | `construction` | Utility workshop containing database schema backups, schema restores, and test data seeding routines. |
| [5. API Endpoints](#5-api-endpoints) | `api` | Dynamic REST API creator, custom parameter binders, and live endpoints manager. |
| [6. API Docs & Explorer](#6-api-docs--explorer) | `menu_book` | OpenAPI interactive Swagger playground and Loopback testing sandbox. |
| [7. Scheduler](#7-scheduler) | `schedule` | Background automation query scheduler, Parquet/CSV exporter, and telemetry logs. |

---

## 🛠️ Comprehensive Screen-by-Screen Walkthroughs

### 1. Explorer

The core IDE of DuckDB Studio. It features a dual-column layout dividing the database metadata tree and active SQL editor workspace.

#### Interface Layout Map:
```text
+------------------------------------+---------------------------------------------------+
|  [DATABASE EXPLORER]               |  [SQL QUERY WORKSPACE]                            |
|                                    |  [ SQL Editor Area ]                              |
|  [Select Active DB Dropdown] [Rec] |  SELECT name, category, stock FROM...             |
|                                    |                                                   |
|  v main (schema)                   |  [ Run Query (Ctrl+Enter) ]         [ Save Snippet ] |
|    v tables                        |                                                   |
|      v product_inventory           |  [QUERY RESULTS GRID]                             |
|        - name (VARCHAR)            |  [Columns: name | category | stock]               |
|        - category (VARCHAR)        |  - Laptop   | Computing| 42                       |
|        - stock (INTEGER)           |  - Keyboard | Periphs  | 108                      |
|                                    |                                                   |
|  [QUERY HISTORY LOGS]              |  [VISUAL QUERY BUILDER]                           |
|  - SELECT * FROM product... (12ms) |  [Select Table Dropdown] [Sort By] [Filter Col]   |
+------------------------------------+---------------------------------------------------+
```

#### Functions:
* **Ad-Hoc Editor**: Write standard or complex SQL statements with hot-reloaded autocomplete and execute them using `Ctrl + Enter`.
* **Database Catalog Tree**: Visually trace attached databases, schemas, tables, views, columns, and data types. Click any table node to automatically preview its data inside the query terminal!
* **Visual Query Builder**: Build projections dynamically by checking columns, configuring sort columns (`ASC`/`DESC`), and injecting filter clauses without typing raw SQL.
* **History Trace**: Access recently executed commands with runtime latencies to quickly restore a previous state.

---

### 2. JupyterLab

An integrated interactive data science console:
* **Python Notebooks**: Launch Jupyter notebook kernels to write advanced Python code alongside your DuckDB instance.
* **Pandas Scans**: Direct loopbacks inside notebooks to read from the DuckDB local catalogs:
  ```python
  import duckdb
  df = duckdb.query("SELECT * FROM product_inventory").df()
  ```

---

### 3. Extensions

A visual manager for DuckDB's unique runtime plugins.

#### Functions:
* **Extension Grid**: Visual status cards for extensions (`httpfs`, `postgres_scanner`, `sqlite_scanner`, `spatial`, `icu`, `json`).
* **Interactive Badges**: Beautiful color indicators showing whether an extension is **Installed** (Grey) or **Loaded** (Green).
* **One-Click Actions**: Click **Install** to pull the binary directly from DuckDB's servers, and **Load** to initialize it into the active execution connection.

---

### 4. Database Tools

A backup, restore, and scalability benchmarker for DuckDB local databases.

#### Interface Layout Map:
```text
+----------------------------------------------------------------------------------------+
|  [DATABASE BACKUP & CATALOG RESTORE]      |  [HIGH-PERFORMANCE DATA SEEDING]           |
|  [Create Backup File]                     |  [Select Seed Target Table]                |
|  - product_catalog_backup.sql             |  [Seeding Record Density (Slider): 10,000] |
|                                           |                                            |
|  [ Restore Catalog ] [ Delete Backup ]    |  [ Trigger Seed Generation ]               |
+----------------------------------------------------------------------------------------+
```

#### Functions:
* **Backup Utilities**: Export the structural database catalog into clean SQL recovery files.
* **Restore Catalog**: Execute a selected backup SQL file to instantly restore schemas, tables, and views structure.
* **Scalability Seeding**: Move the record density slider from `100` to `100,000` rows and click **Trigger Seed Generation** to populate test tables, allowing you to validate performance metrics under realistic data scale.

---

### 5. API Endpoints

Turn any SQL select query into an active REST API microservice.

#### Interface Layout Map:
```text
+------------------------------------------+---------------------------------------------+
|  [CREATE API ENDPOINT FORM]              |  [EXPOSED HTTP ENDPOINTS LIST]              |
|  Path: [ recent-sales ]                  |  v GET /api/recent-sales                    |
|  Desc: [ Sales with qty >= $min_qty ]    |    Telemetry: [Calls: 42 | 12ms avg | 0% err] |
|                                          |    [Source Query SQL] (Expandable)          |
|  SQL Source:                             |    - [Test Endpoint] [Copy Path]            |
|  SELECT * FROM sales WHERE qty >= $qty;  |                                             |
|                                          |  v GET /api/top-products                    |
|  [Analyze Columns] [Create Endpoint]     |    Telemetry: [Calls: 108 | 5ms avg | 0% err] |
+------------------------------------------+---------------------------------------------+
|  [LIVE API TELEMETRY & LATENCY DASHBOARD]                                              |
|  [KPIs: Active Routes: 2 | Total Calls: 150 | Avg Latency: 8.5ms | Success: 100.0%]    |
|  [Table Logs: Path | Invocations | Avg Latency | Min/Max Bounds | Success Rate | Last] |
+----------------------------------------------------------------------------------------+
```

#### Functions:
* **Form Compiler**: Specify an endpoint slug (e.g. `recent-sales`) and write a query.
* **Auto-Generated Query Parameters**: Use the `$parameter_name` notation to automatically capture dynamic query parameters from incoming requests (e.g. `?min_qty=10`).
* **Column Analysis Filter Creator**: Under the editor, click **Analyze Columns for Auto-Params** to parse the schema of your target database table and auto-generate parameter logic based on dynamic ranges.
* **Safe Metered API Pagination**: Dynamic pagination enforcing a default limit of 100 records and a safety ceiling of 10,000, automatically wrapping unbounded queries.
* **Live Telemetry & Performance Dashboard**: Read overall KPI metrics (Total requests, average response speeds, success rates) and audit detailed routes logs (Min/Max Latency, Success Ratios, and Trigger times) directly. Click **Clear Telemetry Logs** to flush health metrics at any time.

---

### 6. API Docs & Explorer

An embedded Swagger-style sandbox to document and run loops against dynamic APIs.

#### Functions:
* **Interactive Sandbox**: Auto-detects endpoint parameters and generates input forms inside the UI.
* **Loopback Executor**: Executes requests via internal HTTP loops, measuring request latency, status codes, and absolute URLs.
* **Formatted JSON View**: Renders dynamic query results as formatted syntax-highlighted JSON trees.

---

### 7. Scheduler

Automate your query reporting and data extraction pipelines.

#### Interface Layout Map:
```text
+------------------------------------------+---------------------------------------------+
|  [SCHEDULE NEW EXPORT JOB]               |  [ACTIVE SCHEDULED JOBS GRID]               |
|  [Preset: Load from Saved Query]         |  v Active: Hourly Sales Report              |
|  Job Name: [ Hourly Sales Report ]       |    [Schedule: Every Hour] [Format: Parquet] |
|  Interval: [ Every Hour ]                |    - [Pause] [Trigger Now] [Delete]         |
|  Format:   [ Parquet ]                   |    Next Target: 2026-05-31 13:00:00         |
|  Partition Column: [ category ]          |                                             |
|  Filename: [ hourly_sales ]              |  v Active: Daily Summary                    |
|  [ Create Export Job ]                   |    Next Target: 2026-06-01 00:00:00         |
+------------------------------------------+---------------------------------------------+
|  [JOB EXECUTION LOGS]                                                                  |
|  - Hourly Sales Report | 2026-05-31 12:00:00 | 124.5ms | 4,200 rows | 120.4 KB | Success|
+----------------------------------------------------------------------------------------+
```

#### Functions:
* **Preset Loader**: Automatically pull final query SQL from Saved Queries into the form with one click.
* **Scheduler Worker**: Configure intervals (`Every Minute`, `Every 5 Minutes`, `Every 15 Minutes`, `Every Hour`, `Every 12 Hours`, `Daily`) which trigger background tasks to dump results into `/exports/`.
* **Export Configurations**: Select Parquet, CSV, or JSON formats. Type in a partition column (e.g. `category`) to partition the folder natively using DuckDB's fast `PARTITION_BY` system.
* **Automation Grid**: Toggle job statuses (Active/Inactive), manually run queries instantly with visual toast notifications, and trace rows/file sizes (e.g., `120.4 KB`) inside the execution logs history grid.
