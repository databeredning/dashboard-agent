# Dashboard Agent

A local AI agent for inspecting InfluxDB telemetry and creating or modifying Grafana dashboards using the Model Context Protocol (MCP).

The project uses a locally hosted LLM through `llama.cpp`, an MCP server written in Python, InfluxDB 2.x, and Grafana.

## Architecture

```text
                         Local machine / server

┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  llama.cpp                                                   │
│  llama-server                                                │
│  Qwen3                                                       │
│  OpenAI-compatible API :8080                                 │
│            ▲                                                 │
│            │                                                 │
│            │ /v1/chat/completions                            │
│            │                                                 │
│        agent.py                                              │
│            │                                                 │
│            │ MCP over STDIO                                  │
│            ▼                                                 │
│        server.py                                             │
│            │                                                 │
└────────────┼─────────────────────────────────────────────────┘
             │
             ├──────────────► InfluxDB 2.x
             │                 :8086
             │
             └──────────────► Grafana
                               :3000
```

`agent.py` is the LLM agent loop.

`server.py` is the MCP server exposing controlled InfluxDB and Grafana operations to the agent.

`llama-server` provides the local OpenAI-compatible LLM API.

The LLM does not receive InfluxDB or Grafana credentials. Authentication and API access remain inside the MCP server.

---

## Features

The agent can:

- Discover InfluxDB measurements
- Discover fields belonging to a measurement
- Discover tag keys
- Discover tag values
- Inspect recent measurement data
- Discover Grafana datasources
- Retrieve existing Grafana dashboards
- Create Grafana dashboards
- Update existing dashboards
- Add InfluxDB-backed Grafana time-series panels
- Automatically back up dashboards before updates
- Protect dashboard updates using Grafana version checking

Example interaction:

```text
> What GPU measurements and fields are available? Use the tools to inspect InfluxDB

[tool] influx_list_measurements({})
[tool] influx_list_fields({'measurement': 'nvidia_smi'})

The available GPU measurement is nvidia_smi...
```

The goal is to support requests such as:

```text
Add GPU temperature and GPU utilization panels to my Grafana dashboard.
```

The agent can inspect the actual InfluxDB schema rather than relying on guessed measurement or field names.

---

# Requirements

## Operating system

Tested on Ubuntu with Python 3.12.

A modern Debian/Ubuntu installation should work with minor adjustments.

Required system packages:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl jq
```

## Python

Python 3.12 or newer is recommended.

Check:

```bash
python3 --version
```

## InfluxDB

InfluxDB 2.x is required.

The MCP server uses the InfluxDB v2 HTTP API and Flux queries.

You need:

- InfluxDB URL
- Organization
- Bucket
- API token with appropriate read permissions

Example:

```text
INFLUX_URL=http://192.168.1.194:8086
INFLUX_ORG=home
INFLUX_BUCKET=homeassistant
```

Do not store a real token in the repository.

## Grafana

Grafana must be reachable from the machine running `server.py`.

You need:

- Grafana URL
- Grafana service account token/API token

The token requires sufficient permissions for the operations you intend the agent to perform.

For read-only operation, dashboard and datasource read permissions are sufficient.

For dashboard creation/modification, the Grafana identity must also have permission to create and edit dashboards.

Example:

```text
GRAFANA_URL=http://192.168.1.194:3000
```

## llama.cpp

A working `llama.cpp` installation with `llama-server` is required for the local LLM agent.

Repository:

```text
https://github.com/ggml-org/llama.cpp
```

Build llama.cpp according to the upstream instructions for your hardware.

GPU acceleration is strongly recommended.

---

# Installation

Clone the repository:

```bash
git clone <repository-url>
cd dashboard-agent
```

Create a Python virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

Verify MCP:

```bash
python -c 'import importlib.metadata; print(importlib.metadata.version("mcp"))'
```

Verify the source files compile:

```bash
python -m py_compile server.py agent.py
```

---

# Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Edit it:

```bash
nano .env
```

Configuration format:

```dotenv
INFLUX_URL=http://localhost:8086
INFLUX_ORG=home
INFLUX_BUCKET=my_bucket
INFLUX_TOKEN=replace-me

GRAFANA_URL=http://localhost:3000
GRAFANA_TOKEN=replace-me
```

Never commit `.env`.

The included `.gitignore` excludes:

```text
.env
.venv/
__pycache__/
*.pyc
backups/
node_modules/
```

---

# Testing connectivity

## InfluxDB

Check InfluxDB health:

```bash
curl -sS "$INFLUX_URL/health" | jq
```

A healthy InfluxDB server should report:

```json
{
  "status": "pass"
}
```

## Grafana

Check Grafana:

```bash
curl -sS "$GRAFANA_URL/api/health" | jq
```

Test authentication:

```bash
curl -sS -o /dev/null -w 'Grafana API: HTTP %{http_code}\n' \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "$GRAFANA_URL/api/user"
```

Expected:

```text
Grafana API: HTTP 200
```

---

# MCP server

The MCP server is implemented in:

```text
server.py
```

It communicates with InfluxDB and Grafana and exposes controlled tools to MCP clients.

## Available InfluxDB tools

### `influx_list_measurements`

Lists measurements in the configured InfluxDB bucket.

### `influx_list_fields`

Lists field names belonging to a measurement.

Example conceptually:

```text
influx_list_fields(
    measurement="nvidia_smi"
)
```

### `influx_list_tag_keys`

Lists tag keys associated with a measurement.

### `influx_list_tag_values`

Lists known values for a specific tag.

Example:

```text
measurement = nvidia_smi
tag = host
```

### `influx_inspect_measurement`

Returns a small number of recent raw samples.

This tool is intended for inspecting actual values, not schema discovery.

Prefer:

```text
influx_list_fields
influx_list_tag_keys
influx_list_tag_values
```

when discovering the schema.

---

# Grafana MCP tools

### `grafana_list_datasources`

Lists Grafana datasources and their UIDs.

This allows the agent to discover the InfluxDB datasource rather than requiring the datasource UID to be supplied manually.

### `grafana_get_dashboard`

Retrieves an existing Grafana dashboard by UID.

### `grafana_create_dashboard`

Creates a new dashboard.

### `grafana_update_dashboard`

Updates an existing dashboard.

Updates use optimistic version checking.

The caller supplies the dashboard version it previously read. If the version in Grafana has changed, the operation fails instead of silently overwriting somebody else's changes.

A JSON backup is also created before an update.

### `grafana_add_timeseries_panel`

Adds an InfluxDB-backed time-series panel to an existing dashboard.

The tool handles:

- Dashboard retrieval
- Version validation
- Backup
- Panel ID allocation
- Panel placement
- Flux query generation
- Grafana dashboard update

This higher-level interface is preferred over having the LLM construct an entire Grafana dashboard document.

---

# Dashboard backups

Before modifying an existing dashboard, the MCP server creates a JSON backup.

Default directory:

```text
/opt/dashboard-agent/backups
```

The backup directory is excluded from Git.

Example filename:

```text
a7pv6t-v1-20260930T180000Z.json
```

A different location can be configured with:

```dotenv
GRAFANA_BACKUP_DIR=/path/to/backups
```

---

# Testing MCP with MCP Inspector

MCP Inspector is useful during development but is not required when running the actual agent.

Run:

```bash
mcp dev server.py
```

The Inspector requires Node.js/npm.

Recent versions of MCP Inspector require a recent Node.js version. Node 22+ is recommended.

When running on a remote server over SSH, create an SSH tunnel from your workstation:

```bash
ssh -L 6274:127.0.0.1:6274 user@server
```

Then open the Inspector URL printed by `mcp dev` in the local browser.

The Inspector launches `server.py` as an MCP STDIO subprocess when the connection is established.

For normal agent operation, MCP Inspector is not required.

---

# Running llama.cpp

The current tested model is Qwen3 using a GGUF quantization.

Example:

```bash
./build/bin/llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M -ngl 20 -c 8192 --alias qwen3-8b --reasoning off --jinja --host 0.0.0.0 --port 8080
```

Important options:

```text
-hf
```

Loads the model from Hugging Face.

```text
-ngl
```

Controls GPU layer offloading. The appropriate value depends on GPU VRAM.

```text
-c 8192
```

Sets the model context size.

```text
--alias qwen3-8b
```

Defines the model name exposed through the API.

```text
--jinja
```

Enables Jinja chat templates required for structured tool/function calling.

```text
--reasoning off
```

Disables Qwen3 reasoning output.

For this agent workload, disabling reasoning can significantly reduce latency because many operations involve straightforward tool selection rather than complex reasoning.

Check the API:

```bash
curl -s http://127.0.0.1:8080/v1/models | jq
```

---

# Agent

Start the local LLM server first.

Then activate the project environment:

```bash
cd /opt/dashboard-agent
source .venv/bin/activate
```

Run:

```bash
python agent.py
```

Expected startup:

```text
Dashboard Agent
Model: qwen3-8b
MCP tools: 10
Type 'quit' to exit.

>
```

Example:

```text
> What GPU measurements and fields are available? Use the tools to inspect InfluxDB
```

The agent may perform:

```text
[tool] influx_list_measurements({})
[tool] influx_list_fields({'measurement': 'nvidia_smi'})
```

and then return the discovered fields.

Exit with:

```text
quit
```

---

# How the agent works

The execution flow is:

```text
User
 │
 ▼
agent.py
 │
 ├──── OpenAI-compatible HTTP API ────► llama-server
 │                                         │
 │                                         ▼
 │                                      Qwen3
 │                                         │
 │                         structured tool request
 │◄────────────────────────────────────────┘
 │
 │ MCP
 ▼
server.py
 │
 ├────────► InfluxDB
 │
 └────────► Grafana
```

The MCP tool result is returned to the model, which can then choose another tool or provide the final response.

`agent.py` starts `server.py` itself using MCP STDIO. You do not need to run `server.py` or `mcp dev` separately during normal agent operation.

---

# Security

## Never commit credentials

The following file must remain private:

```text
.env
```

Never place real InfluxDB or Grafana tokens in:

- `README.md`
- `.env.example`
- source code
- Git commits
- issue reports

## Use least-privilege tokens

Use dedicated credentials for the dashboard agent.

Do not reuse InfluxDB administrative tokens when a read-only token is sufficient.

For Grafana, grant only the dashboard permissions the agent requires.

## MCP boundary

The LLM does not need direct credentials for Grafana or InfluxDB.

Credentials remain in `server.py`'s environment.

The model only receives access to the explicitly exposed MCP tools.

This is intentional: prefer narrow operations such as:

```text
grafana_add_timeseries_panel
```

over giving the model arbitrary authenticated HTTP access to Grafana.

## Dashboard update safety

Dashboard writes use version checking to reduce the chance of overwriting concurrent changes.

Existing dashboards are backed up before updates.

For initial development, use a dedicated test dashboard before allowing the agent to modify important production dashboards.

---

# Performance considerations

Local tool-calling models can generate substantial amounts of context.

Avoid returning unnecessarily large tool responses.

Schema discovery should use the dedicated tools rather than raw measurement inspection.

For example, prefer:

```text
influx_list_fields("nvidia_smi")
```

instead of retrieving hundreds of raw telemetry samples.

The current development environment uses an 8192-token context window.

Small local models may be more effective when MCP tools are:

- Narrow
- High-level
- Well-described
- Predictable
- Limited in output size

The project intentionally moves Grafana-specific implementation details into Python rather than requiring the LLM to generate large dashboard JSON documents.

---

# Current status

The following functionality has been tested end-to-end:

- InfluxDB connectivity
- Grafana connectivity
- MCP server startup
- MCP Inspector connectivity
- InfluxDB measurement discovery
- InfluxDB field discovery
- InfluxDB tag discovery
- Grafana datasource discovery
- Grafana dashboard retrieval
- Grafana dashboard creation
- Grafana dashboard modification
- Automatic dashboard backup
- Version-protected dashboard updates
- Creation of a working InfluxDB-backed Grafana time-series panel
- Qwen3 structured function calling through llama.cpp
- Autonomous MCP tool selection from the local LLM agent

---

# Development status

This project is currently experimental.

Recommended next improvements include:

- Hard limits on MCP tool-result size
- More compact measurement search tools
- Grafana dashboard search/list tools
- Additional panel types
- Better datasource selection
- Configurable LLM model/base URL
- Improved error handling
- Automated dependency installation
- Tests
- systemd service definitions
- Optional web/chat interface

---

# License

No license has been selected yet.
