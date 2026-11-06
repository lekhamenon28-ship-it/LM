"""Independent, read-only Dynatrace maturity collector. Writes one Excel workbook."""
import argparse
import os
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from credential_config import DEFAULT_FILE, load_credentials
from dynatrace_client import ApiClient, PlatformClient
from report_export import write_xlsx


def tags(values):
    result = {}
    for tag in values or []:
        key = tag.get('key', '')
        value = tag.get('value')
        result.setdefault(key, []).append(value if value is not None else True)
    return result


class Collector:
    def __init__(self, client, platform=None, days=30):
        self.client, self.platform = client, platform
        self.end = datetime.now(timezone.utc)
        self.start = self.end - timedelta(days=days)
        self.tables = {}
        self.coverage = []

    def source(self, name, path, operation, platform=False):
        rows = []
        status, error = 'available_empty', None
        try:
            if platform and self.platform is None:
                status = 'not_configured'
                error = 'Set DT_PLATFORM_URL and DT_PLATFORM_TOKEN (Bearer); Environment API token cannot replace it.'
            else:
                # Extend incrementally so errors preserve rows already received.
                for row in operation():
                    rows.append(row)
                status = 'available' if rows else 'available_empty'
        except Exception as exc:
            status = 'partial' if rows else 'error'
            error = str(exc)
        self.tables[name] = rows
        self.coverage.append({'dataset': name, 'endpoint': path, 'status': status,
                              'row_count': len(rows), 'detail': error,
                              'scope': 'Accessible data for supplied credential and report window; empty does not prove feature disabled.'})
        print(name, status, len(rows), flush=True)
        return rows

    def entity_rows(self, kind):
        rows, _ = self.client.paged('/api/v2/entities', {
            'entitySelector': f'type("{kind}")', 'pageSize': 500,
            'from': int(self.start.timestamp()*1000), 'to': int(self.end.timestamp()*1000),
            'fields': '+properties,+tags,+fromRelationships,+toRelationships,+lastSeenTms'}, 'entities')
        for entity in rows:
            yield {'entity_id': entity['entityId'], 'name': entity.get('displayName'),
                   'entity_type': kind, 'last_seen_ms': entity.get('lastSeenTms'),
                   'tags': tags(entity.get('tags')), 'properties': entity.get('properties', {}),
                   'from_relationships': entity.get('fromRelationships', {}),
                   'to_relationships': entity.get('toRelationships', {})}

    def dashboards(self):
        for item in self.client.get('/api/config/v1/dashboards')['dashboards']:
            detail_error = None
            try:
                meta = self.client.get('/api/config/v1/dashboards/'+item['id']).get('dashboardMetadata', {})
            except Exception as exc:
                meta = {}
                detail_error = str(exc)
            yield {'id': item['id'], 'name': meta.get('name', item.get('name')),
                   'owner': meta.get('owner', item.get('owner')), 'tags': meta.get('tags', item.get('tags')),
                   'type': 'classic_dashboard', 'last_modified': meta.get('lastModified'),
                   'detail_error': detail_error, 'source': 'Environment Configuration API'}

    def platform_dashboards(self):
        for page in self.platform.documents():
            for item in page:
                yield {key: item.get(key) for key in ('id','name','type','owner','labels','modificationInfo')}

    def problems(self):
        rows, _ = self.client.paged('/api/v2/problems', {
            'from': int(self.start.timestamp()*1000), 'to': int(self.end.timestamp()*1000),
            'pageSize': 500}, 'problems')
        for row in rows:
            yield {'id': row['problemId'], 'title': row.get('title'), 'severity': row.get('severityLevel'),
                   'status': row.get('status'), 'owner': None, 'last_triggered_ms': row.get('startTime'),
                   'trigger_count': 1, 'end_ms': row.get('endTime'),
                   'trigger_count_basis': 'one problem occurrence, not notifications or historical retriggers'}

    def metrics(self):
        rows, _ = self.client.paged('/api/v2/metrics', {'pageSize': 500}, 'metrics')
        for row in rows:
            yield {'name': row.get('metricId'), 'display_name': row.get('displayName'),
                   'unit': row.get('unit'), 'last_reported_ms': row.get('lastWritten'),
                   'custom': not row.get('metricId','').startswith(('builtin:', 'dt.')),
                   'classification_basis': 'namespace heuristic; not a billing classification'}

    def synthetics(self):
        for item in self.client.get('/api/v1/synthetic/monitors').get('monitors', []):
            detail = self.client.get('/api/v1/synthetic/monitors/'+item['entityId'])
            # Scripts can contain credentials: export metadata only.
            yield {key: detail.get(key, item.get(key)) for key in
                   ('entityId','name','type','enabled','frequencyMin','locations','tags')}

    def usql(self, query):
        result = self.client.get('/api/v1/userSessionQueryLanguage/table', {
            'query': query, 'startTimestamp': int(self.start.timestamp()*1000),
            'endTimestamp': int(self.end.timestamp()*1000)})
        for values in result.get('values', []):
            yield {**dict(zip(result['columnNames'], values)),
                   'extrapolation_level': result.get('extrapolationLevel'),
                   'scope': 'Classic closed sessions; grouped results limited to 5000 rows',
                   'limit_reached': len(result.get('values', [])) >= 5000}

    def collect(self):
        entities = []
        for kind in ('HOST','SERVICE','APPLICATION'):
            entities.extend(self.source(kind.lower()+'_inventory', '/api/v2/entities', lambda k=kind:self.entity_rows(k)))
        relationships = set()
        for row in entities:
            for field, reverse in (('from_relationships',False),('to_relationships',True)):
                for relation, targets in row[field].items():
                    for target in targets:
                        other = target.get('id') or target.get('entityId')
                        if other:
                            relationships.add((other if reverse else row['entity_id'], row['entity_id'] if reverse else other, relation))
        self.tables['service_relationships'] = [dict(zip(('source','target','relationship'), r)) for r in sorted(relationships)]
        self.source('classic_dashboards', '/api/config/v1/dashboards', self.dashboards)
        self.source('platform_dashboards', '/platform/document/v1/documents', self.platform_dashboards, True)
        alerts = self.source('alerts', '/api/v2/problems', self.problems)
        durations = [(r['end_ms']-r['last_triggered_ms'])/60000 for r in alerts
                     if r['status']=='CLOSED' and isinstance(r['end_ms'],(int,float))
                     and isinstance(r['last_triggered_ms'],(int,float))
                     and r['last_triggered_ms'] <= r['end_ms'] <= self.end.timestamp()*1000]
        self.tables['recovery'] = [{'measure':'MTTR_minutes','value': statistics.mean(durations) if durations else None,
                                  'sample_count':len(durations),'basis':'Closed problems returned in window; end minus start; excludes open/future/invalid times'},
                                 {'measure':'MTTD_minutes','value':None,'basis':'Occurrence and detection timestamps unavailable'},
                                 {'measure':'MTTA_minutes','value':None,'basis':'Verified acknowledgement timestamps unavailable'}]
        metrics = self.source('metrics', '/api/v2/metrics', self.metrics)
        self.source('classic_synthetics', '/api/v1/synthetic/monitors', self.synthetics)
        self.source('platform_synthetics', '/platform/synthetic/v1/monitors',
                    lambda: ({k:r.get(k) for k in ('id','name','type','enabled')} for r in
                             self.platform.get('/platform/synthetic/v1/monitors').get('monitors', [])), True)
        for name, query in {
            'rum_sessions':'SELECT COUNT(*) FROM usersession',
            'rum_browsers':'SELECT browserFamily, COUNT(*) FROM usersession GROUP BY browserFamily LIMIT 5000',
            'rum_devices':'SELECT device, COUNT(*) FROM usersession GROUP BY device LIMIT 5000',
            'rum_user_actions':'SELECT COUNT(*) FROM useraction'}.items():
            self.source(name, '/api/v1/userSessionQueryLanguage/table', lambda q=query:self.usql(q))
        for name, query in {
            'traced_services':'fetch spans | filter isNotNull(dt.entity.service) | summarize span_count=count(), by:{dt.entity.service}',
            'platform_rum_sessions':'fetch user.sessions | summarize session_count=count()'}.items():
            self.source(name, '/platform/storage/query/v1/query:execute',
                        lambda q=query:self.platform.query(q,self.start,self.end), True)
        # These are native count usage metrics; do not relabel browser actions as test runs.
        for name, metric in {
            'full_stack_gib_hours':'builtin:billing.full_stack_monitoring.usage_per_host',
            'infrastructure_host_hours':'builtin:billing.infrastructure_monitoring.usage_per_host',
            'foundation_host_hours':'builtin:billing.foundation_and_discovery.usage_per_host',
            'synthetic_browser_actions':'builtin:billing.synthetic.actions.usage',
            'synthetic_http_requests':'builtin:billing.synthetic.requests.usage'}.items():
            self.source(name, '/api/v2/metrics/query', lambda m=metric: self.client.get('/api/v2/metrics/query', {
                'metricSelector':m+':splitBy():sum','from':int(self.start.timestamp()*1000),
                'to':int(self.end.timestamp()*1000),'resolution':'Inf'}).get('result', []))
        self.tables['summary'] = [{'measure':'metric_count','value':len(metrics)},
                                  {'measure':'dashboard_completeness','value':'Accessible Classic and Platform sources reported separately; org-wide total unverified'},
                                  {'measure':'trace_retention','value':'unknown; requires bucket/contract configuration'},
                                  {'measure':'pricing','value':'Contract billing units/rates required; no inferred costs'}]
        self.tables['feature_availability'] = self.coverage
        return {'tool':'dynatrace','window_start':self.start.isoformat(), 'window_end':self.end.isoformat(),'datasets':self.tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, default=DEFAULT_FILE)
    parser.add_argument('--output', type=Path, default=Path('dynatrace_complete_report.xlsx'))
    parser.add_argument('--days', type=int, choices=range(1,91), default=30)
    args = parser.parse_args()
    load_credentials(args.credentials, tool='dynatrace')
    if not os.getenv('DT_ENV_URL') or not os.getenv('DT_API_TOKEN'):
        parser.error('Dynatrace environment URL and API token are required')
    platform = PlatformClient(os.environ['DT_PLATFORM_URL'], os.environ['DT_PLATFORM_TOKEN']) if os.getenv('DT_PLATFORM_URL') and os.getenv('DT_PLATFORM_TOKEN') else None
    report = Collector(ApiClient(os.environ['DT_ENV_URL'],os.environ['DT_API_TOKEN']), platform, args.days).collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx(args.output, {name:(rows,[]) for name,rows in report['datasets'].items()}, report=report)
    print('Report written to', args.output.resolve())


if __name__ == '__main__':
    main()
