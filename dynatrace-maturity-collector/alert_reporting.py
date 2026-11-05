"""Exact host alert associations and distinct occurrence counts over a recent window."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from operational_inventory import timestamp

HISTORY_MEASUREMENTS = {"problem_resolution", "trigger_recovery", "alert_reset"}
ALERT_COLUMNS = ["alert_type", "affected_host_entity_ids", "recent_occurrences_count", "latest_occurrence_timestamp",
                 "alert_window_start", "alert_window_end", "alert_summary_status", "alert_details"]


def prepare_alerts(alerts, observations, days=30, now=None):
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    # Only exact, unambiguous names/aliases from the same tool can map a
    # name-only monitor group. Dynatrace always requires explicit HOST IDs.
    names = defaultdict(set)
    for obs in observations:
        for name in [obs.name, *obs.aliases]:
            names[(obs.tool, name.strip().lower())].add(obs.source_id)
    normalized, groups = [], defaultdict(list)
    host_events = defaultdict(dict)
    for original in alerts:
        row = dict(original)
        tool = row.get("tool", "")
        row["alert_type"] = row.get("alert_type") or "unknown"
        host_ids = row.get("affected_host_entity_ids") or []
        if isinstance(host_ids, str):
            host_ids = [h.strip() for h in host_ids.split(";") if h.strip()]
        row["affected_host_entity_ids"] = sorted(set(str(h) for h in host_ids))
        row.setdefault("affected_host_names", [])
        row.setdefault("host_association_basis", "unassigned")
        if not host_ids and tool != "dynatrace" and row.get("host"):
            matches = names.get((tool, row["host"].strip().lower()), set())
            if len(matches) == 1:
                row["affected_host_entity_ids"] = sorted(matches)
                row["host_association_basis"] = "exact_unambiguous_host_name"
        opened = timestamp(row.get("opened_at"))
        measurement = row.get("measurement", "")
        complete = measurement in HISTORY_MEASUREMENTS
        eligible = bool(complete and opened and start <= opened <= now)
        row["occurrence_count_basis"] = "distinct_started_records_30d" if complete else "snapshot_not_occurrence_history"
        row["recent_occurrences_count"] = None
        row["latest_occurrence_timestamp"] = opened.isoformat() if measurement == "latest_monitor_recovery" and opened and opened <= now else ""
        row["occurrence_window_start"] = start.isoformat()
        row["occurrence_window_end"] = now.isoformat()
        group = (tool, row["alert_type"], row.get("name", ""), tuple(row["affected_host_entity_ids"]), measurement)
        groups[group].append((row, opened, eligible))
        if eligible:
            # One multi-host problem counts once for each explicitly affected
            # host. Duplicate problem rows do not inflate that host's count.
            identity = str(row.get("source_id") or "")
            if identity:
                for host_id in row["affected_host_entity_ids"]:
                    host_events[(tool, host_id)][identity] = row
        normalized.append(row)
    for group in groups.values():
        recent = {str(row.get("source_id")): opened for row, opened, eligible in group if eligible and row.get("source_id")}
        latest = max(recent.values()).isoformat() if recent else ""
        for row, _, _ in group:
            if row.get("measurement") in HISTORY_MEASUREMENTS:
                row["recent_occurrences_count"] = len(recent)
                row["latest_occurrence_timestamp"] = latest
    return normalized, host_events, start.isoformat(), now.isoformat()


def host_alert_fields(host, events, diagnostics, window_start, window_end):
    selected, details = {}, defaultdict(dict)
    affected = []
    tools = host["entity_ids"]
    reliable_tools = {tool for tool in tools if diagnostics.get(tool) == "available" and tool in {"dynatrace", "zabbix"}}
    for tool, host_id in tools.items():
        for identity, row in events.get((tool, host_id), {}).items():
            selected[(tool, identity)] = row
            details[(tool, row["alert_type"], row.get("name", ""))][identity] = row
            affected.append(host_id)
    summaries = []
    for (tool, alert_type, name), records in sorted(details.items()):
        latest = max(timestamp(row["opened_at"]) for row in records.values()).isoformat()
        summaries.append({"tool": tool, "alert_type": alert_type, "name": name,
                          "recent_occurrences_count": len(records), "latest_occurrence_timestamp": latest,
                          "affected_host_entity_id": tools[tool]})
    complete = len(reliable_tools) == len(tools)
    latest = max((timestamp(row["opened_at"]) for row in selected.values()), default=None)
    return {
        "alert_type": "; ".join(sorted({row["alert_type"] for row in selected.values()})) or "unknown",
        "affected_host_entity_ids": "; ".join(sorted(set(affected))),
        "recent_occurrences_count": len(selected) if complete or selected else "unknown",
        "latest_occurrence_timestamp": latest.isoformat() if latest else "unknown",
        "alert_window_start": window_start, "alert_window_end": window_end,
        "alert_summary_status": "explicit_host_records_only" if complete else "partial" if selected else "unknown",
        "alert_details": summaries,
    }
