"""Retrieve public vendor licensing references and list rates without credentials."""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SOURCES = {
    "dynatrace": ("Dynatrace DPS rate card", "https://www.dynatrace.com/pricing/rate-card/"),
    "datadog": ("Datadog US pricing comparison", "https://www.datadoghq.com/pricing/list/"),
    "zabbix": ("Zabbix software license", "https://www.zabbix.com/license"),
    "splunk": ("Splunk Platform pricing", "https://www.splunk.com/en_us/products/pricing.html"),
    "solarwinds": ("SolarWinds Observability Self-Hosted pricing", "https://www.solarwinds.com/hybrid-cloud-observability/pricing"),
}


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def page_text(raw):
    parser = PageText()
    parser.feed(raw)
    return " ".join(" ".join(parser.parts).split())


def fetch_page(url):
    # Public pages only; never use tool API credentials for price lookup.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MultiToolInventory/1.0)", "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(3_000_001)
        if len(data) > 3_000_000:
            raise ValueError("Pricing page exceeds size limit")
        return data.decode("utf-8", errors="replace")


def parse_vendor(tool, text):
    rows = []
    def add(plan, unit, rate, basis, notes=""):
        rows.append({"tool": tool, "plan": plan, "billing_unit": unit, "unit_rate": rate,
                     "currency": "USD" if rate is not None else "", "price_basis": basis, "notes": notes})
    if tool == "dynatrace":
        # Match the rate-card capability, the exact dollar value and its billing
        # unit together. Never substitute the marketing monthly-equivalent price.
        patterns = {
            "full_stack": (r"Full-Stack Monitoring\s+\$\s*([\d.]+)\s+per memory-GiB-hour", "GiB-hour"),
            "infrastructure": (r"Infrastructure Monitoring\s+\$\s*([\d.]+)\s+per hour for any size host", "host-hour"),
            "foundation_and_discovery": (r"Foundation\s*&\s*Discovery\s+\$\s*([\d.]+)\s+per hour for any size host", "host-hour"),
        }
        if not re.search(r"List Price\s*\(USD\)", text, re.I):
            return []
        for plan, (pattern, unit) in patterns.items():
            matches = set(re.findall(pattern, text, re.I))
            if len(matches) == 1:
                add(plan, unit, float(matches.pop()), "public_list_price", "DPS list rate; excludes contract discounts, commitments, taxes and other capabilities.")
    elif tool == "datadog":
        for plan in ("Pro", "Enterprise", "DevSecOps Pro", "DevSecOps Enterprise"):
            pattern = r"Infrastructure " + re.escape(plan) + r"\s+Per infra host, per month\s+(?:Per infra host, per month\s+)?\$\s*([\d.]+)\s+\$\s*([\d.]+)\s+\$\s*([\d.]+)"
            matches = set(re.findall(pattern, text, re.I))
            if len(matches) == 1:
                for basis, value in zip(("annual_commitment", "month_to_month", "on_demand"), matches.pop()):
                    add("infrastructure_" + plan.lower().replace(" ", "_"), "host-month", float(value), "public_list_price_" + basis,
                        "US list rate only. Inventory host count is not historical billable host usage; no cost calculated.")
    elif tool == "solarwinds":
        for plan in ("Essentials", "Advanced", "Premier"):
            heading = {"Essentials": "Essentials Core hybrid infrastructure monitoring", "Advanced": "Advanced Essentials plus", "Premier": "Premier The most comprehensive option"}[plan]
            pattern = re.escape(heading) + r"\s+[^$]{0,350}?Starts at:\s*\$\s*([\d.]+)\s+per node per month"
            matches = set(re.findall(pattern, text, re.I))
            if len(matches) == 1:
                add(plan.lower(), "node-month", float(matches.pop()), "public_starting_price",
                    "Multi-year contracts billed annually. Edition and weighted billable-node counts must be verified; no cost calculated.")
    elif tool == "zabbix":
        if "Zabbix is Open Source Software" in text and "AGPL" in text:
            add("self_hosted_open_source", "software-license", 0, "open_source_license",
                "Base software license only. Support subscriptions, Zabbix Cloud, hosting and operations are not free or included; no total cost calculated.")
    elif tool == "splunk":
        if "Splunk Enterprise" in text and re.search(r"ingest|workload", text, re.I) and re.search(r"Get a quote|Contact a Splunk pricing expert", text, re.I):
            add("enterprise_or_cloud_platform", "contract_specific", None, "quote_required",
                "Connector queries the Splunk management API. Observability Cloud host pricing does not apply automatically; ingest/workload pricing requires the actual contract and usage.")
    return rows


def collect_vendor_pricing(fetch=fetch_page, enabled=True):
    def lookup(item):
        tool, (title, url) = item
        reference = {"tool": tool, "title": title, "url": url, "retrieved_at": "", "status": "not_fetched"}
        rows = []
        if enabled:
            try:
                text = page_text(fetch(url))
                reference["retrieved_at"] = datetime.now(timezone.utc).isoformat()
                rows = parse_vendor(tool, text)
                reference["status"] = "available" if rows else "unparsed_or_no_public_price"
                for row in rows:
                    row.update(reference_url=url, retrieved_at=reference["retrieved_at"])
            except Exception as exc:
                reference["status"] = "lookup_failed: " + type(exc).__name__
        return reference, rows
    # Vendor pages are independent; one slow site must not delay each later one.
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lookup, SOURCES.items()))
    return {"references": [ref for ref, _ in results], "prices": [row for _, rows in results for row in rows]}


def dynatrace_pricing(config, catalog):
    """Contract overrides win. Public USD rates never mix with another currency."""
    if not isinstance(config, dict):
        raise ValueError("pricing must be an object")
    selected = config.get("dynatrace", config if "rates" in config or "currency" in config else {})
    if not isinstance(selected, dict):
        raise ValueError("pricing.dynatrace must be an object")
    currency = selected.get("currency") or "USD"
    rates = selected.get("rates", {})
    if not isinstance(rates, dict):
        raise ValueError("pricing.dynatrace.rates must be an object")
    resolved, metadata = {}, {}
    if currency == "USD":
        for row in catalog.get("prices", []):
            if row["tool"] == "dynatrace" and row["price_basis"] == "public_list_price":
                resolved[row["plan"]] = row["unit_rate"]
                metadata[row["plan"]] = {"price_source": "vendor_public_list", "price_reference": row["reference_url"], "price_retrieved_at": row["retrieved_at"]}
    for mode, rate in rates.items():
        resolved[mode] = rate
        metadata[mode] = {"price_source": "configured_contract_rate", "price_reference": "credentials.json:pricing.dynatrace.rates", "price_retrieved_at": ""}
    return {"currency": currency, "rates": resolved, "rate_metadata": metadata}


def write_pricing_outputs(catalog, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    columns = ["tool", "plan", "billing_unit", "unit_rate", "currency", "price_basis", "notes", "reference_url", "retrieved_at"]
    with (output / "vendor_pricing.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, columns)
        writer.writeheader()
        writer.writerows(catalog["prices"])
    (output / "vendor_pricing.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    text = "# References\n\nOfficial vendor pricing and licensing sources. Public prices are estimates, not your invoice.\n\n"
    for ref in catalog["references"]:
        text += f"- [{ref['title']}]({ref['url']}) — {ref['status']}; retrieved: {ref['retrieved_at'] or 'not fetched'}.\n"
    (output / "references.md").write_text(text, encoding="utf-8")
