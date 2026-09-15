# Dynatrace Observability Maturity Collector

## Cross-tool host inventory

`multi_tool_inventory.py` creates a read-only, host-level inventory across Dynatrace,
Datadog, Zabbix, Splunk, and SolarWinds Observability Self-Hosted (Orion/SWIS).
Run it separately from the Dynatrace maturity assessment:

```bash
# Copy credentials.example.json to credentials.json, then fill in the tools you use.
cp credentials.example.json credentials.json
chmod 600 credentials.json
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

The new output files are:

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

Read-only, tenant-wide inventory and maturity assessment for Dynatrace. It discovers everything visible to the supplied token rather than targeting one cluster.

## Outputs

- `assessment.json` — complete evidence, scores, gaps, and API diagnostics
- `inventory.csv` — entity counts by type and health state
- `recommendations.csv` — prioritized improvements
- `report.html` — self-contained executive report

Missing permissions are reported as `unknown`; they do not become false zero scores.
The collector first reads the tenant's entity-type catalog and then paginates every type, because Dynatrace's monitored-entities endpoint requires an entity selector.

## Quick start

Python 3.10+ is required. There are no third-party dependencies.

PowerShell:

```powershell
$env:DT_ENV_URL = "https://YOUR_ENVIRONMENT.live.dynatrace.com"
$env:DT_API_TOKEN = "YOUR_READ_ONLY_TOKEN"
python collector.py --output ./out
```

Linux/macOS:

```bash
export DT_ENV_URL="https://YOUR_ENVIRONMENT.live.dynatrace.com"
export DT_API_TOKEN="YOUR_READ_ONLY_TOKEN"
python3 collector.py --output ./out
```

For interactive use, credentials can instead be saved once in a user-only file:

```bash
python3 collector.py --setup-credentials
python3 collector.py --output ./out
```

The token is entered without echo and stored at
`~/.config/dynatrace-maturity/credentials.json` with permissions `0600`.
Environment variables take precedence when they are set. Do not commit or share
the credentials file.

For this tenant, confirm in API Explorer whether the Environment API URL is:

```text
https://vri13969.live.dynatrace.com
```

Never commit or paste tokens into configuration files.

## Token permissions

Create a dedicated read-only API token. Enable the permissions available in your tenant that correspond to:

- `entities.read`
- `metrics.read`
- `problems.read`
- `settings.read`
- `slo.read`
- `ReadSyntheticData`
- `ReadConfig` (optional classic configuration coverage)

The collector probes each API independently and records HTTP 401/403 responses as permission gaps.

## Options

```text
--output DIR             Output directory (default: ./out)
--from-time VALUE        Assessment lookback (default: now-30d)
--to-time VALUE          Assessment end (default: now)
--management-zone NAME   Optional tenant inventory filter
--tag TAG                Optional entity tag filter
--page-size NUMBER       Entity page size, max 500 (default: 500)
--fixture FILE           Run from a saved fixture without a tenant/token
--save-fixture FILE      Save raw API responses for repeatable demos
--setup-credentials      Securely save credentials for future runs
```

Filters are optional. With no filter, the collector inventories the complete tenant scope visible to the token.

## Fixture demo

```bash
python collector.py --fixture sample-fixture.json --output ./demo-out
```

Open `demo-out/report.html`.

## Scope and scoring

The report evaluates:

1. Monitoring coverage
2. Telemetry depth
3. Detection and alerting
4. Reliability engineering
5. Root-cause analysis
6. Remediation and automation
7. Governance
8. Operational outcomes

Scores range from 0 to 5. The overall score is a weighted average of dimensions with sufficient evidence. `evidenceCompleteness` shows how much of the assessment could be verified.
