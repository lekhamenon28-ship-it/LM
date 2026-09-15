#!/usr/bin/env python3
"""Read-only host and signal inventory across observability platforms."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collector import ApiClient
from catalog_inventory import collect_catalog
from credential_config import DEFAULT_FILE, load_credentials
from operational_inventory import collect_operational, mttr_rows

TOOLS = ("dynatrace", "datadog", "zabbix", "splunk", "solarwinds")
SIGNALS = ("full_stack", "cpu", "memory", "disk", "application", "kubernetes", "cloud")


@dataclass
class Observation:
    tool: str
    source_id: str
    name: str
    kind: str = "host"
    aliases: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    evidence: dict[str, list[str]] = field(default_factory=dict)


def request_json(url: str, headers: dict[str, str], body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(min(int(exc.headers.get("Retry-After", "2")), 30))
                continue
            raise RuntimeError(f"HTTP {exc.code} from {url}: {exc.read().decode(errors='replace')[:300]}") from exc
        except urllib.error.URLError as exc:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Connection error from {url}: {exc.reason}") from exc


def add(obs: Observation, signal: str, source: str) -> None:
    if signal not in obs.signals:
        obs.signals.append(signal)
    sources = obs.evidence.setdefault(signal, [])
    if source not in sources:
        sources.append(source)


def classify(text: str) -> set[str]:
    value = text.lower()
    patterns = {
        "cpu": r"(?:\bcpu\b|processor|system\.cpu|host\.cpu)",
        "memory": r"(?:\bmemory\b|\bmem\b|system\.mem|host\.mem|vm\.memory)",
        "disk": r"(?:\bdisk\b|filesystem|\bfs\.|volume|storage)",
        "application": r"(?:\bapm\b|application|service\.|trace|web\.test|http\.test)",
        "kubernetes": r"(?:kubernetes|\bk8s\b|\bkube\b|container|pod\.)",
        "cloud": r"(?:\baws\b|\bazure\b|\bgcp\b|cloud|ec2)",
    }
    return {signal for signal, pattern in patterns.items() if re.search(pattern, value)}


def classify_sources(obs: Observation, values: list[str], prefix: str) -> None:
    for value in values:
        for signal in classify(value):
            add(obs, signal, f"{prefix}:{value[:100]}")


def dynatrace() -> list[Observation]:
    client = ApiClient(required("DT_ENV_URL"), required("DT_API_TOKEN"))
    fields = "+properties,+tags,+fromRelationships,+toRelationships"
    observations: list[Observation] = []
    config_accessible = True
    for entity_type in ("HOST", "SERVICE", "PROCESS_GROUP_INSTANCE", "KUBERNETES_NODE", "CLOUD_APPLICATION"):
        entities, _ = client.paged("/api/v2/entities", {"pageSize": 500, "entitySelector": f'type("{entity_type}")', "fields": fields}, "entities")
        if entity_type == "HOST":
            for host in entities:
                props = host.get("properties") or {}
                name = str(host.get("displayName") or host.get("entityId"))
                aliases = [str(v) for v in (props.get("hostName"), props.get("ipAddresses", [])) if isinstance(v, str)]
                aliases += [str(v) for v in props.get("ipAddresses", [])] if isinstance(props.get("ipAddresses"), list) else []
                obs = Observation("dynatrace", str(host["entityId"]), name, aliases=aliases)
                mode = str(props.get("monitoringMode", ""))
                if not mode and config_accessible:
                    try:
                        config = client.get("/api/config/v1/hosts/" + urllib.parse.quote(str(host["entityId"]), safe=""))
                        monitoring = config.get("monitoringConfig") or {}
                        if monitoring.get("monitoringEnabled") is not False:
                            mode = str(monitoring.get("monitoringMode", ""))
                    except RuntimeError as exc:
                        if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                            config_accessible = False
                if mode.upper() == "FULL_STACK":
                    add(obs, "full_stack", "OneAgent.monitoringConfig.monitoringMode")
                observations.append(obs)
        else:
            for entity in entities:
                relations = entity.get("fromRelationships", {}) | entity.get("toRelationships", {})
                host_ids = {str(item["id"] if isinstance(item, dict) else item) for key, items in relations.items() if "HOST" in key.upper() for item in items}
                for obs in observations:
                    if obs.source_id in host_ids:
                        add(obs, "kubernetes" if entity_type == "KUBERNETES_NODE" else "cloud" if entity_type == "CLOUD_APPLICATION" else "application", f"related:{entity_type}:{entity.get('entityId')}")
    metric_ids = {"cpu": "builtin:host.cpu.usage", "memory": "builtin:host.mem.usage", "disk": "builtin:host.disk.usedPct"}
    by_id = {obs.source_id: obs for obs in observations}
    for signal, metric_id in metric_ids.items():
        payload = client.get("/api/v2/metrics/query", {"metricSelector": metric_id + ':splitBy("dt.entity.host")', "from": "now-24h", "resolution": "Inf"})
        for result in payload.get("result", []):
            for series in result.get("data", []):
                host_id = (series.get("dimensionMap") or {}).get("dt.entity.host")
                if host_id in by_id and any(v is not None for v in series.get("values", [])):
                    add(by_id[host_id], signal, f"metric:{metric_id}")
    return observations


def datadog() -> list[Observation]:
    base = os.environ.get("DD_SITE", "datadoghq.com")
    if "://" not in base:
        base = "https://api." + base
    headers = {"DD-API-KEY": required("DD_API_KEY"), "DD-APPLICATION-KEY": required("DD_APP_KEY"), "Accept": "application/json"}
    result: list[Observation] = []
    start = 0
    while True:
        query = urllib.parse.urlencode({"start": start, "count": 1000, "include_hosts_metadata": "true"})
        payload = request_json(base.rstrip("/") + "/api/v1/hosts?" + query, headers)
        hosts = payload.get("host_list", [])
        for host in hosts:
            name = str(host.get("name") or host.get("id") or host.get("aws_id"))
            aliases = [str(v) for v in host.get("aliases", []) if v]
            obs = Observation("datadog", str(host.get("id") or host.get("aws_id") or name), name, aliases=aliases)
            metrics = host.get("metrics") or {}
            if metrics.get("cpu") is not None or metrics.get("iowait") is not None:
                add(obs, "cpu", "host.metrics.cpu/iowait")
            apps = [str(v) for v in host.get("apps", [])]
            classify_sources(obs, apps, "integration")
            classify_sources(obs, [str(v) for v in host.get("sources", [])], "source")
            result.append(obs)
        start += len(hosts)
        if not hosts or start >= int(payload.get("total_matching", start)) or len(hosts) < 1000:
            break
    by_name = {key(alias): obs for obs in result for alias in [obs.name] + obs.aliases if key(alias)}
    metric_ids = {"cpu": "system.cpu.user", "memory": "system.mem.used", "disk": "system.disk.used"}
    end = int(time.time())
    for signal, metric_id in metric_ids.items():
        query = urllib.parse.urlencode({"from": end - 86400, "to": end, "query": f"avg:{metric_id}{{*}} by {{host}}"})
        payload = request_json(base.rstrip("/") + "/api/v1/query?" + query, headers)
        for series in payload.get("series", []):
            tags = [str(tag) for tag in series.get("tag_set", [])]
            tags += str(series.get("scope") or "").split(",")
            host_name = next((tag[5:] for tag in tags if tag.startswith("host:")), "")
            obs = by_name.get(key(host_name))
            if obs and any(point and len(point) > 1 and point[1] is not None for point in series.get("pointlist", [])):
                add(obs, signal, f"metric:{metric_id}")
    return result


def zabbix() -> list[Observation]:
    url = required("ZBX_URL").rstrip("/")
    if not url.endswith("api_jsonrpc.php"):
        url += "/api_jsonrpc.php"
    headers = {"Content-Type": "application/json-rpc", "Authorization": "Bearer " + required("ZBX_TOKEN")}

    def call(method: str, params: dict[str, Any]) -> Any:
        response = request_json(url, headers, {"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
        if "error" in response:
            raise RuntimeError(f"Zabbix {method}: {response['error'].get('message')}: {response['error'].get('data')}")
        return response["result"]

    hosts = call("host.get", {"output": ["hostid", "host", "name", "status"], "selectInterfaces": ["ip", "dns"]})
    result = []
    for host in hosts:
        if str(host.get("status", "0")) != "0":
            continue
        obs = Observation("zabbix", str(host["hostid"]), str(host.get("host") or host.get("name")), aliases=[str(v) for iface in host.get("interfaces", []) for v in (iface.get("ip"), iface.get("dns")) if v])
        items = call("item.get", {"hostids": host["hostid"], "output": ["key_", "name", "status", "state"]})
        classify_sources(obs, [str(item.get("key_", "")) for item in items if str(item.get("status", "0")) == "0" and str(item.get("state", "0")) == "0"], "item")
        result.append(obs)
    return result


def splunk() -> list[Observation]:
    base = required("SPLUNK_URL").rstrip("/")
    token = required("SPLUNK_TOKEN")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/x-www-form-urlencoded"}
    search = "| tstats count where index=* earliest=-24h by host sourcetype"
    form = urllib.parse.urlencode({"search": search, "output_mode": "json"}).encode()
    req = urllib.request.Request(base + "/services/search/v2/jobs/export", data=form, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as response:
        lines = response.read().decode().splitlines()
    by_host: dict[str, Observation] = {}
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        row = record.get("result") or {}
        host = str(row.get("host", ""))
        if not host or host == "*":
            continue
        obs = by_host.setdefault(host, Observation("splunk", host, host))
        classify_sources(obs, [str(row.get("sourcetype", ""))], "sourcetype")
    return list(by_host.values())


def solarwinds() -> list[Observation]:
    base = required("SW_URL").rstrip("/")
    credentials = (required("SW_USER") + ":" + required("SW_PASSWORD")).encode()
    headers = {"Authorization": "Basic " + base64.b64encode(credentials).decode(), "Content-Type": "application/json"}

    def query(swql: str) -> list[dict[str, Any]]:
        payload = request_json(base + "/SolarWinds/InformationService/v3/Json/Query", headers, {"query": swql})
        return payload.get("results", [])

    rows = query("SELECT NodeID, Caption, DNS, IPAddress, CPULoad, PercentMemoryUsed FROM Orion.Nodes")
    by_id: dict[str, Observation] = {}
    for row in rows:
        obs = Observation("solarwinds", str(row["NodeID"]), str(row.get("DNS") or row.get("Caption") or row["NodeID"]), aliases=[str(v) for v in (row.get("Caption"), row.get("IPAddress")) if v])
        if row.get("CPULoad") is not None:
            add(obs, "cpu", "Orion.Nodes.CPULoad")
        if row.get("PercentMemoryUsed") is not None:
            add(obs, "memory", "Orion.Nodes.PercentMemoryUsed")
        by_id[obs.source_id] = obs
    for row in query("SELECT NodeID, VolumeID FROM Orion.Volumes"):
        if str(row.get("NodeID")) in by_id:
            add(by_id[str(row["NodeID"])], "disk", "Orion.Volumes")
    try:
        for row in query("SELECT NodeID, ApplicationID FROM Orion.APM.Application"):
            if str(row.get("NodeID")) in by_id:
                add(by_id[str(row["NodeID"])], "application", "Orion.APM.Application")
    except RuntimeError:
        pass  # SAM is optional in SolarWinds deployments.
    return list(by_id.values())


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable {name}")
    return value


def key(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    if not value or value in ("localhost", "127.0.0.1", "::1"):
        return ""
    return value


def merge(observations: list[Observation]) -> list[dict[str, Any]]:
    # Only exact names and explicit aliases are matched; unknown identities remain separate.
    groups: list[list[Observation]] = []
    lookup: dict[str, list[int]] = defaultdict(list)
    for obs in observations:
        identities = {key(v) for v in [obs.name] + obs.aliases} - {""}
        matches = {index for identity in identities for index in lookup[identity]}
        if len(matches) == 1 and not any(item.tool == obs.tool for item in groups[next(iter(matches))]):
            index = next(iter(matches))
            groups[index].append(obs)
        else:
            index = len(groups)
            groups.append([obs])
        for identity in identities:
            if index not in lookup[identity]:
                lookup[identity].append(index)
    result = []
    for group in groups:
        names = sorted({obs.name for obs in group}, key=lambda name: (-("." in name), name.lower()))
        tools = sorted({obs.tool for obs in group})
        signals = sorted({signal for obs in group for signal in obs.signals})
        result.append({"host": names[0], "aliases": names[1:], "tools": tools, "tool_count": len(tools), "signals": signals, "observations": [asdict(obs) for obs in group]})
    return sorted(result, key=lambda row: row["host"].lower())


def write_outputs(observations: list[Observation], diagnostics: dict[str, str], output: Path,
                  catalog: dict[str, list[dict[str, str]]] | None = None,
                  catalog_diagnostics: dict[str, dict[str, str]] | None = None,
                  operational: dict[str, list[dict[str, Any]]] | None = None,
                  operational_diagnostics: dict[str, dict[str, str]] | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    hosts = merge(observations)
    catalog = catalog or {kind: [] for kind in ("applications", "dashboards", "metrics")}
    catalog_diagnostics = catalog_diagnostics or {kind: {} for kind in catalog}
    operational = operational or {kind: [] for kind in ("service_relationships", "alerts", "trace_availability")}
    operational_diagnostics = operational_diagnostics or {kind: {} for kind in operational}
    mttr = mttr_rows(operational.get("alerts", []))
    document = {"generated_at": datetime.now(timezone.utc).isoformat(), "hosts": hosts, "diagnostics": diagnostics,
                "catalog": catalog, "catalog_diagnostics": catalog_diagnostics,
                "operational": operational, "operational_diagnostics": operational_diagnostics, "mttr": mttr}
    (output / "host_inventory.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    with (output / "host_inventory.csv").open("w", newline="", encoding="utf-8") as handle:
        columns = ["host", "aliases", "tools", "tool_count"] + list(TOOLS) + list(SIGNALS)
        writer = csv.DictWriter(handle, columns)
        writer.writeheader()
        for host in hosts:
            row = {"host": host["host"], "aliases": "; ".join(host["aliases"]), "tools": "; ".join(host["tools"]), "tool_count": host["tool_count"]}
            row.update({tool: "yes" if tool in host["tools"] else "unknown" if diagnostics.get(tool) != "available" else "no" for tool in TOOLS})
            row.update({signal: "yes" if signal in host["signals"] else "unknown" for signal in SIGNALS})
            writer.writerow(row)
    with (output / "tool_host_details.csv").open("w", newline="", encoding="utf-8") as handle:
        columns = ["host", "tool", "source_id", "kind"] + list(SIGNALS) + ["evidence"]
        writer = csv.DictWriter(handle, columns)
        writer.writeheader()
        for host in hosts:
            for obs in host["observations"]:
                row = {"host": host["host"], "tool": obs["tool"], "source_id": obs["source_id"], "kind": obs["kind"], "evidence": json.dumps(obs["evidence"], sort_keys=True)}
                row.update({signal: "yes" if signal in obs["signals"] else "unknown" for signal in SIGNALS})
                writer.writerow(row)
    with (output / "connector_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tool", "dataset", "status"])
        writer.writerows((tool, "hosts", diagnostics.get(tool, "not_configured")) for tool in TOOLS)
        writer.writerows((tool, kind, catalog_diagnostics.get(kind, {}).get(tool, "unknown")) for kind in ("applications", "dashboards", "metrics") for tool in TOOLS)
        writer.writerows((tool, kind, operational_diagnostics.get(kind, {}).get(tool, "unknown")) for kind in ("service_relationships", "alerts", "trace_availability") for tool in TOOLS)
    for kind in ("applications", "dashboards", "metrics"):
        with (output / (kind + ".csv")).open("w", newline="", encoding="utf-8") as handle:
            columns = ["tool", "source_id", "name", "host", "scope", "status"]
            writer = csv.DictWriter(handle, columns)
            writer.writeheader()
            writer.writerows(sorted(catalog.get(kind, []), key=lambda row: (row["tool"], row["name"].lower(), row.get("host", ""))))
    operational_columns = {
        "service_relationships": ["tool", "source_id", "source", "target_id", "target", "relationship", "evidence"],
        "alerts": ["tool", "source_id", "name", "host", "service", "severity", "status", "opened_at", "resolved_at", "duration_minutes", "measurement"],
        "trace_availability": ["tool", "service", "host", "status", "evidence"],
    }
    for kind, columns in operational_columns.items():
        with (output / (kind + ".csv")).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, columns)
            writer.writeheader()
            writer.writerows(sorted(operational.get(kind, []), key=lambda row: (row["tool"], row.get("name", row.get("service", row.get("source", ""))))))
    with (output / "mttr.csv").open("w", newline="", encoding="utf-8") as handle:
        columns = ["tool", "measurement", "sample_count", "mean_minutes", "median_minutes"]
        writer = csv.DictWriter(handle, columns)
        writer.writeheader()
        writer.writerows(mttr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out"))
    parser.add_argument("--fixture", type=Path, help="JSON fixture with observations and diagnostics")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_FILE, help="JSON credentials file (default: credentials.json beside this script)")
    args = parser.parse_args()
    if args.fixture:
        data = json.loads(args.fixture.read_text(encoding="utf-8"))
        observations = [Observation(**item) for item in data["observations"]]
        diagnostics = data.get("diagnostics", {})
        catalog = data.get("catalog", {})
        catalog_diagnostics = data.get("catalog_diagnostics", {})
        operational = data.get("operational", {})
        operational_diagnostics = data.get("operational_diagnostics", {})
    else:
        try:
            load_credentials(args.credentials)
        except RuntimeError as exc:
            parser.error(str(exc))
        observations = []
        diagnostics = {}
        for name, connector in (("dynatrace", dynatrace), ("datadog", datadog), ("zabbix", zabbix), ("splunk", splunk), ("solarwinds", solarwinds)):
            try:
                found = connector()
                observations.extend(found)
                diagnostics[name] = "available"
                print(f"{name}: {len(found)} hosts")
            except Exception as exc:
                diagnostics[name] = str(exc)
                print(f"{name}: {exc}", file=sys.stderr)
        catalog, catalog_diagnostics = collect_catalog(observations)
        operational, operational_diagnostics = collect_operational(observations, catalog)
    write_outputs(observations, diagnostics, args.output, catalog, catalog_diagnostics, operational, operational_diagnostics)
    print(f"Wrote {len(merge(observations))} hosts to {args.output.resolve() / 'host_inventory.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
