"""Relationships, alerts, recovery durations, and trace evidence."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from collector import ApiClient
from catalog_inventory import dd_client, get_json, post_json, required, solarwinds_query, splunk_auth, zabbix_call

TOOLS = ("dynatrace", "datadog", "zabbix", "splunk", "solarwinds")
KINDS = ("service_relationships", "alerts", "trace_availability")


@dataclass
class Relationship:
    tool: str
    source_id: str
    source: str
    target_id: str
    target: str
    relationship: str
    evidence: str


@dataclass
class Alert:
    tool: str
    source_id: str
    name: str
    host: str = ""
    service: str = ""
    severity: str = ""
    status: str = ""
    opened_at: str = ""
    resolved_at: str = ""
    duration_minutes: float | None = None
    measurement: str = ""


@dataclass
class Trace:
    tool: str
    service: str
    host: str = ""
    status: str = "unknown"
    evidence: str = ""


def timestamp(value: Any) -> datetime | None:
    if value in (None, "", 0, "0", -1, "-1"):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            return datetime.fromtimestamp(number, timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


def alert_row(tool: str, source_id: Any, name: Any, opened: Any = None, resolved: Any = None,
              measurement: str = "", **kwargs: Any) -> Alert:
    start = timestamp(opened)
    end = timestamp(resolved)
    minutes = round((end - start).total_seconds() / 60, 2) if start and end and end >= start else None
    return Alert(tool, str(source_id), str(name), opened_at=start.isoformat() if start else "",
                 resolved_at=end.isoformat() if end else "", duration_minutes=minutes,
                 measurement=measurement, **kwargs)


def dt_client() -> ApiClient:
    return ApiClient(required("DT_ENV_URL"), required("DT_API_TOKEN"))


def dynatrace_relationships(hosts: list[Any]) -> list[Relationship]:
    client = dt_client()
    entities: list[dict[str, Any]] = []
    for entity_type in ("HOST", "SERVICE", "APPLICATION", "CLOUD_APPLICATION"):
        batch, _ = client.paged("/api/v2/entities", {"pageSize": 500, "entitySelector": f'type("{entity_type}")', "fields": "+fromRelationships,+toRelationships"}, "entities")
        entities.extend(batch)
    names = {str(item.get("entityId")): str(item.get("displayName") or item.get("entityId")) for item in entities}
    rows: list[Relationship] = []
    seen: set[tuple[str, str, str]] = set()
    for entity in entities:
        entity_id = str(entity.get("entityId", ""))
        for direction in ("fromRelationships", "toRelationships"):
            for relation, targets in (entity.get(direction) or {}).items():
                for target in targets:
                    target_id = str(target.get("id") if isinstance(target, dict) else target)
                    if target_id not in names:
                        continue
                    source_id, dest_id = (target_id, entity_id) if direction == "fromRelationships" else (entity_id, target_id)
                    marker = (source_id, dest_id, relation)
                    if marker not in seen:
                        seen.add(marker)
                        rows.append(Relationship("dynatrace", source_id, names[source_id], dest_id, names[dest_id], relation, "entity_relationship"))
    return rows


def dynatrace_alerts(hosts: list[Any]) -> list[Alert]:
    client = dt_client()
    problems, _ = client.paged("/api/v2/problems", {"pageSize": 500, "from": "now-30d", "to": "now"}, "problems")
    rows = []
    for problem in problems:
        root = problem.get("rootCauseEntity") or {}
        root_id = root.get("entityId") or {} if isinstance(root, dict) else {}
        service = str(root.get("name") or (root_id.get("id") if isinstance(root_id, dict) else root_id) or "") if isinstance(root, dict) else ""
        status = str(problem.get("status", ""))
        rows.append(alert_row("dynatrace", problem.get("problemId") or problem.get("displayId"), problem.get("title", ""),
                              problem.get("startTime"), problem.get("endTime"), "problem_resolution",
                              service=service, severity=str(problem.get("severityLevel") or problem.get("impactLevel") or ""), status=status))
    return rows


def dynatrace_traces(applications: list[dict[str, str]]) -> list[Trace]:
    rows = [Trace("dynatrace", item["name"], item.get("host", ""), "unknown", "Grail_access_not_configured")
            for item in applications if item.get("tool") == "dynatrace"]
    token = os.environ.get("DT_PLATFORM_TOKEN", "").strip()
    base = os.environ.get("DT_PLATFORM_URL", "").strip().rstrip("/")
    if not token or not base:
        return rows
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"}
    query = "fetch spans, from:-24h | filter isNotNull(dt.entity.service) | summarize span_count=count(), by:{dt.entity.service}"
    result = post_json(base + "/platform/storage/query/v1/query:execute", headers, {"query": query})
    request_token = result.get("requestToken")
    if not request_token:
        raise RuntimeError("Dynatrace Grail query did not return a request token")
    for _ in range(12):
        response = get_json(base + "/platform/storage/query/v1/query:poll?" + urllib.parse.urlencode({"request-token": request_token}), headers)
        if response.get("state") == "SUCCEEDED":
            counts = {str(record.get("dt.entity.service")): int(record.get("span_count") or 0) for record in (response.get("result") or {}).get("records", response.get("records", []))}
            for row, item in zip(rows, (item for item in applications if item.get("tool") == "dynatrace")):
                if counts.get(item.get("source_id", ""), 0) > 0:
                    row.status = "yes"
                    row.evidence = "Grail.spans.active_24h"
                else:
                    row.evidence = "Grail.spans.no_matching_service_24h"
            return rows
        if response.get("state") == "FAILED":
            raise RuntimeError("Dynatrace Grail query failed")
        time.sleep(1)
    raise RuntimeError("Dynatrace Grail query did not finish within 12 seconds")


def datadog_relationships(hosts: list[Any]) -> list[Relationship]:
    base, headers = dd_client()
    env = os.environ.get("DD_ENV", "prod")
    payload = get_json(base + "/api/v1/service_dependencies?" + urllib.parse.urlencode({"env": env}), headers)
    return [Relationship("datadog", str(source), str(source), str(target), str(target), "calls", "APM_service_dependencies")
            for source, details in payload.items() if isinstance(details, dict) for target in details.get("calls", [])]


def datadog_alerts(hosts: list[Any]) -> list[Alert]:
    base, headers = dd_client()
    rows = []
    page = 0
    while True:
        query = urllib.parse.urlencode({"with_downtimes": "false", "group_states": "all", "page": page, "page_size": 1000})
        payload = get_json(base + "/api/v1/monitor?" + query, headers)
        if not isinstance(payload, list):
            raise RuntimeError("Datadog monitor response was not a list")
        for monitor in payload:
            groups = ((monitor.get("state") or {}).get("groups") or {})
            if not groups:
                rows.append(Alert("datadog", str(monitor.get("id", "")), str(monitor.get("name", "")),
                                  severity=str(monitor.get("priority") or ""), status=str(monitor.get("overall_state") or "unknown"), measurement="monitor_current_state"))
            for group_name, state in groups.items():
                if not isinstance(state, dict):
                    continue
                tags = [tag.strip() for tag in str(group_name).split(",")]
                host = next((tag[5:] for tag in tags if tag.startswith("host:")), "")
                service = next((tag[8:] for tag in tags if tag.startswith("service:")), "")
                row = alert_row("datadog", str(monitor.get("id", "")) + ":" + str(group_name), monitor.get("name", ""),
                                state.get("last_triggered_ts"), state.get("last_resolved_ts"), "latest_monitor_recovery",
                                host=host, service=service, severity=str(monitor.get("priority") or ""), status=str(state.get("status") or "unknown"))
                resolved = timestamp(state.get("last_resolved_ts"))
                if resolved and resolved.timestamp() < time.time() - 30 * 86400:
                    row.duration_minutes = None
                rows.append(row)
        if len(payload) < 1000:
            break
        page += 1
    return rows


def datadog_traces(applications: list[dict[str, str]]) -> list[Trace]:
    base, headers = dd_client()
    query = urllib.parse.urlencode({"filter[env]": "*"})
    payload = get_json(base + "/api/v2/apm/services?" + query, headers)
    attrs = (payload.get("data") or {}).get("attributes") or {}
    services = attrs.get("services") or []
    metadata = attrs.get("metadata") or []
    rows = []
    for index, service in enumerate(services):
        meta = metadata[index] if index < len(metadata) and isinstance(metadata[index], dict) else {}
        traced = meta.get("isTraced")
        rows.append(Trace("datadog", str(service), status="yes" if traced is True else "no" if traced is False else "unknown",
                          evidence="APM_service_list.isTraced" if traced is not None else ""))
    return rows


def zabbix_relationships(hosts: list[Any]) -> list[Relationship]:
    services = zabbix_call("service.get", {"output": ["serviceid", "name"], "selectChildren": ["serviceid", "name"]})
    rows = []
    for service in services:
        for child in service.get("children", []):
            rows.append(Relationship("zabbix", str(service["serviceid"]), str(service.get("name", "")),
                                     str(child.get("serviceid", "")), str(child.get("name", "")), "contains", "service_tree"))
    return rows


def zabbix_alerts(hosts: list[Any]) -> list[Alert]:
    since = int(time.time()) - 30 * 86400
    events = zabbix_call("event.get", {"output": ["eventid", "name", "clock", "value", "r_eventid", "severity", "acknowledged"],
                                       "time_from": since, "selectHosts": ["host", "name"], "source": 0, "object": 0})
    recoveries = {str(event.get("eventid")): event for event in events if str(event.get("value")) == "0"}
    needed = {str(event.get("r_eventid")) for event in events if str(event.get("value")) == "1" and str(event.get("r_eventid", "0")) != "0"} - recoveries.keys()
    if needed:
        extra = zabbix_call("event.get", {"output": ["eventid", "clock", "value"], "eventids": sorted(needed)})
        recoveries.update({str(event.get("eventid")): event for event in extra})
    rows = []
    for event in events:
        if str(event.get("value")) != "1":
            continue
        host = "; ".join(sorted({str(item.get("host") or item.get("name")) for item in event.get("hosts", [])}))
        recovery = recoveries.get(str(event.get("r_eventid")))
        rows.append(alert_row("zabbix", event.get("eventid"), event.get("name", ""), event.get("clock"),
                              recovery.get("clock") if recovery else None, "trigger_recovery",
                              host=host, severity=str(event.get("severity") or ""),
                              status="resolved" if recovery else "open"))
    return rows


def solarwinds_relationships(hosts: list[Any]) -> list[Relationship]:
    names = {host.source_id: host.name for host in hosts}
    applications = solarwinds_query("SELECT ApplicationID, Name, NodeID FROM Orion.APM.Application")
    return [Relationship("solarwinds", str(item.get("ApplicationID", "")), str(item.get("Name", "")),
                         str(item.get("NodeID", "")), names.get(str(item.get("NodeID")), ""), "runs_on", "SAM_application_node")
            for item in applications]


def solarwinds_alerts(hosts: list[Any]) -> list[Alert]:
    rows = solarwinds_query("SELECT AlertHistoryID, AlertActiveID, AlertObjectID, EventType, Message, TimeStamp FROM Orion.AlertHistory WHERE DAYDIFF(TimeStamp, GETUTCDATE()) < 30")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        identity = str(row.get("AlertActiveID") or row.get("AlertObjectID") or row.get("AlertHistoryID"))
        groups.setdefault(identity, []).append(row)
    result = []
    for identity, events in groups.items():
        triggers = sorted((event for event in events if int(event.get("EventType", -1)) == 0), key=lambda event: timestamp(event.get("TimeStamp")) or datetime.min.replace(tzinfo=timezone.utc))
        resets = sorted((event for event in events if int(event.get("EventType", -1)) in (1, 8)), key=lambda event: timestamp(event.get("TimeStamp")) or datetime.min.replace(tzinfo=timezone.utc))
        for trigger in triggers:
            opened = timestamp(trigger.get("TimeStamp"))
            matching = next((reset for reset in resets if opened and timestamp(reset.get("TimeStamp")) and timestamp(reset.get("TimeStamp")) >= opened), None)
            if matching:
                resets.remove(matching)
            result.append(alert_row("solarwinds", trigger.get("AlertHistoryID"), trigger.get("Message", ""),
                                    trigger.get("TimeStamp"), matching.get("TimeStamp") if matching else None,
                                    "alert_reset", status="resolved" if matching else "open"))
    return result


def splunk_alerts(hosts: list[Any]) -> list[Alert]:
    base, headers = splunk_auth()
    result = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({"output_mode": "json", "count": 500, "offset": offset})
        payload = get_json(base + "/servicesNS/-/-/saved/searches?" + query, headers)
        entries = payload.get("entry", [])
        for entry in entries:
            content = entry.get("content") or {}
            if str(content.get("is_scheduled", "0")) in ("1", "true", "True") and content.get("alert_type"):
                result.append(Alert("splunk", str(entry.get("name", "")), str(entry.get("name", "")),
                                    status="configured", measurement="saved_search_alert_rule"))
        offset += len(entries)
        if len(entries) < 500:
            break
    return result


def splunk_traces(applications: list[dict[str, str]]) -> list[Trace]:
    return [Trace("splunk", item["name"], item.get("host", ""), "inferred", "trace_sourcetype")
            for item in applications if item.get("tool") == "splunk" and "trace" in item.get("name", "").lower()]


CONNECTORS = {
    "service_relationships": {"dynatrace": dynatrace_relationships, "datadog": datadog_relationships,
                              "zabbix": zabbix_relationships, "splunk": None, "solarwinds": solarwinds_relationships},
    "alerts": {"dynatrace": dynatrace_alerts, "datadog": datadog_alerts, "zabbix": zabbix_alerts,
               "splunk": splunk_alerts, "solarwinds": solarwinds_alerts},
}


def collect_operational(observations: list[Any], catalog: dict[str, list[dict[str, str]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, str]]]:
    output: dict[str, list[dict[str, Any]]] = {kind: [] for kind in KINDS}
    diagnostics: dict[str, dict[str, str]] = {kind: {} for kind in KINDS}
    for kind, connectors in CONNECTORS.items():
        for tool in TOOLS:
            connector = connectors[tool]
            if connector is None:
                diagnostics[kind][tool] = "unsupported_no_reliable_relationship_source"
                continue
            try:
                rows = connector([obs for obs in observations if obs.tool == tool])
                output[kind].extend(asdict(row) for row in rows)
                diagnostics[kind][tool] = "available"
            except Exception as exc:
                diagnostics[kind][tool] = str(exc)
    applications = catalog.get("applications", [])
    for tool in TOOLS:
        try:
            if tool == "datadog":
                rows = datadog_traces(applications)
            elif tool == "dynatrace":
                rows = dynatrace_traces(applications)
            elif tool == "splunk":
                rows = splunk_traces(applications)
            else:
                rows = [Trace(tool, item["name"], item.get("host", ""), "unknown", "no_direct_trace_status_in_current_connector")
                        for item in applications if item.get("tool") == tool]
            output["trace_availability"].extend(asdict(row) for row in rows)
            diagnostics["trace_availability"][tool] = "available" if tool == "datadog" or (tool == "dynatrace" and os.environ.get("DT_PLATFORM_TOKEN") and os.environ.get("DT_PLATFORM_URL")) else "limited"
        except Exception as exc:
            diagnostics["trace_availability"][tool] = str(exc)
    return output, diagnostics


def mttr_rows(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    durations: dict[tuple[str, str], list[float]] = {}
    for alert in alerts:
        value = alert.get("duration_minutes")
        measurement = alert.get("measurement", "")
        if isinstance(value, (int, float)) and measurement in ("problem_resolution", "trigger_recovery", "alert_reset", "latest_monitor_recovery"):
            durations.setdefault((alert["tool"], measurement), []).append(float(value))
    return [{"tool": tool, "measurement": measurement, "sample_count": len(values),
             "mean_minutes": round(sum(values) / len(values), 2),
             "median_minutes": round(sorted(values)[len(values) // 2] if len(values) % 2 else
                                     (sorted(values)[len(values) // 2 - 1] + sorted(values)[len(values) // 2]) / 2, 2)}
            for (tool, measurement), values in sorted(durations.items())]
