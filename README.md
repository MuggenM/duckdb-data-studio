# 🦆 DuckDB Studio & API Explorer

DuckDB Studio is an ultra-premium, high-performance, and feature-rich visual management platform and microservice generator for **DuckDB**. It bridges the gap between ad-hoc local SQL data exploration and production-ready REST API deployment, enabling developers, data scientists, and analysts to automate data flows, build instant backend microservices, and manage local data files with breathtaking visual aesthetics.

---

## 🚀 Key Architectural Pillars

```mermaid
graph TD
    A[DuckDB Local Database] --> B[DuckDB Studio Core]
    B --> C[Visual SQL Editor & Explorer]
    B --> D[JupyterLab Embedded Terminal]
    B --> E[Dynamic FastAPI Creator]
    B --> F[Background Automation Scheduler]
    
    E --> G[OpenAPI Swagger playground]
    E --> H[Live Telemetry Dashboard]
    
    F --> I[Parquet/CSV/JSON Exports]
```

### 1. Visual SQL Editor & Interactive Catalog Explorer 📊
* **High-Performance Query Execution**: Execute complex analytical SQL queries instantly against local or attached databases.
* **Schema Catalog Tree**: Visually browse schemas, tables, views, columns, and datatype catalogs via an interactive sidebar list.
* **Execution History & Snippets**: Retain a robust history of executed statements and save frequently used queries into a custom workspace library.

### 2. Embedded JupyterLab Workspace 💻
* Embedded full-featured **JupyterLab** workspace terminal enabling seamless integration with notebooks for advanced Python, pandas, and machine learning pipelines side-by-side with your database operations.

### 3. Visual Extensions Manager 🔌
* Browse the rich DuckDB extension catalog (like `httpfs` for S3 query structures, `postgres_scanner`/`sqlite_scanner` for direct database scanners, `spatial`, and `icu`).
* Visually click to **Install** and **Load** extensions in real-time without writing manual SQL commands.

### 4. Database Seeding & Recovery Utilities 🛠️
* **Catalog Backups & Restore**: Create instant catalog structure backups and restore them in one click.
* **Custom Database Seeding**: Populate benchmark tables with custom-density test datasets in seconds to validate query architectures under scale.

### 5. FastAPI Endpoint Creator (Microservices Generator) ⚡
* **Instant Expose**: Compile any raw SQL query on-the-fly and host it as a dynamic REST API endpoint (e.g. `/api/recent-sales`).
* **Auto-Generated Query Parameters**: Use the `$parameter_name` notation to automatically capture dynamic query parameters from incoming requests (e.g. `?min_qty=10`). Includes custom analysis tools to parse table schemas and auto-generate parameter options.
* **Safe Metered API Pagination**: Avoid container crashes from massive responses. Enforces a default dynamic pagination limit of 100 records and a safety max limit ceiling of 10,000, automatically wrapping unbounded queries.

### 6. Interactive OpenAPI Docs & Explorer 📖
* Embedded visual sandbox playground designed in a modern Swagger UI layout.
* Inspect active dynamic endpoint schemas, input query parameters, test executions with real-time browser loopbacks, and view beautifully formatted JSON response blocks with live latency measurements.

### 7. Live API Metrics & Latency Dashboard 📈
* **KPI Telemetry Cards**: Monitor global health statistics, including Total API Requests, Average Latency (ms), and Global Success Rate percentage.
* **Granular Route Analytics**: Review a performance analytics table sorting endpoint routes by invocations, average response times, min/max bounds, success ratios, and last triggered timestamps.
* **Reset Operations**: Instantly truncate metrics log history to start fresh profiling sessions.

### 8. Background Query Scheduler & Exporter ⏰
* **Automated Data Pipelines**: Build period-based schedules (`Every Minute` to `Daily`) to automatically execute saved queries in a background thread.
* **High-Performance Exports**: Dump scheduled query results directly to the project's local `/exports/` directory as **Parquet**, **CSV**, or **JSON** files.
* **Native Partitioning**: Specify optional columns to partition the exported directories natively using DuckDB's ultra-fast `PARTITION_BY` option.
* **Automation Grid**: Manage automation profiles with interactive pause/resume toggles, manual trigger buttons, and visual execution log tables.

---

## 🛠️ Technology Stack & Dependencies

* **Frontend Framework**: [NiceGUI](https://nicegui.io/) (High-performance web interface builder based on TailwindCSS and Quasar)
* **Backend Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Asynchronous high-performance REST API routing)
* **Database Engine**: [DuckDB](https://duckdb.org/) (In-process analytical database engine)
* **Visual Theme**: Curated Slate & Indigo dark/light color scheme, standard responsive grid layouts, custom card shadows, and visual feedback toasts.

---

## 📦 Deployment & Container Settings

The application is fully containerized and runs with hot-reloading configurations under Docker.

### Running with Docker Compose
To build and start the environment:
```bash
docker compose up -d --build
```

### Accessing the Web Interfaces
* **DuckDB Studio Web Dashboard**: [http://localhost:8086](http://localhost:8086)
* **Dynamic REST API Base URL**: `http://localhost:8086/api/<endpoint-slug>`
* **JupyterLab Terminal**: Access via the embedded tab or container ports.

---

## 📂 Project Structure

* [main.py](file:///home/martin/volumes/duckdb-studio/main.py): Primary codebase containing the web application core, NiceGUI layouts, background scheduler daemon, and FastAPI routing handlers.
* [exports/](file:///home/martin/volumes/duckdb-studio/exports): Target directory for automated background query files.
* [databases/](file:///home/martin/volumes/duckdb-studio/databases): Mounted database folder containing the DuckDB databases.
