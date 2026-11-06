# Datadog collection methodology

The collector performs GET requests and read-only analytics/search POST requests.
It never changes monitors, tests, applications, instrumentation, or access controls.
All report timestamps and measurement windows use UTC. Host and active metric
discovery use the last 24 hours; events, incidents, RUM and consumption default to
30 days. Each API source has a row in `feature_availability.csv`. Empty, denied,
partial, and unavailable are distinct statuses. An empty API response does not
prove a feature is disabled. The report captures the organization visible to the
configured API/application key pair, not other organizations in the UI account.

## Alerts and incident timing

`alerts.csv` has one row per monitor group, including P1–P5 priority (severity),
current status, owner/team tags, last trigger/recovery timestamps and trigger count.
An author is not silently treated as the assigned owner. Unknown ownership stays
unknown. Monitor rows and groups are not occurrence counts.

`alert_events.csv` contains normalized retained monitor transitions, fetched with
cursor pagination through the Events API. Trigger count is the number of distinct
event IDs entering **Alert** from another explicitly known state, per exact monitor
ID and sorted group tags, within the requested window. It excludes warnings,
no-data transitions and renotifications. Counts describe accessible retained events,
not notifications or lifetime triggers. If event history fails or returns warnings,
trigger counts stay unknown. Zero only means no matching retained transition.

`mttr.csv` separates these measurements (all durations in minutes):

- `latest_monitor_recovery`: last recovery minus last trigger per monitor group,
  requiring both timestamps in chronological order and recovery within the window.
  Only groups currently in OK state contribute a completed monitor recovery.
  This is a sample of latest monitor recoveries, not full incident-history MTTR.
- `incident_mttr`: incident resolved minus incident created.
- `incident_mttd`: incident detected minus customer impact start.
- `incident_internal_response`: incident created minus detected, following
  Datadog's internal-response definition. This is not acknowledgement time.
- `incident_mtta`: explicit acknowledged timestamp minus detected timestamp when
  supplied by a verified incident-system export in report configuration. The API
  alone does not expose this timestamp; no proxy or zero is substituted.

Incident samples are non-test incidents **created in the reporting window**.
MTTR requires a valid resolved timestamp; MTTD can include unresolved incidents
when impact-start and detection are known. Negative, absent and invalid durations
are excluded. Each measurement has its own sample count, arithmetic mean and
median; no samples produce unknown, not zero. Incident and monitor measurements
are never mixed. Open monitor triggers do not produce a completed recovery pair.

## Applications, relationships and traces

Applications combine APM service metadata (all environments), Software Catalog
entities with `includeDiscovered=true`, and legacy service definitions. Sources
remain explicit, so the same service may appear in multiple rows. Catalog entities
can be services, frontends, datastores or systems; their registration does not prove
active tracing. APM's `isTraced`/`isUsm` fields provide runtime feature evidence.
An empty wildcard response is retried against configured/observed environments.
Indexed-span analytics independently discovers services with retained spans over
24 hours. Retained spans do not prove current live ingestion or USM enablement.
Bucket limits and query warnings produce partial coverage.
The collector also reads catalog relations including discovered APM/USM links.
APM dependencies query configured `DD_ENV` and environment tags observed on hosts;
this does not guarantee every environment in the organization is discovered.

`trace_consumption.csv` retains hourly-usage API aggregation for ingested and indexed
spans. Ingested span **bytes are not a span count**. Raw units and usage types are
preserved; bytes also have a decimal GB conversion. `trace_retention.csv` contains
filter configuration, enabled state and sampling rate. This API does not report the
organization's retention duration. Days remain unknown unless explicitly supplied
with a verified source and timestamp in report configuration.

## Dashboards

The list API is offset-paginated and each dashboard is enriched from its detail API
with author, tags, layout type and modification timestamp. The author is labelled
as creator, not an assigned owner. Detail permission/rate-limit errors remain on
the row. Repeated pages are detected and reported as partial coverage.

The custom dashboard API excludes preset integration dashboards. The collector
also reads accessible manual Dashboard Lists and their members, including listed
integration/host presets. Membership is preserved; kind and ID together deduplicate
records. An incomplete list response is partial coverage. Unlisted presets may
remain missing. Optionally supply `expected_dashboard_count` to compare the same
organization and UI filter; a matching count alone is not proof of full identity coverage.

## Host and metric exports

Host Inventory and Total Host Details use identical schemas and tag parsing.
Datadog reports contain Datadog `host_id`, not Dynatrace entity IDs, OneAgent modes,
Full-Stack memory billing or Infrastructure-mode columns. Hostnames are preserved
after trimming whitespace; names are not truncated or guessed. Raw source tags and
aliases remain available. Tag keys are case-insensitive, hyphens normalize to
underscores, values split only at the first colon, and conflicting values are
preserved. Provider inference uses explicit cloud integration metadata only.
Missing region, environment, application ID, and ownership remain unknown.

`metrics.csv` lists unique organization-wide active metric names over 24 hours,
metadata and volume API evidence. The latest non-null queried rollup bucket is
labelled as a last-reported approximation, not raw ingestion time. Query errors
and missing series remain visible. A metric name count is not a billable custom
timeseries count. An integration name or naming prefix is not used to guess custom
billing classification. Per-name custom status is matched to paginated monthly
`usage/top_avg_metrics` evidence across months overlapping the window. Positive
matches record the evidence month; absent names remain unknown. Calendar-month
values may extend beyond the reporting window. Measured custom-timeseries consumption is in
`custom_metric_usage.csv`. Rate-limited enrichments are marked partial.
Missing timestamps are retried with sum queries over 24 hours and then average
queries over a configurable longer window (default seven days). Recovered values
state the actual lookback and remain rollup approximations.

`estimated_usage.csv` separately queries available Datadog estimated-usage metrics
for ingested span bytes/counts, indexed spans, custom timeseries and hosts over
24 hours. Count metrics use `as_count()` buckets; gauges use hourly averages.
These values are labelled estimates and are never substituted for billing usage.
Do not add estimated values to the authoritative usage API measurements.

## RUM and Synthetics

RUM outputs include application configuration, session/view event counts, distinct
session cardinality, browsers, devices and account-level consumption. Aggregations
query retained events within the reporting window; distinct-session cardinality is
approximate and not invoiced sessions. Browser/device buckets can overlap sessions
and must not be summed as unique users. Missing facet values and inactive apps are
not inferred to be unlicensed. RUM aggregate warnings cause partial coverage. An
unfiltered probe additionally checks all event types without application/facet filters.

Synthetics includes configured API/Browser and other test types, subtype, cadence,
locations, devices, status, tags and author. Latest samples over the reporting window give
passed/failed/no-data counts; success rate is passed/(passed+failed)*100. No-data
results are reported separately. These API samples are not guaranteed complete
execution history or SLA measurements. Account-level measured run consumption is
separate; cadence multiplied by locations is not actual billed usage. No tests are
triggered by this collector. Empty/unsupported v2 results fall back to documented
legacy API/Browser result endpoints. Explicit passed booleans or string states
determine success; undocumented numeric statuses remain unclassified. Legacy API
test summaries are limited to 150 results. Scheduling status remains separate.

## Consumption and pricing

`usage.csv` fetches all available hourly product families for the current org with
cursor pagination in UTC calendar-day request slices to avoid long-range API
timeouts. Each daily timeout is retried once. Three consecutive failed slices stop
older requests; any omitted interval is explicitly reported as partial coverage.
The window is aligned to whole UTC hours. API reporting delays
and null measurements are preserved. Recent nulls within the configured billing
lag (default 72 hours) are `billing_pending`; older nulls are `not_reported`.
Null is never zero. Saved-report retries evaluate pending status against the
current time while preserving the original reporting window.
`consumption.csv` groups each distinct product-family/usage-type pair without
adding overlapping editions or component products. Additive bytes/events/session/
run measurements use sums of reported hours; host/container/cardinality gauges use
means of reported hours. Missing hours are not extrapolated. Neither is asserted to
be an invoice aggregation; contracts may use percentiles, high-water marks,
allowances, annual commitments, retention tiers or other rules.

`vendor_pricing.csv` normalizes hosts, containers, log GB, custom metrics, ingested/
indexed spans, RUM sessions and Synthetic API/Browser runs as billing drivers.
Units are comparison bases, not inferred contracted SKUs. Prices remain unknown
until explicit contract/plan data is supplied; old host-license prices are not
applied to unrelated drivers. Optional configuration:

```json
{"pricing":{"datadog":{"drivers":{"log_ingestion":{
  "billing_unit":"GB_ingested", "unit_rate":0.0, "currency":"USD"
}}}}}
```

The zero above is only an example placeholder, not a vendor price. No invoice
estimate is calculated from a snapshot host count or missing usage.

## Verified report inputs and single-file export

Copy `datadog-report.example.json` to `datadog-report.json` or use `--config FILE`.
Optional settings support:

- `host_metadata`: exact hostname/Datadog host ID mappings, filling missing fields
  only and preserving provenance.
- `owners.monitors` and `owners.dashboards`: exact resource IDs mapped to verified
  assigned owners. Authors remain separate.
- `environments`: additional exact environments for APM and dependency queries.
- `incident_timestamps`: `incident_id`, `source`, `created_at` and optional
  `detected_at`, `impact_started_at`, `acknowledged_at`, `resolved_at`. Imported
  incidents must fall in the reporting cohort. Negative/future endpoints are excluded.
- `trace_retention`: positive `days`, nonempty verified `source`, and `verified_at`.
- `metric_lookback_days`, `billing_lag_hours`, `expected_dashboard_count`.

Contract rates continue to use `pricing.datadog.drivers` in credentials.json.
These inputs change reporting only, never Datadog instrumentation, tags, access,
retention, scheduling or paid feature activation.

`--single-file --output report.xlsx` writes only one workbook with all datasets,
summary, findings, methodology and a remediation sheet containing next actions.
`--input-report saved.json --refresh SOURCE ...` retries selected API sources;
`--input-report saved.json --reexport` rebuilds with verified inputs without API calls.
Generated workbooks contain a lossless snapshot in the hidden Report Data sheet.
They can also be used as `--input-report`, retaining null values, source timestamps
and original windows without a companion JSON file.

## Official references

- [Dashboard List members](https://docs.datadoghq.com/api/latest/dashboard-lists/get-items-of-a-dashboard-list/)
- [Custom metric usage by name](https://docs.datadoghq.com/api/latest/usage-metering/get-all-custom-metrics-by-hourly-average/)
- [Indexed-span analytics](https://docs.datadoghq.com/api/latest/spans/aggregate-spans/)

- [Dashboards API](https://docs.datadoghq.com/api/latest/dashboards/get-all-dashboards/)
- [APM services](https://docs.datadoghq.com/api/latest/apm/get-service-list/)
- [Software Catalog entities](https://docs.datadoghq.com/api/latest/software-catalog/get-a-list-of-entities/)
- [Catalog relations](https://docs.datadoghq.com/api/latest/software-catalog/get-a-list-of-entity-relations/)
- [Events search](https://docs.datadoghq.com/api/latest/events/search-events/)
- [Incident timing fields](https://docs.datadoghq.com/api/latest/incidents/get-a-list-of-incidents/)
- [Hourly usage](https://docs.datadoghq.com/api/latest/usage-metering/get-hourly-usage-by-product-family/)
- [Metric volumes](https://docs.datadoghq.com/api/latest/metrics/list-distinct-metric-volumes-by-metric-name/)
- [RUM APIs](https://docs.datadoghq.com/api/latest/rum/)
- [Synthetic result summaries](https://docs.datadoghq.com/api/latest/synthetics/get-a-tests-latest-results/)
- [Public pricing](https://www.datadoghq.com/pricing/list/)
