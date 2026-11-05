# Multi-tool Observability Collector

## One configuration file for all tools

The collector reads `credentials.json` beside the scripts. Python 3.10+ is
required; there are no third-party dependencies. Create it once, then enter your URLs and full credential values:

```bash
python3 credential_config.py --init
# Open credentials.json in your editor and fill in the tools you use.
python3 multi_tool_inventory.py --output ./multi-out
```

The initializer creates a user-only file and refuses to overwrite an existing one.
The template already includes your Dynatrace Environment API and platform URLs.
Leave unused tools and secret fields blank. Do not use token IDs in place of token
values. `credentials.json` is ignored by Git and is never included in reports.

| Section | Configuration fields |
| --- | --- |
| `dynatrace` | `environment_url`, `api_token`; optional Grail `platform_url`, `platform_token` |
| `datadog` | `site`, `api_key`, `application_key`, `environment` |
| `zabbix` | `url`, `api_token` |
| `splunk` | `url` (management API), `api_token` |
| `solarwinds` | `url` (SWIS API), `username`, `password` |

Environment variables override file values. On Linux/macOS the file must have
permissions `600`; the initializer sets this automatically. To use a different
file, pass `--credentials /path/to/credentials.json` to the collector.
Fixture runs do not require credentials.

## Cross-tool host inventory

`multi_tool_inventory.py` creates a read-only, host-level inventory across Dynatrace,
Datadog, Zabbix, Splunk, and SolarWinds Observability Self-Hosted (Orion/SWIS).
Run a live collection or try the included sample data:

```bash
python3 multi_tool_inventory.py --output ./multi-out
python3 multi_tool_inventory.py --fixture multi-tool-fixture.json --output ./demo-multi-out
```

The inventory command reads `credentials.json` by default. The repository
contains only `credentials.example.json`, with blank secret fields. Use
`--credentials /path/to/credentials.json` for another file. Environment variables
override saved values. The file is ignored by Git and must have user-only access
(`chmod 600 credentials.json` on Linux/macOS). Leave unused tool sections blank;
their status will show missing credentials in `connector_status.csv`. The file is
never copied into the inventory output.

Configure only the connectors you have access to. Each connector runs independently;
missing credentials or API errors appear in `connector_status.csv`.

| Tool | Environment variables | Read-only source |
| --- | --- | --- |
| Dynatrace | `DT_ENV_URL`, `DT_API_TOKEN` | Entities and host metric data |
| Datadog | `DD_API_KEY`, `DD_APP_KEY`, optional `DD_SITE` (default `datadoghq.com`) | Hosts and metric timeseries |
| Zabbix | `ZBX_URL`, `ZBX_TOKEN` | Enabled hosts and supported items |
| Splunk | `SPLUNK_URL` (management API URL), `SPLUNK_TOKEN` | Host/sourcetype pairs with events in the last 24 hours |
| SolarWinds | `SW_URL` (SWIS URL), `SW_USER`, `SW_PASSWORD` | Nodes, volumes, and optional SAM applications |

The output files are:

- `host_inventory.csv`: one consolidated row per matched host, with tool presence and
  observed full-stack, CPU, memory, disk, application, Kubernetes, and cloud signals.
- `tool_host_details.csv`: one row per tool's host record, including source ID and
  JSON evidence for every detected signal.
- `host_inventory.json`: both tables' underlying observations and diagnostics.
- `connector_status.csv`: connector availability and errors.
- `applications.csv`: monitored Dynatrace entities, Datadog APM services, SolarWinds
  SAM applications, and Splunk application-log sourcetypes (clearly marked inferred).
  Zabbix does not expose a universal application object in current API versions.
- `dashboards.csv`: dashboard IDs and names visible through each tool's API.
- `metrics.csv`: metric keys or configured pollers, with tool, host when available,
  and collection status.
- `service_relationships.csv`: Dynatrace entity links, Datadog APM calls,
  Zabbix service-tree links, and SolarWinds SAM application-to-node links.
- `alerts.csv`: Dynatrace problems, Datadog monitor groups, Zabbix trigger events,
  Splunk saved-search alert rules, and SolarWinds alert history.
- `mttr.csv`: mean and median resolution minutes by tool and measurement type,
  with the count of completed alert pairs used. Open alerts are excluded.
- `trace_availability.csv`: explicit Datadog APM trace status and optional
  Dynatrace Grail span evidence. Unverified trace status remains unknown.

`yes` means the collector found evidence; `no` means a successfully queried tool did
not return that host. `unknown` means it could not verify the tool or signal. Full-stack
is only reported when a tool explicitly exposes that mode. Hosts merge on exact,
case-insensitive hostname or explicit alias/IP matches. If identity is ambiguous, they
remain separate; check the detail table before treating a missing tool as a gap.
Datadog's host list defaults to recently active hosts, and metric, Splunk, and Dynatrace
signal queries use a 24-hour lookback. Thus this is a snapshot of evidence, not a
guarantee that an unmarked signal has never been configured.
Metric status is `active_24h` where the API exposes recent data or a last-written
timestamp. SolarWinds pollers are marked `configured_poller` because this API query
does not verify fresh samples. Dashboards are tool-wide and have no host association.
Datadog APM services and Splunk metric names are tool-wide; Datadog active metrics
and Zabbix items are listed per host. Dashboard access may require extra permissions:
Dynatrace `ReadConfig`, Datadog `dashboards_read`, and equivalent read roles for the
other tools. Datadog metrics and APM need `metrics_read` and `apm_read`.
Datadog alerts need `monitors_read`. Datadog relationship queries use `DD_ENV`
(default `prod`). For direct Dynatrace trace evidence, fill in `platform_url`
(the `https://YOUR_ENV.apps.dynatrace.com` URL) and `platform_token` (an OAuth
bearer token with Grail span-query access) in `credentials.json`. Without them,
Dynatrace trace status is unknown. Span queries use a 24-hour window. Dynatrace
problems, Zabbix events, and SolarWinds history use a 30-day alert window;
Datadog and Splunk return monitor or rule snapshots. Datadog's
`latest_monitor_recovery` in `mttr.csv` uses
only the latest trigger/resolution pair per monitor group, so it is not a
historical incident MTTR. Splunk saved-search rules have no resolution time in
this connector.

## Files

- `multi_tool_inventory.py`: command-line entry point, host collection and output.
- `catalog_inventory.py`: application, dashboard and metric collection.
- `operational_inventory.py`: relationships, alerts, MTTR and trace evidence.
- `dynatrace_client.py`: shared Dynatrace HTTP client and pagination.
- `credential_config.py`: single-file configuration loader and initializer.
- `licensing_usage.py`: measured DPS consumption and cost estimation.
- `vendor_pricing.py`: official pricing lookup and reference exports.
- `credentials.example.json`: configuration template with blank secrets.
- `multi-tool-fixture.json`: offline sample data.
- `tests/`: unit tests for configuration, inventory and API behavior.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 multi_tool_inventory.py --fixture multi-tool-fixture.json --output ./demo-multi-out
```

## Host status and licensing usage

`host_inventory.csv`, `tool_host_details.csv`, and `host_inventory.json` include:

- `entity_id` and `entity_ids`: Dynatrace host entity ID and all matched tool IDs.
- `host_status`: Dynatrace's last reported `properties.state`, such as `running`.
  This is not a live OS power-state check. Inspect `last_seen` before treating a
  historical state as current; missing state stays `unknown`, never `offline`.
- `monitoring_mode`: the reported OneAgent monitoring mode (for example `FULL_STACK`).
- `memory_gib`: currently reported physical RAM, used only for explicit estimates.
- `host_hours`: measured Infrastructure and Foundation & Discovery host-hours.
- `memory_gib_hours_consumption`: measured Full-Stack memory-GiB-hours over the usage period,
  including historical billing entities. This is consumption, not current RAM or estimated runtime.
- `full_stack_gib_hours`: retained compatibility column with the same value. Do not add
  the two columns together; they represent the same consumption.
- `full_stack_host_hours_estimate`: Full-Stack GiB-hours divided by current host RAM.
  This is an estimate: historical RAM changes affect its accuracy. It is not
  measured runtime and is not used to calculate Full-Stack cost.
- `usage_period_start`, `usage_period_end`, `usage_status`, `estimated_cost`, and
  `cost_currency`. Missing usage or prices remain unknown, rather than zero.

`host_inventory.csv` combines inventory and licensing costs in one file, with
per-mode unit rate columns and a `usage_details` JSON column preserving each mode’s
source metric, actual response resolution, unit rate, and estimate basis. It also
retains historical billing entities absent from the current host inventory (their
host name is `unknown`). `record_type` distinguishes `inventory_and_usage`,
`inventory_only`, and historical `usage_only` rows. Historical rows have unknown
current state and tool-presence flags. Each mode's query status is in `connector_status.csv` and
`licensing_diagnostics` in the JSON output. A host can consume multiple modes over
the period; today's monitoring mode does not select its historical billing data.

The default usage lookback is 30 days; change it with:

```bash
python3 multi_tool_inventory.py --output ./multi-out --usage-days 7
```

Consumption comes from Dynatrace DPS `builtin:billing.*.usage_per_host` metrics.
The collector sums reported usage with a requested 15-minute resolution; Dynatrace
may return coarser historical resolution. Usage is not extrapolated across gaps.
API failures are reported as partial coverage; empty results do not prove zero
consumption. These DPS metrics do not provide classic host-unit-hour licensing.

## Vendor pricing and references

Live runs retrieve public licensing and pricing information from official vendor
sites without sending tool credentials to those sites.

| Vendor | Model collected | Reference |
| --- | --- | --- |
| Dynatrace | DPS per GiB-hour or host-hour | [Official rate card](https://www.dynatrace.com/pricing/rate-card/) |
| Datadog | Infrastructure editions per host-month; annual, monthly, on-demand | [US pricing comparison](https://www.datadoghq.com/pricing/list/) |
| Zabbix | Self-hosted base software license; support/hosting separate | [Software license](https://www.zabbix.com/license) |
| Splunk | Platform ingest/workload models; quote required | [Platform pricing](https://www.splunk.com/en_us/products/pricing.html) |
| SolarWinds | Self-Hosted edition starting rates per node-month | [Self-Hosted pricing](https://www.solarwinds.com/hybrid-cloud-observability/pricing) |

New outputs:

- `vendor_pricing.csv` / `vendor_pricing.json`: plans, billing units, public USD
  rates when available, price basis, official URL and retrieval timestamp.
- `references.md`: official vendor websites, lookup status and retrieval time.
- The inventory JSON includes `references` and `vendor_pricing`.
- Usage and inventory CSVs include `price_source` and `price_reference`.
  Detailed usage rows also include `price_retrieved_at`.

Dynatrace's public DPS rates are applied automatically to measured billing usage.
These are list-price estimates, not invoice amounts. Other vendors' prices are
reference information: snapshot host counts do not establish historical monthly
billable usage, edition, contract term or weighted billable nodes. The Splunk
connector uses the management API, so Observability Cloud host rates are not
applied to its log data. A zero Zabbix base-license fee excludes support, hosting
and operations.

If a site is unavailable or its layout, unit or currency cannot be parsed with
confidence, its rates remain unknown. No old or hardcoded rates are substituted.
Fixture runs do not access websites; their references are marked `not_fetched`.

The shared configuration defaults to automatic public rates:

```json
"pricing": {
  "dynatrace": {
    "currency": "USD",
    "rates": {}
  }
}
```

Optional contract overrides go inside `rates`, for example `"full_stack": 0.008`.
This is an example, not a vendor quote. Full-Stack uses GiB-hours; `infrastructure`
and `foundation_and_discovery` use host-hours. Overrides take precedence and are
labeled `configured_contract_rate`. Public USD rates never mix with another
currency; provide the required contract rates when using a different currency.
Legacy flat `pricing.currency` / `pricing.rates` files remain supported.

Costs exclude commitments, volume discounts, taxes and other capabilities.
Usage definitions: [Dynatrace built-in billing metrics](https://docs.dynatrace.com/docs/analyze-explore-automate/metrics-classic/built-in-metrics).

The separate `host_usage.csv` is no longer generated and is removed when an
existing output directory is regenerated. Use `host_inventory.csv` for the full
inventory, consumption and cost report. `tool_host_details.csv` retains per-tool
monitoring evidence only.

## Alert types, recent occurrences and affected hosts

The combined `host_inventory.csv` includes:

- `alert_type`: the categories of recently started, explicitly host-linked alerts.
- `recent_occurrences_count`: distinct alert/problem IDs started in the last 30 days.
- `affected_host_entity_ids`: affected host IDs used for the row's associations.
- `latest_occurrence_timestamp`: the most recent matching start time in UTC ISO 8601.
- `alert_window_start`, `alert_window_end`, `alert_summary_status` and `alert_details`.
  The details JSON groups counts and latest times by tool, category and alert title.

Dynatrace `alert_type` is the Problems API `severityLevel` category (for example
`AVAILABILITY`, `PERFORMANCE` or `CUSTOM_ALERT`), not an underlying event type.
Problem IDs are deduplicated. One multi-host problem counts once for each explicit
host. Counts are distinct problem occurrences, not notification deliveries,
reopened transitions, or an inferred count of underlying events. Problems that
started before the window do not count, even if still open.

Host associations use explicit HOST entities from `affectedEntities` and
`impactedEntities`. Service-only and environment-only problems are retained in
`alerts.csv` with no assigned host, rather than assigning every related host.
Current topology is not used as proof of a host being affected by a past problem.
A zero count means no matching explicitly host-linked records, not proof that the
host's services had no alerts. Failed queries return unknown counts. Historical
billing hosts can receive alerts through their exact entity ID too.

The detailed `alerts.csv` retains the same type, affected IDs, grouped recent
occurrence count and latest timestamp, along with each individual start/end time
and `host_association_basis`. Its grouped count is for the same tool, category,
title and affected-host set; do not sum repeated grouped counts across records.
Datadog monitor snapshots and Splunk alert rules do not provide full occurrence
history, so their recent counts stay unknown. Name-only associations for other
tools require an exact unambiguous host identity from that same tool.

Source: [Dynatrace Problems API fields](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problems-list).
