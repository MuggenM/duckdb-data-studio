# 📖 DuckDB Studio & API Explorer Manual

Welcome to the official, complete user manual for **DuckDB Studio & API Explorer**. This document covers every single screen, utility, and dynamic capability built into the application, complete with live pixel-perfect screenshots and high-fidelity animated walkthroughs captured directly from the local running server environment.

---

## 🚀 Live Studio Feature Traversal Walkthrough

Below is a live, automated walk-through demonstrating rapid traversal across all 7 workspace tabs inside **DuckDB Studio** in real-time:

![DuckDB Studio Live Walkthrough](./assets/feature_traversal.gif)

---

## 🧭 Navigating the Workspace Tabs

DuckDB Studio organizes its toolset into specialized workspace tabs:

| Workspace Tab | Icon | Purpose |
| :--- | :---: | :--- |
| [1. Explorer](#1-explorer) | `query_stats` | Database schema catalog, visual column structure query builder, and ad-hoc SQL console. |
| [2. JupyterLab](#2-jupyterlab) | `terminal` | Embedded Jupyter console terminal for writing integrated Python and pandas data science notebooks. |
| [3. Extensions](#3-extensions) | `extension` | Graphical DuckDB extension repository enabling one-click library installation and loading. |
| [4. Database Tools](#4-database-tools) | `construction` | Utility workshop containing database schema backups, schema restores, and test data seeding routines. |
| [5. API Endpoints](#5-api-endpoints) | `api` | Dynamic REST API creator, custom parameter binders, and live endpoints manager with optional JWT protection. |
| [6. API Docs & Explorer](#6-api-docs--explorer) | `menu_book` | OpenAPI interactive Swagger playground and Loopback testing sandbox supporting Bearer Auth. |
| [7. Scheduler](#7-scheduler) | `schedule` | Background automation query scheduler, Parquet/CSV exporter, and telemetry logs. |

---

## 🛠️ Comprehensive Screen-by-Screen Walkthroughs

### 1. Explorer

The core IDE of DuckDB Studio. It features a dual-column layout dividing the database metadata tree and active SQL editor workspace.

#### Active Explorer Screen:
![Explorer Workspace Screen](./assets/explorer_tab.png)

#### Live Explorer Feature Traversal Walkthrough:
![Explorer Workspace Walkthrough](./assets/explorer_traversal.gif)

#### Advanced Explorer Visual Assets:

##### Schema Catalog Browser
Easily browse through your databases, schemas, tables, views, and columns recursively:
![Schema Browser Walkthrough](./assets/schema_browser.gif)

##### Interactive Visual Query Builder
Build database projection queries dynamically on-the-fly without typing single line of SQL:
![Interactive Visual Query Builder](./assets/interactive_visual_query_builder.gif)

##### Save Query Presets
Save your frequently used SQL scripts directly to internal DuckDB storage for quick loading later:
![Save Query](./assets/save_query.gif)

##### Session History Trace
Trace and review recently run query metrics, timing, and latencies:
![Session History](./assets/session_history.gif)

##### Explain & Explain Analyze Plans
Visually analyze the execution query optimization plan (logical, physical, and execution profile statistics):
* **Explain Plan**:
![Explain Plan](./assets/explain.gif)
* **Explain Analyze Plan**:
![Explain Analyze](./assets/explain_analyse.gif)

#### Functions:
* **Ad-Hoc Editor**: Write standard or complex SQL statements with hot-reloaded autocomplete and execute them using `Ctrl + Enter`.
* **Database Catalog Tree**: Visually trace attached databases, schemas, tables, views, columns, and data types. Click any table node to automatically preview its data inside the query terminal!
* **Visual Query Builder**: Build projections dynamically by checking columns, configuring sort columns (`ASC`/`DESC`), and injecting filter clauses without typing raw SQL.
* **History Trace**: Access recently executed commands with runtime latencies to quickly restore a previous state.

---

### 2. JupyterLab

An integrated interactive data science console.

#### Active JupyterLab Walkthrough:
![JupyterLab Workspace Walkthrough](./assets/jupyterlab.gif)

#### Functions:
* **Python Notebooks**: Launch Jupyter notebook kernels to write advanced Python code alongside your DuckDB instance.
* **Pandas Scans**: Direct loopbacks inside notebooks to read from the DuckDB local catalogs:
  ```python
  import duckdb
  df = duckdb.query("SELECT * FROM product_inventory").df()
  ```

---

### 3. Extensions

A visual manager for DuckDB's unique runtime plugins.

#### Active Extensions Manager Walkthrough:
![Extensions Manager Walkthrough](./assets/extensions.gif)

#### Functions:
* **Extension Grid**: Visual status cards for extensions (`httpfs`, `postgres_scanner`, `sqlite_scanner`, `spatial`, `icu`, `json`).
* **Interactive Badges**: Beautiful color indicators showing whether an extension is **Installed** (Grey) or **Loaded** (Green).
* **One-Click Actions**: Click **Install** to pull the binary directly from DuckDB's servers, and **Load** to initialize it into the active execution connection.

---

### 4. Database Tools

A backup, restore, and scalability benchmarker for DuckDB local databases.

#### Active Database Tools Walkthrough:
![Database Seeder & Utilities Walkthrough](./assets/database_tools.gif)

#### Direct File Importing Walkthrough:
Easily import CSV/Parquet files directly into your active catalog using the file selector interface:
![Import File](./assets/import_file.gif)

#### Functions:
* **Backup Utilities**: Export the structural database catalog into clean SQL recovery files.
* **Restore Catalog**: Execute a selected backup SQL file to instantly restore schemas, tables, and views structure.
* **Scalability Seeding**: Move the record density slider from `100` to `100,000` rows and click **Trigger Seed Generation** to populate test tables, allowing you to validate performance metrics under realistic data scale.

---

### 5. API Endpoints

Turn any SQL select query into an active REST API microservice and monitor live metrics.

#### Active API Endpoints Workspace Walkthrough:
![API Endpoints & Telemetry Walkthrough](./assets/api_endpoints.gif)

#### Optional JWT Authentication:
Dynamic API endpoints can optionally require secure JWT (JSON Web Token) Authorization.
* **Toggle Security**: Enable or disable authentication on-the-fly using the **Require JWT Token Authorization** toggle switch during endpoint creation or editing.
* **Header Enforced**: When protected, the endpoint rejects unauthorized requests with a `401 Unauthorized` response. Accessing the endpoint requires passing the Authorization header:
  ```http
  Authorization: Bearer <your_jwt_token>
  ```
* **Signature Verification**: Signature verification is performed on the server using `HS256` symmetric signing with the global `STORAGE_SECRET`.

#### Functions:
* **Form Compiler**: Specify an endpoint slug (e.g. `recent-sales`) and write a query.
* **Auto-Generated Query Parameters**: Use the `$parameter_name` notation to automatically capture dynamic query parameters from incoming requests (e.g. `?min_qty=10`).
* **Column Analysis Filter Creator**: Under the editor, click **Analyze Columns for Auto-Params** to parse the schema of your target database table and auto-generate parameter logic based on dynamic ranges.
* **Safe Metered API Pagination**: Dynamic pagination enforcing a default limit of 100 records and a safety ceiling of 10,000, automatically wrapping unbounded queries.
* **Live Telemetry & Performance Dashboard**: Read overall KPI metrics (Total requests, average response speeds, success rates) and audit detailed routes logs (Min/Max Latency, Success Ratios, and Trigger times) directly.

---

### 6. API Docs & Explorer

An embedded Swagger-style sandbox to document and run loops against dynamic APIs.

#### Active Interactive Docs Sandbox:
![API Docs & Explorer Walkthrough](./assets/api_docs_explorer.gif)

#### Functions:
* **Interactive Sandbox**: Auto-detects endpoint parameters and generates input forms inside the UI.
* **JWT Testing Sandbox**: If an API requires JWT Authorization, a dedicated **Authorization Token** field is automatically rendered, allowing you to paste a Bearer token and test secured endpoints directly.
* **Loopback Executor**: Executes requests via internal HTTP loops, measuring request latency, status codes, and absolute URLs.
* **Formatted JSON View**: Renders dynamic query results as formatted syntax-highlighted JSON trees.

---

### 7. Scheduler

Automate your query reporting and data extraction pipelines.

#### Active Query Scheduler Walkthrough:
![Query Scheduler & Logs Walkthrough](./assets/scheduler.gif)

#### Functions:
* **Preset Loader**: Automatically pull final query SQL from Saved Queries into the form with one click.
* **Scheduler Worker**: Configure intervals (`Every Minute`, `Every 5 Minutes`, `Every 15 Minutes`, `Every Hour`, `Every 12 Hours`, `Daily`) which trigger background tasks to dump results into `/exports/`.
* **Export Configurations**: Select Parquet, CSV, or JSON formats. Type in a partition column (e.g. `category`) to partition the folder natively using DuckDB's fast `PARTITION_BY` system.
* **Automation Grid**: Toggle job statuses (Active/Inactive), manually run queries instantly with visual toast notifications, and trace rows/file sizes (e.g., `120.4 KB`) inside the execution logs history grid.
