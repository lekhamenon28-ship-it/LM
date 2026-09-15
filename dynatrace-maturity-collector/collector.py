#!/usr/bin/env python3
"""Tenant-wide Dynatrace inventory and observability maturity collector."""

from __future__ import annotations

import argparse
import csv
import getpass
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

VERSION = "1.0.0"
CREDENTIALS_FILE = Path.home() / ".config" / "dynatrace-maturity" / "credentials.json"


@dataclass
class Dataset:
    status: str
    data: Any = None
    endpoint: str = ""
    error: str | None = None
    pages: int = 0


@dataclass
class ApiClient:
    base_url: str
    token: str
    timeout: int = 30
    retries: int = 3

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.base_url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Api-Token {self.token}", "Accept": "application/json", "User-Agent": f"dt-maturity/{VERSION}"},
        )
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:600]
                if exc.code == 429 and attempt + 1 < self.retries:
                    time.sleep(int(exc.headers.get("Retry-After", "2")))
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(f"Connection error: {exc.reason}") from exc
        raise RuntimeError("Request failed")

    def paged(self, path: str, params: dict[str, Any], item_key: str) -> tuple[list[Any], int]:
        items: list[Any] = []
        pages = 0
        next_key: str | None = None
        while True:
            query = {"nextPageKey": next_key} if next_key else dict(params)
            payload = self.get(path, query)
            pages += 1
            items.extend(payload.get(item_key, []))
            next_key = payload.get("nextPageKey")
            if not next_key:
                return items, pages


def safe_collect(name: str, endpoint: str, fn: Callable[[], tuple[Any, int] | Any]) -> Dataset:
    try:
        result = fn()
        if isinstance(result, tuple) and len(result) == 2:
            return Dataset("available", result[0], endpoint, pages=result[1])
        return Dataset("available", result, endpoint, pages=1)
    except Exception as exc:  # one denied API must not abort the assessment
        message = str(exc)
        status = "forbidden" if "HTTP 401" in message or "HTTP 403" in message else "error"
        print(f"[{status}] {name}: {message}", file=sys.stderr)
        return Dataset(status, None, endpoint, message)


def build_selector(management_zone: str | None, tag: str | None) -> str | None:
    parts = []
    if management_zone:
        escaped = management_zone.replace('"', '\\"')
        parts.append(f'mzName("{escaped}")')
    if tag:
        escaped = tag.replace('"', '\\"')
        parts.append(f'tag("{escaped}")')
    return ",".join(parts) or None


def load_credentials() -> tuple[str, str]:
    base = os.environ.get("DT_ENV_URL", "")
    token = os.environ.get("DT_API_TOKEN", "")
    if base and token:
        return base.rstrip("/"), token
    try:
        saved = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        saved = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read credentials from {CREDENTIALS_FILE}: {exc}") from exc
    return (base or saved.get("environmentUrl", "")).rstrip("/"), token or saved.get("apiToken", "")


def setup_credentials() -> None:
    default_url = "https://vri13969.live.dynatrace.com"
    base = input(f"Dynatrace Environment API URL [{default_url}]: ").strip() or default_url
    token = getpass.getpass("Dynatrace API token: ").strip()
    if not token:
        raise SystemExit("Token was empty; credentials were not saved")
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(CREDENTIALS_FILE, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"environmentUrl": base.rstrip("/"), "apiToken": token}, handle, indent=2)
        handle.write("\n")
    CREDENTIALS_FILE.chmod(0o600)
    print(f"Credentials saved securely to {CREDENTIALS_FILE}")


def collect_live(args: argparse.Namespace) -> dict[str, Dataset]:
    base, token = load_credentials()
    if not base or not token:
        raise SystemExit("Credentials are required. Run collector.py --setup-credentials once, set DT_ENV_URL and DT_API_TOKEN, or use --fixture.")
    client = ApiClient(base, token, args.timeout)
    scope_selector = build_selector(args.management_zone, args.tag)

    problem_params: dict[str, Any] = {"from": args.from_time, "to": args.to_time, "pageSize": 500}
    if scope_selector:
        problem_params["entitySelector"] = scope_selector

    entity_types = safe_collect("entity types", "/api/v2/entityTypes", lambda: client.paged("/api/v2/entityTypes", {"pageSize": 500}, "types"))

    def collect_all_entities() -> tuple[list[Any], int]:
        if entity_types.status != "available":
            raise RuntimeError("Entity types could not be listed, so tenant-wide entity discovery cannot continue")
        all_entities: list[Any] = []
        total_pages = 0
        # The entities endpoint requires an entity selector. Enumerating the
        # tenant's own type catalog avoids a hard-coded and incomplete type list.
        for item in entity_types.data or []:
            type_id = item.get("type") if isinstance(item, dict) else str(item)
            if not type_id:
                continue
            selector = f'type("{type_id}")'
            if scope_selector:
                selector += "," + scope_selector
            params = {
                "pageSize": args.page_size,
                "entitySelector": selector,
                "fields": "+properties,+tags,+managementZones,+fromRelationships,+toRelationships",
            }
            page_items, pages = client.paged("/api/v2/entities", params, "entities")
            all_entities.extend(page_items)
            total_pages += pages
        return all_entities, total_pages

    return {
        "entities": safe_collect("entities", "/api/v2/entities", collect_all_entities),
        "entity_types": entity_types,
        "metrics": safe_collect("metric catalog", "/api/v2/metrics", lambda: client.paged("/api/v2/metrics", {"pageSize": 500, "fields": "displayName,description,unit,aggregationTypes,transformations,tags"}, "metrics")),
        "problems": safe_collect("problems", "/api/v2/problems", lambda: client.paged("/api/v2/problems", problem_params, "problems")),
        "settings_schemas": safe_collect("settings schemas", "/api/v2/settings/schemas", lambda: client.paged("/api/v2/settings/schemas", {"pageSize": 500}, "items")),
        "settings_objects": safe_collect("settings objects", "/api/v2/settings/objects", lambda: client.paged("/api/v2/settings/objects", {"pageSize": 500, "fields": "objectId,schemaId,scope,value"}, "items")),
        "slos": safe_collect("SLOs", "/api/v2/slo", lambda: client.paged("/api/v2/slo", {"pageSize": 500}, "slo")),
        "synthetic_monitors": safe_collect("synthetic monitors", "/api/v1/synthetic/monitors", lambda: client.get("/api/v1/synthetic/monitors").get("monitors", [])),
        "management_zones": safe_collect("management zones", "/api/config/v1/managementZones", lambda: client.get("/api/config/v1/managementZones").get("values", [])),
        "alerting_profiles": safe_collect("alerting profiles", "/api/config/v1/alertingProfiles", lambda: client.get("/api/config/v1/alertingProfiles").get("values", [])),
        "notifications": safe_collect("notifications", "/api/config/v1/notifications", lambda: client.get("/api/config/v1/notifications").get("values", [])),
    }


def load_fixture(path: Path) -> dict[str, Dataset]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    source = raw.get("datasets", raw)
    result = {}
    for name, value in source.items():
        result[name] = Dataset(**value) if isinstance(value, dict) and "status" in value else Dataset("available", value, "fixture")
    return result


def dataset_json(datasets: dict[str, Dataset]) -> dict[str, Any]:
    return {name: {"status": ds.status, "data": ds.data, "endpoint": ds.endpoint, "error": ds.error, "pages": ds.pages} for name, ds in datasets.items()}


def entity_type(entity: dict[str, Any]) -> str:
    return entity.get("type") or str(entity.get("entityId", "UNKNOWN")).split("-")[0] or "UNKNOWN"


def categorize_entity(type_name: str) -> str:
    value = type_name.upper()
    rules = [
        ("Kubernetes", ("KUBERNETES", "CLOUD_APPLICATION", "CONTAINER")),
        ("Applications & UX", ("APPLICATION", "MOBILE_APPLICATION", "CUSTOM_APPLICATION")),
        ("Services", ("SERVICE", "SERVICE_METHOD")),
        ("Infrastructure", ("HOST", "PROCESS", "DISK", "NETWORK", "HYPERVISOR")),
        ("Cloud", ("AWS", "AZURE", "GCP", "CLOUD_")),
        ("Databases", ("DATABASE", "SQL", "CASSANDRA", "MONGODB", "REDIS")),
        ("Synthetic", ("SYNTHETIC", "HTTP_CHECK")),
    ]
    for category, needles in rules:
        if any(n in value for n in needles):
            return category
    return "Other"


def tag_count(entity: dict[str, Any]) -> int:
    return len(entity.get("tags") or [])


def calculate_assessment(datasets: dict[str, Dataset], args: argparse.Namespace) -> dict[str, Any]:
    entities = datasets.get("entities", Dataset("unknown", [])).data or []
    problems = datasets.get("problems", Dataset("unknown", [])).data or []
    metrics = datasets.get("metrics", Dataset("unknown", [])).data or []
    schemas = datasets.get("settings_schemas", Dataset("unknown", [])).data or []
    objects = datasets.get("settings_objects", Dataset("unknown", [])).data or []
    slos = datasets.get("slos", Dataset("unknown", [])).data or []
    synthetic = datasets.get("synthetic_monitors", Dataset("unknown", [])).data or []

    by_type = Counter(entity_type(e) for e in entities)
    by_category = Counter(categorize_entity(entity_type(e)) for e in entities)
    health = defaultdict(Counter)
    for entity in entities:
        health[entity_type(entity)][str(entity.get("properties", {}).get("healthState", entity.get("healthState", "UNKNOWN")))] += 1

    tagged = sum(1 for e in entities if tag_count(e) > 0)
    zone_assigned = sum(1 for e in entities if e.get("managementZones"))
    root_caused = sum(1 for p in problems if p.get("rootCauseEntity") or p.get("rootCauseEntityId"))
    closed = sum(1 for p in problems if str(p.get("status", "")).upper() == "CLOSED")
    active = len(problems) - closed
    schema_ids = [str(s.get("schemaId", "")).lower() for s in schemas]
    object_schema_ids = [str(o.get("schemaId", "")).lower() for o in objects]
    configuration_text = " ".join(schema_ids + object_schema_ids)

    def available(name: str) -> bool:
        return datasets.get(name, Dataset("unknown")).status == "available"

    dimensions: dict[str, dict[str, Any]] = {}

    coverage_score = None
    if available("entities"):
        category_breadth = len([v for v in by_category.values() if v > 0])
        coverage_score = min(5.0, round(category_breadth / 7 * 3 + min(len(entities), 100) / 100 * 2, 2))
    dimensions["coverage"] = dimension(coverage_score, {
        "entities": len(entities), "entityTypes": len(by_type), "categories": dict(by_category)
    }, recommendation(coverage_score, "Validate OneAgent/ActiveGate coverage for entity categories that are absent or unexpectedly small."))

    telemetry_score = None
    if available("metrics"):
        metric_names = " ".join(str(m.get("metricId", m.get("metricKey", ""))).lower() for m in metrics)
        signals = {
            "infrastructureMetrics": any(x in metric_names for x in ("host.", "process.", "cloud.")),
            "kubernetesMetrics": "kubernetes" in metric_names or "cloud.application" in metric_names,
            "serviceMetrics": "service." in metric_names,
            "applicationMetrics": "apps." in metric_names or "application." in metric_names,
            "databaseMetrics": "database" in metric_names,
        }
        telemetry_score = round(sum(signals.values()) / len(signals) * 5, 2)
    else:
        signals = {}
    dimensions["telemetry"] = dimension(telemetry_score, {"metricDefinitions": len(metrics), "detectedSignals": signals}, recommendation(telemetry_score, "Expand telemetry beyond infrastructure metrics to logs, traces, services, applications, and databases."))

    detection_inputs = [available("problems"), available("alerting_profiles"), available("notifications")]
    detection_score = None if not any(detection_inputs) else round((2 if available("problems") else 0) + (1.5 if (datasets.get("alerting_profiles", Dataset("x", [])).data or []) else 0) + (1.5 if (datasets.get("notifications", Dataset("x", [])).data or []) else 0), 2)
    dimensions["detection"] = dimension(detection_score, {"problems30d": len(problems), "activeProblems": active, "alertingProfiles": len(datasets.get("alerting_profiles", Dataset("x", [])).data or []), "notifications": len(datasets.get("notifications", Dataset("x", [])).data or [])}, recommendation(detection_score, "Configure actionable alerting profiles and notification integrations, then tune recurring noisy problems."))

    reliability_score = None if not available("slos") and not available("synthetic_monitors") else min(5.0, round(min(len(slos), 10) / 10 * 2.5 + min(len(synthetic), 10) / 10 * 2.5, 2))
    dimensions["reliability"] = dimension(reliability_score, {"slos": len(slos), "syntheticMonitors": len(synthetic)}, recommendation(reliability_score, "Create user-journey SLOs and synthetic tests for critical applications and APIs."))

    rca_score = None if not available("problems") else round((root_caused / len(problems) * 4 if problems else 0) + (1 if any("service" in k.lower() for k in by_type) else 0), 2)
    dimensions["rca"] = dimension(rca_score, {"problems": len(problems), "withRootCause": root_caused, "rootCauseCoveragePct": pct(root_caused, len(problems))}, recommendation(rca_score, "Improve topology and trace coverage so more detected problems include a defensible root-cause entity."))

    automation_terms = ("automation", "workflow", "notification", "problem")
    automation_objects = sum(1 for x in object_schema_ids if any(t in x for t in automation_terms))
    remediation_score = None if not available("settings_objects") else min(5.0, round(automation_objects / 5 * 5, 2))
    dimensions["remediation"] = dimension(remediation_score, {"automationRelatedSettings": automation_objects, "note": "Workflow execution history may require Platform API evidence."}, recommendation(remediation_score, "Add guarded remediation workflows with retry limits, approval where needed, and post-action health verification."))

    governance_score = None if not available("entities") else round((pct(tagged, len(entities)) / 100 * 2.5) + (pct(zone_assigned, len(entities)) / 100 * 1.5) + (1 if available("settings_objects") and len(objects) > 0 else 0), 2)
    dimensions["governance"] = dimension(governance_score, {"taggedEntitiesPct": pct(tagged, len(entities)), "managementZoneAssignedPct": pct(zone_assigned, len(entities)), "settingsObjects": len(objects)}, recommendation(governance_score, "Standardize ownership, environment, application, criticality, and cost-center tags across monitored entities."))

    operational_score = None if not available("problems") else min(5.0, round((closed / len(problems) * 3 if problems else 0) + (root_caused / len(problems) * 2 if problems else 0), 2))
    dimensions["operations"] = dimension(operational_score, {"closedProblems": closed, "activeProblems": active, "closedPct": pct(closed, len(problems))}, recommendation(operational_score, "Track MTTA, MTTR, alert recurrence, and remediation success externally to demonstrate operational improvement."))

    weights = {"coverage": .15, "telemetry": .15, "detection": .15, "reliability": .15, "rca": .15, "remediation": .10, "governance": .10, "operations": .05}
    scored_weight = sum(weights[k] for k, v in dimensions.items() if v["score"] is not None)
    overall = round(sum(v["score"] * weights[k] for k, v in dimensions.items() if v["score"] is not None) / scored_weight, 2) if scored_weight else None
    completeness = round(sum(1 for d in datasets.values() if d.status == "available") / max(len(datasets), 1) * 100, 1)

    recommendations = []
    for name, value in dimensions.items():
        if value["recommendation"]:
            recommendations.append({"priority": priority(value["score"]), "dimension": name, "finding": summarize_finding(name, value), "recommendedAction": value["recommendation"]})
    recommendations.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["priority"]])

    diagnostics = {name: {"status": ds.status, "endpoint": ds.endpoint, "error": ds.error, "pages": ds.pages} for name, ds in datasets.items()}
    return {
        "metadata": {"collectorVersion": VERSION, "generatedAt": datetime.now(timezone.utc).isoformat(), "scope": "tenant-wide" if not args.management_zone and not args.tag else "filtered", "managementZone": args.management_zone, "tag": args.tag, "period": {"from": args.from_time, "to": args.to_time}},
        "overallScore": overall, "level": maturity_level(overall), "evidenceCompleteness": completeness,
        "inventory": {"totalEntities": len(entities), "byCategory": dict(sorted(by_category.items())), "byType": dict(sorted(by_type.items())), "healthByType": {k: dict(v) for k, v in sorted(health.items())}},
        "dimensions": dimensions, "recommendations": recommendations, "apiDiagnostics": diagnostics,
    }


def dimension(score: float | None, evidence: dict[str, Any], rec: str | None) -> dict[str, Any]:
    return {"score": score, "status": "unknown" if score is None else "scored", "evidence": evidence, "recommendation": rec}


def recommendation(score: float | None, text: str) -> str | None:
    return text if score is not None and score < 4 else None


def pct(value: int, total: int) -> float:
    return round(value / total * 100, 1) if total else 0.0


def priority(score: float | None) -> str:
    if score is None or score < 2.5:
        return "high"
    return "medium" if score < 4 else "low"


def maturity_level(score: float | None) -> str:
    if score is None:
        return "Unknown"
    return "Initial" if score <= 1 else "Visible" if score <= 2 else "Defined" if score <= 3 else "Managed" if score <= 4 else "Optimizing"


def summarize_finding(name: str, value: dict[str, Any]) -> str:
    score = value["score"]
    return f"{name.title()} maturity scored {score:.1f} of 5 based on currently available API evidence." if score is not None else f"{name.title()} could not be scored because required API evidence was unavailable."


def write_outputs(assessment: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "assessment.json").write_text(json.dumps(assessment, indent=2), encoding="utf-8")
    with (output / "inventory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["entity_type", "category", "count", "healthy", "unhealthy", "unknown"])
        health = assessment["inventory"]["healthByType"]
        for entity_type, count in assessment["inventory"]["byType"].items():
            states = health.get(entity_type, {})
            writer.writerow([entity_type, categorize_entity(entity_type), count, states.get("HEALTHY", 0), sum(v for k, v in states.items() if k not in ("HEALTHY", "UNKNOWN")), states.get("UNKNOWN", 0)])
    with (output / "recommendations.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["priority", "dimension", "finding", "recommendedAction"])
        writer.writeheader(); writer.writerows(assessment["recommendations"])
    (output / "report.html").write_text(render_html(assessment), encoding="utf-8")


def render_html(a: dict[str, Any]) -> str:
    esc = lambda value: html.escape(str(value))
    score = a["overallScore"]
    score_text = "—" if score is None else f"{score:.1f}"
    cards = "".join(f'<article><span>{esc(name.title())}</span><b>{"—" if d["score"] is None else f"{d["score"]:.1f}"}</b><small>{esc(d["status"])}</small></article>' for name, d in a["dimensions"].items())
    inventory = "".join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in a["inventory"]["byCategory"].items())
    recs = "".join(f'<tr><td><i class="{esc(r["priority"])}">{esc(r["priority"])}</i></td><td>{esc(r["dimension"].title())}</td><td>{esc(r["recommendedAction"])}</td></tr>' for r in a["recommendations"])
    diagnostics = "".join(f'<tr><td>{esc(k)}</td><td><i class="{esc(v["status"])}">{esc(v["status"])}</i></td><td>{esc(v["error"] or "")}</td></tr>' for k, v in a["apiDiagnostics"].items())
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Dynatrace Maturity Assessment</title><style>
body{{margin:0;background:#f4f6f8;color:#17202a;font:14px Arial,sans-serif}}main{{max-width:1120px;margin:auto;padding:42px 22px}}header{{background:linear-gradient(125deg,#301c64,#5b2ca0 60%,#1496ff);color:#fff;padding:34px;border-radius:18px;display:flex;justify-content:space-between;align-items:end}}h1{{margin:5px 0;font-size:30px}}header p{{margin:5px 0;opacity:.8}}.hero-score{{text-align:center}}.hero-score b{{display:block;font-size:54px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}}article,section{{background:#fff;border:1px solid #e0e5ea;border-radius:14px;padding:20px}}article span,article small{{display:block;color:#65727e}}article b{{display:block;font-size:30px;margin:10px 0;color:#54279b}}section{{margin:16px 0}}h2{{margin-top:0}}table{{width:100%;border-collapse:collapse}}td,th{{padding:11px;border-bottom:1px solid #edf0f2;text-align:left}}i{{font-style:normal;padding:4px 8px;border-radius:12px;background:#e8edf2}}i.high,i.error,i.forbidden{{background:#ffe2e0;color:#a72a22}}i.medium{{background:#fff0c9;color:#805d00}}i.available{{background:#dff5e8;color:#14713b}}.meta{{color:#65727e;margin-top:14px}}@media(max-width:760px){{.cards{{grid-template-columns:repeat(2,1fr)}}header{{display:block}}.hero-score{{text-align:left;margin-top:20px}}}}
</style></head><body><main><header><div><small>DYNATRACE · TENANT-WIDE ASSESSMENT</small><h1>Observability maturity</h1><p>{esc(a["level"])} maturity · {a["evidenceCompleteness"]}% evidence completeness</p></div><div class="hero-score"><b>{score_text}</b><span>out of 5</span></div></header><div class="cards">{cards}</div><section><h2>Monitored estate</h2><p><b>{a["inventory"]["totalEntities"]}</b> entities across <b>{len(a["inventory"]["byType"])}</b> entity types</p><table><tbody>{inventory}</tbody></table></section><section><h2>Prioritized recommendations</h2><table><thead><tr><th>Priority</th><th>Dimension</th><th>Action</th></tr></thead><tbody>{recs or '<tr><td colspan="3">No recommendations generated.</td></tr>'}</tbody></table></section><section><h2>API evidence diagnostics</h2><table><thead><tr><th>Dataset</th><th>Status</th><th>Detail</th></tr></thead><tbody>{diagnostics}</tbody></table></section><p class="meta">Generated {esc(a["metadata"]["generatedAt"])} by collector {VERSION}. Unknown evidence is not scored as zero.</p></main></body></html>'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out"))
    parser.add_argument("--from-time", default="now-30d")
    parser.add_argument("--to-time", default="now")
    parser.add_argument("--management-zone")
    parser.add_argument("--tag")
    parser.add_argument("--page-size", type=int, default=500, choices=range(1, 501), metavar="1..500")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--save-fixture", type=Path)
    parser.add_argument("--setup-credentials", action="store_true", help="securely save tenant credentials for future runs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.setup_credentials:
        setup_credentials()
        return 0
    datasets = load_fixture(args.fixture) if args.fixture else collect_live(args)
    if args.save_fixture:
        args.save_fixture.write_text(json.dumps({"datasets": dataset_json(datasets)}, indent=2), encoding="utf-8")
    assessment = calculate_assessment(datasets, args)
    write_outputs(assessment, args.output)
    print(f"Assessment complete: {args.output.resolve()}")
    print(f"Score: {assessment['overallScore']} / 5 ({assessment['level']}); evidence completeness: {assessment['evidenceCompleteness']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
