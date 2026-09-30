import os
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp.server import MCPServer

load_dotenv()

INFLUX_URL = os.environ["INFLUX_URL"].rstrip("/")
INFLUX_ORG = os.environ["INFLUX_ORG"]
INFLUX_BUCKET = os.environ["INFLUX_BUCKET"]
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]

GRAFANA_URL = os.environ["GRAFANA_URL"].rstrip("/")
GRAFANA_TOKEN = os.environ["GRAFANA_TOKEN"]

BACKUP_DIR = Path(
    os.environ.get(
        "GRAFANA_BACKUP_DIR",
        "/opt/dashboard-agent/backups"
    )
)

mcp = MCPServer("dashboard-agent")


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def influx_query(flux: str) -> str:
    response = httpx.post(
        f"{INFLUX_URL}/api/v2/query",
        params={"org": INFLUX_ORG},
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Accept": "application/csv",
            "Content-Type": "application/vnd.flux",
        },
        content=flux,
        timeout=30.0,
    )
    response.raise_for_status()
    return response.text


def grafana_headers() -> dict:
    return {
        "Authorization": f"Bearer {GRAFANA_TOKEN}",
        "Content-Type": "application/json",
    }


def validate_uid(uid: str) -> None:
    if not uid:
        raise ValueError("Dashboard UID must not be empty")

    if "/" in uid or "\\" in uid or ".." in uid:
        raise ValueError("Invalid dashboard UID")


def get_grafana_dashboard(uid: str) -> dict:
    validate_uid(uid)

    response = httpx.get(
        f"{GRAFANA_URL}/api/dashboards/uid/{uid}",
        headers=grafana_headers(),
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def backup_dashboard(uid: str, data: dict) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version = data.get("dashboard", {}).get("version", "unknown")

    filename = BACKUP_DIR / (
        f"{uid}-v{version}-{timestamp}.json"
    )

    filename.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )

    return str(filename)


# ---------------------------------------------------------
# InfluxDB tools
# ---------------------------------------------------------

@mcp.tool()
def influx_list_measurements() -> str:
    """List measurements available in the configured InfluxDB bucket."""

    flux = f'''
import "influxdata/influxdb/schema"

schema.measurements(bucket: "{INFLUX_BUCKET}")
'''

    return influx_query(flux)


@mcp.tool()
def influx_inspect_measurement(measurement: str) -> str:
    """
    Return raw recent sample records from an InfluxDB measurement.

    WARNING: This tool can return a large amount of data.

    Do NOT use this tool to discover field names or tag names.
    Use influx_list_fields and influx_list_tag_keys for schema discovery.
    Only use this tool when actual recent sample values are specifically needed.
    """

    safe_measurement = (
        measurement
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )

    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "{safe_measurement}")
  |> limit(n: 5)
'''

    return influx_query(flux)

@mcp.tool()
def influx_list_fields(measurement: str) -> str:
    """List field names available for an InfluxDB measurement."""

    safe_measurement = (
        measurement
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )

    flux = f'''
import "influxdata/influxdb/schema"

schema.measurementFieldKeys(
    bucket: "{INFLUX_BUCKET}",
    measurement: "{safe_measurement}",
)
'''

    return influx_query(flux)


@mcp.tool()
def influx_list_tag_keys(measurement: str) -> str:
    """List tag keys available for an InfluxDB measurement."""

    safe_measurement = (
        measurement
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )

    flux = f'''
import "influxdata/influxdb/schema"

schema.measurementTagKeys(
    bucket: "{INFLUX_BUCKET}",
    measurement: "{safe_measurement}",
)
'''

    return influx_query(flux)


@mcp.tool()
def influx_list_tag_values(
    measurement: str,
    tag: str,
) -> str:
    """List values for a tag within an InfluxDB measurement."""

    safe_measurement = (
        measurement
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )

    safe_tag = (
        tag
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )

    flux = f'''
import "influxdata/influxdb/schema"

schema.measurementTagValues(
    bucket: "{INFLUX_BUCKET}",
    measurement: "{safe_measurement}",
    tag: "{safe_tag}",
)
'''

    return influx_query(flux)

# ---------------------------------------------------------
# Grafana read tools
# ---------------------------------------------------------

@mcp.tool()
def grafana_get_dashboard(uid: str) -> str:
    """Retrieve a Grafana dashboard by UID."""

    data = get_grafana_dashboard(uid)
    return json.dumps(data, indent=2)


# ---------------------------------------------------------
# Grafana write tools
# ---------------------------------------------------------

@mcp.tool()
def grafana_create_dashboard(
    title: str,
    panels: list[dict],
) -> str:
    """
    Create a new Grafana dashboard.

    This does not overwrite an existing dashboard.
    """

    if not title.strip():
        raise ValueError("Dashboard title must not be empty")

    dashboard = {
        "id": None,
        "uid": None,
        "title": title.strip(),
        "tags": ["dashboard-agent"],
        "timezone": "browser",
        "schemaVersion": 42,
        "version": 0,
        "refresh": "",
        "panels": panels,
    }

    payload = {
        "dashboard": dashboard,
        "overwrite": False,
        "message": "Created by dashboard-agent",
    }

    response = httpx.post(
        f"{GRAFANA_URL}/api/dashboards/db",
        headers=grafana_headers(),
        json=payload,
        timeout=30.0,
    )

    response.raise_for_status()
    return json.dumps(response.json(), indent=2)


@mcp.tool()
def grafana_update_dashboard(
    uid: str,
    dashboard: dict,
    expected_version: int,
    message: str = "Updated by dashboard-agent",
) -> str:
    """
    Update an existing Grafana dashboard.

    Safety behavior:
    - Fetches the current dashboard first.
    - Refuses the update if expected_version does not match Grafana.
    - Saves a local JSON backup before modifying Grafana.
    - Preserves the requested dashboard UID.
    """

    validate_uid(uid)

    current = get_grafana_dashboard(uid)
    current_dashboard = current["dashboard"]
    current_version = current_dashboard.get("version")

    if current_version != expected_version:
        raise ValueError(
            f"Version mismatch: expected {expected_version}, "
            f"but Grafana currently has version {current_version}. "
            "Fetch the dashboard again before updating it."
        )

    if not isinstance(dashboard, dict):
        raise ValueError("dashboard must be a JSON object")

    incoming_uid = dashboard.get("uid")

    if incoming_uid not in (None, uid):
        raise ValueError(
            f"Dashboard UID mismatch: got {incoming_uid!r}, "
            f"expected {uid!r}"
        )

    backup_file = backup_dashboard(uid, current)

    # Make a copy so we do not mutate the MCP argument object.
    updated_dashboard = dict(dashboard)

    # Preserve Grafana identity/version information.
    updated_dashboard["uid"] = uid
    updated_dashboard["id"] = current_dashboard.get("id")
    updated_dashboard["version"] = current_version

    payload = {
        "dashboard": updated_dashboard,
        "overwrite": True,
        "message": message,
    }

    response = httpx.post(
        f"{GRAFANA_URL}/api/dashboards/db",
        headers=grafana_headers(),
        json=payload,
        timeout=30.0,
    )

    response.raise_for_status()

    result = response.json()

    return json.dumps(
        {
            "grafana": result,
            "backup": backup_file,
            "previous_version": current_version,
        },
        indent=2,
    )

@mcp.tool()
def grafana_add_timeseries_panel(
    uid: str,
    expected_version: int,
    title: str,
    measurement: str,
    field: str,
    host: str = "",
    unit: str = "short",
) -> str:
    """
    Add an InfluxDB Flux time-series panel to an existing Grafana dashboard.

    The tool:
    - checks the dashboard version
    - backs up the current dashboard
    - generates the Flux query
    - assigns a new panel ID
    - places the panel below existing panels
    - saves the dashboard

    Use an empty host string to omit host filtering.
    """

    validate_uid(uid)

    current = get_grafana_dashboard(uid)
    dashboard = current["dashboard"]
    current_version = dashboard.get("version")

    if current_version != expected_version:
        raise ValueError(
            f"Version mismatch: expected {expected_version}, "
            f"but Grafana currently has version {current_version}. "
            "Fetch the dashboard again before modifying it."
        )

    if not title.strip():
        raise ValueError("Panel title must not be empty")

    if not measurement.strip():
        raise ValueError("Measurement must not be empty")

    if not field.strip():
        raise ValueError("Field must not be empty")

    def flux_escape(value: str) -> str:
        return (
            value
            .replace("\\", "\\\\")
            .replace('"', '\\"')
        )

    safe_measurement = flux_escape(measurement)
    safe_field = flux_escape(field)
    safe_host = flux_escape(host)

    flux_lines = [
        f'from(bucket: "{INFLUX_BUCKET}")',
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)',
        f'  |> filter(fn: (r) => r._measurement == "{safe_measurement}")',
    ]

    if host:
        flux_lines.append(
            f'  |> filter(fn: (r) => r.host == "{safe_host}")'
        )

    flux_lines.extend([
        f'  |> filter(fn: (r) => r._field == "{safe_field}")',
        '  |> group(columns: ["host"])',
    ])

    flux = "\n".join(flux_lines)

    panels = dashboard.setdefault("panels", [])

    # Find an unused panel ID.
    existing_ids = [
        panel.get("id", 0)
        for panel in panels
        if isinstance(panel.get("id"), int)
    ]

    panel_id = max(existing_ids, default=0) + 1

    # Put the new panel below everything currently on the dashboard.
    next_y = max(
        (
            panel.get("gridPos", {}).get("y", 0)
            + panel.get("gridPos", {}).get("h", 0)
            for panel in panels
        ),
        default=0,
    )

    # Reuse the InfluxDB datasource from an existing panel when possible.
    datasource = None

    for panel in panels:
        candidate = panel.get("datasource")

        if (
            isinstance(candidate, dict)
            and candidate.get("type") == "influxdb"
        ):
            datasource = candidate
            break

    # Your existing GPU dashboard uses this datasource UID.
    # Fall back to it if the target dashboard is empty.
    if datasource is None:
        datasource = {
            "type": "influxdb",
            "uid": "efuuz03iqr11cc",
        }

    new_panel = {
        "id": panel_id,
        "title": title.strip(),
        "type": "timeseries",
        "datasource": datasource,
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": next_y,
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {
                    "mode": "palette-classic"
                },
                "custom": {
                    "drawStyle": "line",
                    "lineInterpolation": "linear",
                    "lineWidth": 1,
                    "fillOpacity": 0,
                    "showPoints": "auto",
                    "spanNulls": False,
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": False,
            },
            "tooltip": {
                "mode": "single",
                "sort": "none",
            },
        },
        "targets": [
            {
                "datasource": datasource,
                "query": flux,
                "refId": "A",
            }
        ],
    }

    backup_file = backup_dashboard(uid, current)

    panels.append(new_panel)

    payload = {
        "dashboard": dashboard,
        "overwrite": True,
        "message": f"Added panel: {title.strip()}",
    }

    response = httpx.post(
        f"{GRAFANA_URL}/api/dashboards/db",
        headers=grafana_headers(),
        json=payload,
        timeout=30.0,
    )

    response.raise_for_status()
    result = response.json()

    return json.dumps(
        {
            "grafana": result,
            "backup": backup_file,
            "previous_version": current_version,
            "panel": {
                "id": panel_id,
                "title": title.strip(),
                "measurement": measurement,
                "field": field,
                "host": host or None,
                "unit": unit,
            },
        },
        indent=2,
    )

@mcp.tool()
def grafana_list_datasources() -> str:
    """
    List Grafana datasources available to the service account.

    Useful for discovering datasource names, types, UIDs and URLs
    before creating dashboard panels.
    """

    response = httpx.get(
        f"{GRAFANA_URL}/api/datasources",
        headers=grafana_headers(),
        timeout=30.0,
    )

    response.raise_for_status()

    datasources = response.json()

    result = [
        {
            "id": ds.get("id"),
            "uid": ds.get("uid"),
            "name": ds.get("name"),
            "type": ds.get("type"),
            "url": ds.get("url"),
            "isDefault": ds.get("isDefault"),
        }
        for ds in datasources
    ]

    return json.dumps(result, indent=2)

if __name__ == "__main__":
    mcp.run()
