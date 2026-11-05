"""Dynatrace DPS usage with public-vendor or configured-contract rate provenance."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

METRICS = {
    "full_stack": ("builtin:billing.full_stack_monitoring.usage_per_host", "GiB-hour"),
    "infrastructure": ("builtin:billing.infrastructure_monitoring.usage_per_host", "host-hour"),
    "foundation_and_discovery": ("builtin:billing.foundation_and_discovery.usage_per_host", "host-hour"),
}


def collect_usage(client, observations, days=30, pricing=None):
    pricing = {} if pricing is None else pricing
    if not isinstance(pricing, dict):
        raise ValueError("pricing must be an object")
    rates = pricing.get("rates", {})
    currency = pricing.get("currency", "")
    if not isinstance(rates, dict) or not isinstance(currency, str):
        raise ValueError("pricing requires a currency string and rates object")
    for mode, rate in rates.items():
        if mode not in METRICS or isinstance(rate, bool) or not isinstance(rate, (float, int)) or not math.isfinite(rate) or rate < 0:
            raise ValueError("Pricing rates must be nonnegative finite numbers keyed by billing mode")
    if rates and not currency.strip():
        raise ValueError("Set pricing.currency when configuring pricing.rates")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    by_id = {o.source_id: o for o in observations if o.tool == "dynatrace"}
    rows, diagnostics = [], {}
    for mode, (metric, unit) in METRICS.items():
        try:
            payload = client.get("/api/v2/metrics/query", {
                "metricSelector": metric + ':splitBy("dt.entity.host"):sum',
                "from": int(start.timestamp() * 1000), "to": int(end.timestamp() * 1000), "resolution": "15m",
            })
            series = [s for result in payload.get("result", []) for s in result.get("data", [])]
            diagnostics[mode] = "available" if series else "no_data"
            if payload.get("warnings"):
                diagnostics[mode] = "partial: " + str(payload["warnings"])
            for item in series:
                entity_id = (item.get("dimensionMap") or {}).get("dt.entity.host") or "unknown"
                values = [v for v in item.get("values", []) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0]
                total = sum(values) if values else None
                host = by_id.get(entity_id)
                memory = host.memory_gib if host else None
                # Full-Stack bills memory-GiB-hours, not host-hours. Never use
                # today's RAM to label a historical estimate as measured usage.
                host_hours = total if unit == "host-hour" else None
                estimated = total / memory if total is not None and unit == "GiB-hour" and memory and memory > 0 else None
                rate = rates.get(mode)
                rows.append({
                    "tool": "dynatrace", "entity_id": entity_id, "host": host.name if host else "unknown",
                    "monitoring_mode": mode, "period_start": start.isoformat(), "period_end": end.isoformat(),
                    "usage_value": round(total, 6) if total is not None else None, "usage_unit": unit,
                    "host_hours": round(host_hours, 6) if host_hours is not None else None,
                    "full_stack_gib_hours": round(total, 6) if total is not None and unit == "GiB-hour" else None,
                    "full_stack_host_hours_estimate": round(estimated, 6) if estimated is not None else None,
                    "estimate_basis": "GiB-hours / current host RAM GiB; historical RAM may differ" if estimated is not None else "",
                    "unit_rate": rate, "currency": currency if rate is not None else "",
                    "estimated_cost": round(total * rate, 6) if total is not None and rate is not None else None,
                    "status": "reported_usage" if total is not None else "no_data",
                    "metric": metric, "resolution": payload.get("resolution", "unknown"),
                    **pricing.get("rate_metadata", {}).get(mode, {
                        "price_source": "configured_contract_rate" if rate is not None else "unknown",
                        "price_reference": "credentials.json:pricing.rates" if rate is not None else "",
                        "price_retrieved_at": "",
                    }),
                })
        except RuntimeError as exc:
            diagnostics[mode] = str(exc)
    return rows, diagnostics


def host_usage_fields(entity_id: str, rows: list[dict[str, Any]], diagnostics=None):
    matched = [r for r in rows if r["entity_id"] == entity_id]
    measured = [r for r in matched if r.get("usage_value") is not None]
    def total(key):
        values = [r[key] for r in measured if r.get(key) is not None]
        return round(sum(values), 6) if values else "unknown"
    # Do not expose a partial sum as total cost if any consumed mode lacks a rate.
    currencies = {r.get("currency") for r in measured}
    issues = [v for v in (diagnostics or {}).values() if v not in ("available", "no_data")]
    cost = total("estimated_cost") if not issues and measured and all(r.get("estimated_cost") is not None for r in measured) and len(currencies) == 1 else "unknown"
    sources = sorted({r.get("price_source", "unknown") for r in measured})
    references = sorted({r.get("price_reference", "") for r in measured} - {""})
    return {
        "price_source": "; ".join(sources) or "unknown",
        "price_reference": "; ".join(references),
        "host_hours": total("host_hours"), "full_stack_gib_hours": total("full_stack_gib_hours"),
        "memory_gib_hours_consumption": total("full_stack_gib_hours"),
        "full_stack_host_hours_estimate": total("full_stack_host_hours_estimate"),
        "usage_period_start": matched[0]["period_start"] if matched else "unknown",
        "usage_period_end": matched[0]["period_end"] if matched else "unknown",
        "usage_status": "partial" if issues else "reported_usage" if measured else "no_data",
        "estimated_cost": cost, "cost_currency": next(iter(currencies)) if cost != "unknown" else "",
    }
