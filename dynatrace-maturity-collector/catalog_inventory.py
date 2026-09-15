"""Application, dashboard, and metric catalogs for the host inventory."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from collector import ApiClient


@dataclass
class CatalogRow:
    tool: str
    source_id: str
    name: str
    host: str = ""
    scope: str = ""
    status: str = ""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable {name}")
    return value


def get_json(url: str, headers: dict[str, str]) -> Any:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def dynatrace_catalog(kind: str, hosts: list[Any]) -> list[CatalogRow]:
    client = ApiClient(required("DT_ENV_URL"), required("DT_API_TOKEN"))
    if kind == "applications":
        rows = []
        host_names = {host.source_id: host.name for host in hosts}
        for entity_type in ("APPLICATION", "SERVICE", "CLOUD_APPLICATION"):
            entities, _ = client.paged("/api/v2/entities", {"pageSize": 500, "entitySelector": f'type("{entity_type}")', "fields": "+fromRelationships,+toRelationships"}, "entities")
            for entity in entities:
                relations = (entity.get("fromRelationships") or {}) | (entity.get("toRelationships") or {})
                linked = {str(item.get("id") if isinstance(item, dict) else item) for relation, items in relations.items() if "HOST" in relation.upper() for item in items}
                host = "; ".join(sorted(host_names[host_id] for host_id in linked if host_id in host_names))
                rows.append(CatalogRow("dynatrace", str(entity.get("entityId", "")), str(entity.get("displayName") or entity.get("entityId")), host, entity_type, "monitored_entity"))
        return rows
    if kind == "dashboards":
        payload = client.get("/api/config/v1/dashboards")
        return [CatalogRow("dynatrace", str(item.get("id", "")), str(item.get("name", "")), scope="classic", status="listed") for item in payload.get("dashboards", [])]
    metrics, _ = client.paged("/api/v2/metrics", {"pageSize": 500, "fields": "displayName,unit,lastWritten"}, "metrics")
    cutoff = int((time.time() - 86400) * 1000)
    return [CatalogRow("dynatrace", str(item.get("metricId", "")), str(item.get("displayName") or item.get("metricId")), scope=str(item.get("unit", "")), status="active_24h" if (item.get("lastWritten") or 0) >= cutoff else "not_active_24h") for item in metrics]


def dd_client() -> tuple[str, dict[str, str]]:
    site = os.environ.get("DD_SITE", "datadoghq.com")
    base = site if "://" in site else "https://api." + site
    return base.rstrip("/"), {"DD-API-KEY": required("DD_API_KEY"), "DD-APPLICATION-KEY": required("DD_APP_KEY"), "Accept": "application/json"}


def datadog_catalog(kind: str, hosts: list[Any]) -> list[CatalogRow]:
    base, headers = dd_client()
    if kind == "applications":
        query = urllib.parse.urlencode({"filter[env]": "*"})
        payload = get_json(base + "/api/v2/apm/services?" + query, headers)
        attributes = (payload.get("data") or {}).get("attributes") or {}
        services = attributes.get("services") or []
        return [CatalogRow("datadog", str(service), str(service), status="APM_service") for service in services]
    if kind == "dashboards":
        payload = get_json(base + "/api/v1/dashboard", headers)
        return [CatalogRow("datadog", str(item.get("id", "")), str(item.get("title", "")), status="listed") for item in payload.get("dashboards", [])]
    rows = []
    cutoff = int(time.time()) - 86400
    for host in hosts:
        query = urllib.parse.urlencode({"from": cutoff, "host": host.name})
        payload = get_json(base + "/api/v1/metrics?" + query, headers)
        rows.extend(CatalogRow("datadog", str(metric), str(metric), host=host.name, status="active_24h") for metric in payload.get("metrics", []))
    return rows


def zabbix_call(method: str, params: dict[str, Any]) -> Any:
    url = required("ZBX_URL").rstrip("/")
    if not url.endswith("api_jsonrpc.php"):
        url += "/api_jsonrpc.php"
    headers = {"Authorization": "Bearer " + required("ZBX_TOKEN"), "Content-Type": "application/json-rpc"}
    response = post_json(url, headers, {"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
    if "error" in response:
        raise RuntimeError(f"Zabbix {method}: {response['error'].get('message')}: {response['error'].get('data')}")
    return response["result"]


def zabbix_catalog(kind: str, hosts: list[Any]) -> list[CatalogRow]:
    if kind == "dashboards":
        items = zabbix_call("dashboard.get", {"output": ["dashboardid", "name"]})
        return [CatalogRow("zabbix", str(item["dashboardid"]), str(item["name"]), status="listed") for item in items]
    if kind == "applications":
        # Zabbix has no universal application object in current API versions.
        return []
    rows = []
    for host in hosts:
        items = zabbix_call("item.get", {"hostids": host.source_id, "output": ["itemid", "name", "key_", "status", "state", "lastclock"]})
        for item in items:
            if str(item.get("status", "0")) != "0":
                continue
            recent = int(item.get("lastclock") or 0) >= int(time.time()) - 86400
            rows.append(CatalogRow("zabbix", str(item.get("itemid", "")), str(item.get("name") or item.get("key_")), host.name, str(item.get("key_", "")), "active_24h" if recent else "configured_no_recent_data"))
    return rows


def splunk_auth() -> tuple[str, dict[str, str]]:
    return required("SPLUNK_URL").rstrip("/"), {"Authorization": "Bearer " + required("SPLUNK_TOKEN"), "Accept": "application/json"}


def splunk_catalog(kind: str, hosts: list[Any]) -> list[CatalogRow]:
    base, headers = splunk_auth()
    if kind in ("applications", "metrics"):
        search = ("| tstats count where index=* earliest=-24h by host sourcetype" if kind == "applications"
                  else "| mstats count WHERE index=* metric_name=* earliest=-24h BY metric_name")
        form = urllib.parse.urlencode({"search": search, "output_mode": "json"}).encode()
        req = urllib.request.Request(base + "/services/search/v2/jobs/export", data=form, headers={**headers, "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as response:
            records = [json.loads(line) for line in response.read().decode().splitlines() if line.strip()]
        rows = []
        for record in records:
            item = record.get("result") or {}
            if kind == "metrics":
                metric = str(item.get("metric_name", ""))
                if metric:
                    rows.append(CatalogRow("splunk", metric, metric, status="active_24h"))
            else:
                sourcetype = str(item.get("sourcetype", ""))
                host = str(item.get("host", ""))
                if host and any(term in sourcetype.lower() for term in ("app", "service", "trace")):
                    rows.append(CatalogRow("splunk", sourcetype, sourcetype, host, status="inferred_from_logs"))
        return rows
    path = "/servicesNS/-/-/data/ui/views"
    rows = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({"output_mode": "json", "count": 500, "offset": offset})
        payload = get_json(base + path + "?" + query, headers)
        entries = payload.get("entry", [])
        for item in entries:
            content = item.get("content") or {}
            if kind == "dashboards" and not ("dashboard" in str(content.get("eai:type", "")).lower() or "dashboard" in str(content.get("eai:data", "")).lower()):
                continue
            rows.append(CatalogRow("splunk", str(item.get("name", "")), str(content.get("label") or item.get("name", "")), scope=str(content.get("eai:acl", {}).get("app", "")), status="listed"))
        offset += len(entries)
        if len(entries) < 500:
            break
    return rows


def solarwinds_query(swql: str) -> list[dict[str, Any]]:
    base = required("SW_URL").rstrip("/")
    auth = base64.b64encode((required("SW_USER") + ":" + required("SW_PASSWORD")).encode()).decode()
    headers = {"Authorization": "Basic " + auth, "Content-Type": "application/json"}
    payload = post_json(base + "/SolarWinds/InformationService/v3/Json/Query", headers, {"query": swql})
    return payload.get("results", [])


def solarwinds_catalog(kind: str, hosts: list[Any]) -> list[CatalogRow]:
    host_names = {host.source_id: host.name for host in hosts}
    if kind == "applications":
        items = solarwinds_query("SELECT ApplicationID, Name, NodeID FROM Orion.APM.Application")
        return [CatalogRow("solarwinds", str(item.get("ApplicationID", "")), str(item.get("Name", "")), host_names.get(str(item.get("NodeID")), ""), status="SAM_application") for item in items]
    if kind == "dashboards":
        items = solarwinds_query("SELECT UniqueKey, DisplayName FROM Orion.Dashboards.Instances WHERE ParentID IS NULL")
        return [CatalogRow("solarwinds", str(item.get("UniqueKey", "")), str(item.get("DisplayName", "")), scope="modern", status="listed") for item in items]
    # Orion pollers represent configured measurements; live freshness is not exposed here.
    items = solarwinds_query("SELECT PollerID, PollerType, NetObject FROM Orion.Pollers")
    return [CatalogRow("solarwinds", str(item.get("PollerID", "")), str(item.get("PollerType", "")), scope=str(item.get("NetObject", "")), status="configured_poller") for item in items]


CONNECTORS = {"dynatrace": dynatrace_catalog, "datadog": datadog_catalog, "zabbix": zabbix_catalog, "splunk": splunk_catalog, "solarwinds": solarwinds_catalog}


def collect_catalog(observations: list[Any]) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, str]]]:
    catalog: dict[str, list[dict[str, str]]] = {kind: [] for kind in ("applications", "dashboards", "metrics")}
    diagnostics: dict[str, dict[str, str]] = {kind: {} for kind in catalog}
    for tool, connector in CONNECTORS.items():
        hosts = [obs for obs in observations if obs.tool == tool]
        for kind in catalog:
            if tool == "zabbix" and kind == "applications":
                diagnostics[kind][tool] = "unsupported_universal_application_catalog"
                continue
            try:
                rows = connector(kind, hosts)
                catalog[kind].extend(asdict(row) for row in rows)
                diagnostics[kind][tool] = "available"
            except Exception as exc:
                diagnostics[kind][tool] = str(exc)
    return catalog, diagnostics
