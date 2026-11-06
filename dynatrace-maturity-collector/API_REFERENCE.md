# Independent collectors and API coverage

`datadog_maturity.py` uses `datadog_client.py`. `dynatrace_maturity.py` uses `dynatrace_client.py`. Neither standalone collector imports the legacy mixed-vendor collectors. Credential loading, spreadsheet writing and timestamp utilities are shared.

## Datadog

Authentication: `DD-API-KEY` and `DD-APPLICATION-KEY`, with the configured Datadog site. Permissions and organization visibility still determine coverage.

The collector uses host inventory, monitor/search and events, dashboards and dashboard lists, Software Catalog/services, metrics metadata and queries, usage metering, spans analytics, RUM applications/analytics and Synthetics APIs. Pagination is endpoint-specific; continuation links must remain on the authenticated API origin. See [DATADOG-METHODOLOGY.md](DATADOG-METHODOLOGY.md) for endpoint paths, formulas, scopes and limitations.

Official reference: https://docs.datadoghq.com/api/latest/

## Dynatrace

Environment routes use `Authorization: Api-Token`; Platform routes use `Authorization: Bearer`. Platform tokens must be supplied separately and must have the appropriate permissions. This implementation never substitutes one credential type for the other.

| Dataset | API | Pagination / interpretation |
|---|---|---|
| Hosts, services, applications, relationships | GET `/api/v2/entities` | `nextPageKey` alone on continuation; FROM/TO directions preserved |
| Metric names and metadata | GET `/api/v2/metrics` | `nextPageKey`; custom classification is a namespace heuristic, not billed usage |
| Native consumption metrics | GET `/api/v2/metrics/query` | Fixed report window; billing metric values retained in native units |
| Classic dashboards | GET `/api/config/v1/dashboards`, `/{id}` | Detail failures retained on each row; unknown modification time stays unknown |
| Modern dashboards | GET `/platform/document/v1/documents` | `filter=type='dashboard'`, `page-size`, `page-key`; accessible documents only |
| Alerts and recovery | GET `/api/v2/problems` | `nextPageKey`; MTTR from closed problem end minus start; one trigger means one problem occurrence |
| Classic synthetic tests | GET `/api/v1/synthetic/monitors`, `/{id}` | Metadata only; scripts and embedded secrets excluded |
| Platform synthetic tests | GET `/platform/synthetic/v1/monitors` | Separate source; no test execution or configuration mutations |
| Classic RUM sessions, browsers, devices, user actions | GET `/api/v1/userSessionQueryLanguage/table` | Aggregate queries; closed sessions; extrapolation retained; grouped cap flagged |
| Traced service span counts and modern RUM session counts | POST `/platform/storage/query/v1/query:execute`, GET `query:poll` | Read-only fixed DQL; synchronous and asynchronous results supported; notifications require completeness review |

Official references:

- https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-entities-list
- https://developer.dynatrace.com/develop/sdks/client-document/v1/
- https://developer.dynatrace.com/develop/sdks/client-query/
- https://docs.dynatrace.com/docs/dynatrace-api/environment-api/rum/user-sessions/table
- https://docs.dynatrace.com/docs/dynatrace-api/environment-api/synthetic/synthetic-monitors/get-all-monitors
- https://developer.dynatrace.com/develop/sdks/client-synthetic/v3/

## Limits and evidence

A successful request proves collection of accessible data, not organization-wide completeness. No dashboard total is declared complete without reconciling source and permission coverage. Empty data is distinguished from missing credentials and request failures.

Dynatrace span ingestion/indexing billing, retention, synthetic execution success/failure histories, modern RUM browser/device/page-view breakdowns and MTTA/MTTD are not inferred from inventory or session counts. These require additional accessible sources and verified timestamps. User actions are not labeled page views, and synthetic browser actions/HTTP requests are not labeled test runs. No cost is inferred without applicable contract rates. The new Dynatrace collector preserves native host properties and tags; it does not yet normalize every cloud tag alias into dedicated columns.
