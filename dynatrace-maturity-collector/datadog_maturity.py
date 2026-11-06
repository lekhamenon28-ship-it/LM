#!/usr/bin/env python3
"""Read-only Datadog maturity report with explicit API and feature coverage."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

from credential_config import DEFAULT_FILE, load_credentials, read_credentials
from datadog_client import Client, APIError
from collector_common import timestamp
from report_export import write_csv, write_xlsx, read_report
from datadog_enrichment import (load_config, dashboard_list_members, custom_metrics, span_services,
    rum_probe, retry_metric_timestamps, apply_enrichment, remediation_rows)

UNKNOWN = 'unknown'


def iso(value):
    parsed = timestamp(value)
    return parsed.isoformat() if parsed else UNKNOWN


def tag_map(value):
    """Preserve conflicting tag values, source namespaces and colon-containing values."""
    result = defaultdict(set)
    def add(key, val):
        if val is not None:
            result[str(key).strip().casefold().replace('-', '_')].add(str(val).strip())
    def visit(item):
        if isinstance(item, dict):
            for key, val in item.items():
                if isinstance(val, dict):
                    visit(val)
                elif isinstance(val, list):
                    # Datadog tags_by_source is {integration: ["key:value", ...]}.
                    for child in val:
                        if isinstance(child, str) and ':' not in child:
                            add(key, child)
                        else:
                            visit(child)
                else:
                    add(key, val)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            key, sep, val = item.partition(':')
            add(key, val if sep else '')
    visit(value)
    return {k: sorted(v) for k, v in sorted(result.items())}


TAG_FIELDS = {
    'cloud_provider': ('cloud_provider', 'cloud', 'provider', 'platform'),
    'region': ('region', 'cloud.region', 'aws_region', 'azure_region', 'gcp_region', 'location'),
    'availability_zone': ('availability_zone', 'zone', 'cloud.availability_zone'),
    'environment': ('env', 'environment', 'environment_name', 'deployment.environment', 'deployment.environment.name'),
    'application_id': ('application_id', 'app_id', 'appid', 'applicationid', 'application.id'),
    'application': ('application', 'app', 'app_name', 'application_name'),
    'service': ('service', 'service_name'),
    'owner': ('owner', 'team', 'responsible_team'),
    'account_id': ('account_id', 'aws_account', 'aws_account_id', 'subscription_id', 'project_id'),
    'instance_id': ('instance_id', 'aws_instance_id', 'host_id', 'cloud.instance.id'),
}


def host_row(host):
    tags = tag_map([host.get('tags_by_source', {}), host.get('tags', [])])
    result = {'tool': 'datadog', 'host_id': str(host.get('id', UNKNOWN)),
              'hostname': str(host.get('name') or host.get('host_name') or UNKNOWN).strip(),
              'aliases': sorted(set(host.get('aliases') or [])),
              'last_reported_at': iso(host.get('last_reported_time')),
              'is_muted': host.get('is_muted', UNKNOWN), 'sources': host.get('sources') or [],
              'integrations': host.get('apps') or [], 'tags': tags}
    for field, keys in TAG_FIELDS.items():
        values = {v for key in keys for v in tags.get(key, []) if v}
        result[field] = '; '.join(sorted(values)) or UNKNOWN
    if result['cloud_provider'] == UNKNOWN:
        # Only explicit integration metadata, never hostname heuristics.
        sources = set(str(s).casefold() for s in result['sources'] + result['integrations'])
        providers = {provider for provider, markers in {'aws': {'aws', 'ec2'}, 'azure': {'azure'}, 'gcp': {'gcp', 'gce', 'google_cloud'}}.items() if sources & markers}
        if providers:
            result['cloud_provider'] = '; '.join(sorted(providers))
    result['metadata_status'] = 'partial' if any(result[k] == UNKNOWN for k in ('cloud_provider', 'region', 'environment', 'application_id')) else 'available'
    return result




class Collector:
    def __init__(self, client, days=30, config=None):
        self.client = client
        self.config = config or {}
        self.end = datetime.now(timezone.utc)
        self.start = self.end - timedelta(days=days)
        self.active_start = self.end - timedelta(days=1)
        self.tables = {}
        self.coverage = []
        self.hosts = []
        self.apm = []
        self.usage = []

    def run_source(self, dataset, source, func, permission='', note=''):
        start_count = len(self.tables.get(dataset, []))
        status, detail, http = 'available', note, 200
        try:
            for row in func():
                self.tables.setdefault(dataset, []).append(row)
            if len(self.tables.get(dataset, [])) == start_count:
                status = 'available_empty'
                detail = 'API returned no matching records; feature enablement is unknown. ' + note
        except Exception as exc:
            count = len(self.tables.get(dataset, [])) - start_count
            status = 'partial' if count else 'unavailable'
            http = getattr(exc, 'status', UNKNOWN)
            detail = str(exc) + ('; ' + note if note else '')
        count = len(self.tables.get(dataset, [])) - start_count
        details = self.tables.get(dataset, [])[start_count:]
        enrichment_failures = sum((r.get('detail_status') not in (None,'available') or
            r.get('query_status') not in (None,'queried') or r.get('status') in ('partial','unavailable') or str(r.get('status','')).startswith('HTTP ')) for r in details)
        if enrichment_failures and status in ('available','available_empty'):
            status = 'partial'
            detail += f' {enrichment_failures} rows have unavailable enrichment; see row status.'
        self.coverage.append({'dataset':dataset,'source':source,'status':status,'record_count':count,'http_status':http,'required_permission':permission or UNKNOWN,'details':detail,'checked_at':datetime.now(timezone.utc).isoformat()})
        self.tables.setdefault(dataset, [])
        print(f'{dataset} [{source}]: {status}, {count} rows', flush=True)

    def hosts_collect(self):
        for payload in self.client.offset('/api/v1/hosts', 'host_list', 1000, 'count','start', {'include_hosts_metadata':'true'}):
            for host in payload.get('host_list', []):
                self.hosts.append(host)
                yield host_row(host)

    def dashboards(self):
        # Details expose tags and author information absent from the summary endpoint.
        for payload in self.client.offset('/api/v1/dashboard','dashboards',100,'count','start'):
            for item in payload.get('dashboards', []):
                detail_status = 'available'
                detail = {}
                try:
                    detail = self.client.request('/api/v1/dashboard/'+urllib.parse.quote(item['id'],safe=''))
                except Exception as exc:
                    detail_status = str(exc)
                tags = detail.get('tags', item.get('tags'))
                ownership=tag_map(tags or [])
                yield {'dashboard_id':item['id'],'name':item.get('title'),
                       'owner':'; '.join(ownership.get('owner',[]) or ownership.get('team',[])) or UNKNOWN,
                       'owner_basis':'explicit owner/team tag; author is separate',
                       'author':detail.get('author_handle') or item.get('author_handle') or UNKNOWN,
                       'tags':tags if tags is not None else UNKNOWN,
                       'type':detail.get('layout_type') or item.get('layout_type') or UNKNOWN,
                       'created_at':item.get('created_at'), 'last_modified_at':detail.get('modified_at') or item.get('modified_at'),
                       'url':item.get('url'), 'origin':'custom_or_cloned', 'detail_status':detail_status}

    def apm_services(self):
        self.apm=[]
        environments=['*']
        fallback={os.environ.get('DD_ENV','prod'),*self.config.get('environments',[])}
        fallback.update(v for h in self.tables.get('host_inventory',[]) for v in str(h.get('environment','')).split('; ') if v and v!=UNKNOWN)
        for env in environments:
            payload=self.client.request('/api/v2/apm/services', {'filter[env]':env})
            attrs=payload.get('data',{}).get('attributes',{})
            services=attrs.get('services') or []
            if env=='*' and not services:environments.extend(sorted(fallback-{'*'}))
            for i,name in enumerate(services):
                meta=attrs.get('metadata') or []
                metadata=meta[i] if i<len(meta) and isinstance(meta[i],dict) else {}
                row={'service':name,'source':'apm','is_traced':metadata.get('isTraced',UNKNOWN),'is_usm':metadata.get('isUsm',UNKNOWN),'environment':env}
                self.apm.append(row)
                yield row

    def catalog(self):
        for payload in self.client.offset('/api/v2/catalog/entity', params={'includeDiscovered':'true'}):
            for item in payload.get('data', []):
                attrs = item.get('attributes', {})
                yield {'service':attrs.get('name'),'display_name':attrs.get('displayName'),'source':'software_catalog', 'catalog_id':item.get('id'),
                       'kind':attrs.get('kind'),'namespace':attrs.get('namespace'),'owner':attrs.get('owner'),
                       'tags':attrs.get('tags', []),'last_modified_at':item.get('meta',{}).get('modifiedAt'),
                       'is_traced':UNKNOWN,'is_usm':UNKNOWN,'evidence':'Catalog registration does not prove active APM instrumentation'}

    def definitions(self):
        for payload in self.client.offset('/api/v2/services/definitions', size_key='page[size]',offset_key='page[number]',numbered=True):
            for item in payload.get('data', []):
                attrs=item.get('attributes', {});schema=attrs.get('schema',{});meta=schema.get('metadata',{})
                yield {'service':schema.get('dd-service') or meta.get('name') or schema.get('name'), 'source':'service_definition',
                       'catalog_id':item.get('id'),'kind':schema.get('kind','service'),'owner':schema.get('team') or meta.get('owner') or UNKNOWN,
                       'tags':schema.get('tags') or meta.get('tags',[]),'last_modified_at':attrs.get('meta',{}).get('last-modified-time'),
                       'is_traced':UNKNOWN,'is_usm':UNKNOWN,'evidence':'Configured service definition; runtime availability unverified'}

    def relations(self):
        for payload in self.client.offset('/api/v2/catalog/relation',params={'includeDiscovered':'true'}):
            for item in payload.get('data',[]):
                attrs=item.get('attributes',{})
                yield {'relationship_id':item.get('id'), 'source':attrs.get('from'), 'target':attrs.get('to'),
                       'relationship':attrs.get('type'), 'evidence':item.get('meta',{}).get('source',UNKNOWN),'api':'software_catalog'}

    def dependencies(self):
        envs = sorted({v for host in self.tables.get('host_inventory',[]) for v in str(host.get('environment','')).split('; ') if v and v!=UNKNOWN})
        envs = sorted(set([os.environ.get('DD_ENV','prod'), *envs,*self.config.get('environments',[]),
                          *(r['environment'] for r in self.apm if r.get('environment') not in (None,'*',UNKNOWN))]))
        for env in envs:
            payload=self.client.request('/api/v1/service_dependencies',{'env':env,'start':int(self.active_start.timestamp()),'end':int(self.end.timestamp())})
            for source,attrs in payload.items():
                if isinstance(attrs,dict):
                    for target in attrs.get('calls',[]):
                        yield {'source':source,'target':target,'relationship':'calls','environment':env,'api':'apm_service_dependencies','evidence':'observed_service_dependency'}

    def alerts(self):
        for payload in self.client.offset('/api/v1/monitor',None,1000,'page_size','page',{'group_states':'all','with_downtimes':'false'},numbered=True):
            for monitor in payload:
                tags=tag_map(monitor.get('tags',[]))
                owner='; '.join(tags.get('owner',[]) or tags.get('team',[])) or UNKNOWN
                groups=(monitor.get('state') or {}).get('groups') or {'':{}}
                for group, state in groups.items():
                    triggered=timestamp(state.get('last_triggered_ts'))
                    resolved=timestamp(state.get('last_resolved_ts'))
                    status=state.get('status') or monitor.get('overall_state') or UNKNOWN
                    duration = (resolved-triggered).total_seconds()/60 if triggered and resolved and resolved>=triggered and self.start<=resolved<=self.end and str(status).lower()=='ok' else None
                    yield {'monitor_id':monitor.get('id'),'group':group,'name':monitor.get('name'),
                           'severity':f"P{monitor['priority']}" if monitor.get('priority') else UNKNOWN,
                           'status':str(status).lower().replace(' ','_'),'owner':owner,'owner_basis':'explicit owner/team tag; author is separate',
                           'author':(monitor.get('creator') or {}).get('email',UNKNOWN), 'last_triggered_at':iso(state.get('last_triggered_ts')),
                           'last_resolved_at':iso(state.get('last_resolved_ts')),'trigger_count':UNKNOWN,
                           'trigger_count_basis':'Monitor snapshot has no occurrence history; see feature_availability',
                           'type':monitor.get('type'),'tags':monitor.get('tags',[]),
                           'latest_recovery_minutes':duration,'measurement':'latest_monitor_recovery',
                           'window_start':self.start.isoformat(),'window_end':self.end.isoformat()}

    def usage_collect(self):
        start=self.start.replace(minute=0,second=0,microsecond=0)
        end=self.end.replace(minute=0,second=0,microsecond=0)
        chunk_end=end
        failed=[]
        consecutive_errors=0
        while chunk_end>start:
            chunk=max((chunk_end-timedelta(microseconds=1)).replace(hour=0,minute=0,second=0,microsecond=0),start)
            params={'filter[timestamp][start]':chunk.isoformat(),'filter[timestamp][end]':chunk_end.isoformat(),
                    'filter[product_families]':'all','filter[include_descendants]':'false','page[limit]':500}
            cursors=set()
            try:
                while True:
                    try:
                        payload=self.client.request('/api/v2/usage/hourly_usage',params,timeout=10)
                    except TimeoutError:
                        payload=self.client.request('/api/v2/usage/hourly_usage',params,timeout=10)
                    for item in payload.get('data',[]):
                        attrs=item.get('attributes',{})
                        for measurement in attrs.get('measurements') or []:
                            row={'timestamp':attrs.get('timestamp'),'product_family':attrs.get('product_family'),
                                 'usage_type':measurement.get('usage_type'),'value':measurement.get('value'),
                                 'organization_id':attrs.get('public_id'),'region':attrs.get('region'),
                                 'status':'reported' if measurement.get('value') is not None else
                                    'billing_pending' if timestamp(attrs.get('timestamp')) and timestamp(attrs['timestamp'])>=datetime.now(timezone.utc)-timedelta(hours=self.config.get('billing_lag_hours',72)) else 'not_reported',
                                 'window_start':start.isoformat(),'window_end':end.isoformat()}
                            self.usage.append(row)
                            yield row
                    cursor=payload.get('meta',{}).get('pagination',{}).get('next_record_id')
                    if not cursor:break
                    if cursor in cursors:raise RuntimeError('Usage pagination repeated cursor')
                    cursors.add(cursor);params['page[next_record_id]']=cursor
                consecutive_errors=0
            except APIError:
                raise
            except Exception as exc:
                failed.append(chunk.isoformat()+': '+str(exc))
                consecutive_errors+=1
            print(f'usage slice {chunk.date()}: '+('failed' if consecutive_errors else 'collected'),flush=True)
            chunk_end=chunk
            if consecutive_errors>=3:
                failed.append('Stopped after three consecutive daily request failures; older interval '+start.isoformat()+' to '+chunk_end.isoformat()+' was not collected')
                break
        if failed:raise RuntimeError('Missing daily usage intervals: '+'; '.join(failed))

    def monitor_events(self):
        body={'filter':{'query':'source:alert','from':self.start.isoformat(),'to':self.end.isoformat()},
              'page':{'limit':1000},'sort':'timestamp'}
        seen=set()
        while True:
            payload=self.client.request('/api/v2/events/search',body=body)
            meta=payload.get('meta',{})
            if meta.get('warnings') or meta.get('status') in ('timeout','partial'):
                raise RuntimeError('Event search returned partial results or warnings')
            for item in payload.get('data',[]):
                envelope=item.get('attributes',{});attrs=envelope.get('attributes',{})
                monitor=attrs.get('monitor',{});transition=monitor.get('transition',{})
                if not monitor.get('id'):continue
                yield {'event_id':attrs.get('evt',{}).get('id') or item.get('id'),
                       'monitor_id':monitor['id'],'groups':sorted(monitor.get('groups') or []),
                       'timestamp':envelope.get('timestamp'),
                       'source_state':transition.get('source_state',UNKNOWN),'destination_state':transition.get('destination_state',UNKNOWN),
                       'transition_type':transition.get('transition_type',UNKNOWN),
                       'cycle_id':monitor.get('alert_cycle_key_txt') or attrs.get('monitor-alert-event',{}).get('alert_cycle_key') or UNKNOWN}
            cursor=meta.get('page',{}).get('after')
            if not cursor:break
            if cursor in seen:raise RuntimeError('Event search repeated cursor')
            seen.add(cursor);body['page']['cursor']=cursor

    def apply_monitor_history(self):
        complete=any(r['dataset']=='alert_events' and r['status'] in ('available','available_empty') for r in self.coverage)
        counts=Counter();latest={};seen=set();unclassified=set()
        for row in self.tables.get('alert_events',[]):
            if row['event_id'] in seen:continue
            seen.add(row['event_id'])
            key=(str(row['monitor_id']),','.join(sorted(row['groups'])))
            if not row['source_state'] or not row['destination_state'] or str(row['source_state']).casefold()=='unknown' or str(row['destination_state']).casefold()=='unknown':
                unclassified.add(key)
            if (str(row['destination_state']).casefold()=='alert' and
                str(row['source_state']).casefold()!='alert' and
                str(row['transition_type']).casefold() not in ('renotify','renotification') and row['source_state']!=UNKNOWN):
                group=','.join(sorted(row['groups']))
                key=(str(row['monitor_id']),group)
                counts[key]+=1
                t=timestamp(row['timestamp'])
                if t:latest[key]=max(latest.get(key,t),t)
        for row in self.tables.get('alerts',[]):
            key=(str(row['monitor_id']),','.join(sorted(t.strip() for t in row['group'].split(',') if t.strip())))
            known=complete and key not in unclassified
            row['trigger_count']=counts[key] if known else UNKNOWN
            row['trigger_count_basis']='Distinct retained events entering Alert from another known state; excludes warnings, no-data and renotifications; requested-window coverage' if known else 'Event history incomplete, unavailable or missing transition fields; trigger count unknown'
            if key in latest:
                existing=timestamp(row['last_triggered_at'])
                row['last_triggered_at']=max(existing,latest[key]).isoformat() if existing else latest[key].isoformat()

    def retention(self):
        for item in self.client.request('/api/v2/apm/config/retention-filters').get('data',[]):
            attrs=item.get('attributes',{})
            yield {'filter_id':item.get('id'),'name':attrs.get('name'),'enabled':attrs.get('enabled'),
                   'rate':attrs.get('rate'),'filter_type':attrs.get('filter_type'),'filter':attrs.get('filter'),
                   'retention_days':UNKNOWN,'retention_basis':'Filter API exposes sampling rules, not organization retention duration'}

    def estimated_usage(self):
        selected={
            'datadog.estimated_usage.apm.ingested_bytes':('ingested_span_bytes','bytes'),
            'datadog.estimated_usage.apm.ingested_spans':('ingested_spans','spans'),
            'datadog.estimated_usage.apm.total_indexed_spans':('indexed_spans','spans'),
            'datadog.estimated_usage.metrics.custom':('custom_metrics','timeseries'),
            'datadog.estimated_usage.hosts':('hosts','hosts')}
        active={r['metric']:r for r in self.tables.get('metrics',[])}
        for name,(driver,unit) in selected.items():
            if name not in active:continue
            is_count=active[name].get('type')=='count'
            query='sum:'+name+'{*}'+('.as_count()' if is_count else '.rollup(avg,3600)')
            payload=self.client.request('/api/v1/query',{'from':int(self.active_start.timestamp()),'to':int(self.end.timestamp()),'query':query})
            if payload.get('status')=='error' or payload.get('error'):raise RuntimeError('Estimated usage query failed')
            for series in payload.get('series') or []:
                for point in series.get('pointlist') or []:
                    if len(point)<2 or point[1] is None:continue
                    yield {'driver':driver,'metric':name,'timestamp':iso(point[0]),'value':point[1],
                           'unit':unit,'aggregation':'count_per_query_bucket' if is_count else 'average_gauge_per_query_bucket',
                           'quality':'estimated_usage_metric; not billing API consumption or invoice',
                           'window_start':self.active_start.isoformat(),'window_end':self.end.isoformat()}

    def rum_apps(self):
        for item in self.client.request('/api/v2/rum/applications').get('data',[]):
            attrs=item.get('attributes',{})
            owners=tag_map(attrs.get('tags') or [])
            yield {'application_id':item.get('id'),'name':attrs.get('name'),'type':attrs.get('type'),
                   'active':attrs.get('is_active'),'tags':attrs.get('tags',[]),
                   'owner':'; '.join(owners.get('owner',[]) or owners.get('team',[])) or UNKNOWN,
                   'owner_basis':'explicit owner/team tag; author is separate','author':attrs.get('created_by_handle'),
                   'last_modified_at':iso(attrs.get('updated_at')),'product_scales':attrs.get('product_scales',{}),
                   'replay_sample_rate':attrs.get('apm_rum_flat_sampling_replay_sample_rate',UNKNOWN)}

    def rum_aggregate(self, event_type, facet=None):
        body={'filter':{'from':self.start.isoformat(),'to':self.end.isoformat(),'query':'@type:'+event_type},
              'compute':[{'aggregation':'count','type':'total'},{'aggregation':'cardinality','metric':'@session.id','type':'total'}],
              'group_by':[{'facet':facet or '@application.id','limit':1000}]}
        seen=set()
        while True:
            payload=self.client.request('/api/v2/rum/analytics/aggregate',body=body)
            if payload.get('meta',{}).get('warnings') or payload.get('meta',{}).get('status') in ('timeout','partial'):
                raise RuntimeError('RUM aggregation returned warnings/partial results')
            for bucket in payload.get('data',{}).get('buckets',[]):
                yield {'event_type':event_type,'dimension':facet or '@application.id',
                       'dimension_value':bucket.get('by',{}).get(facet or '@application.id'),
                       'event_count':bucket.get('computes',{}).get('c0'),
                       'distinct_sessions':bucket.get('computes',{}).get('c1'),
                       'count_basis':'Retained RUM events; cardinality is approximate; not billable sessions',
                       'window_start':self.start.isoformat(),'window_end':self.end.isoformat()}
            cursor=payload.get('meta',{}).get('page',{}).get('after')
            if not cursor:break
            if cursor in seen:raise RuntimeError('RUM pagination repeated cursor')
            seen.add(cursor);body['page']={'cursor':cursor}

    def synthetic_tests(self):
        for payload in self.client.offset('/api/v1/synthetics/tests','tests',100,'page_size','page_number',numbered=True):
            for item in payload.get('tests',[]):
                options=item.get('options',{})
                owners=tag_map(item.get('tags') or [])
                yield {'test_id':item.get('public_id'),'name':item.get('name'),'type':item.get('type'),'subtype':item.get('subtype'),
                       'status':item.get('status'),'frequency_seconds':options.get('tick_every'),
                       'locations':item.get('locations',[]),'devices':options.get('device_ids',[]),
                       'tags':item.get('tags',[]),'owner':'; '.join(owners.get('owner',[]) or owners.get('team',[])) or UNKNOWN,
                       'owner_basis':'explicit owner/team tag; author is separate','author':(item.get('creator') or {}).get('email'),
                       'last_modified_at':item.get('modified_at'),'billing_runs':UNKNOWN,
                       'billing_basis':'Account-level measured consumption is in usage.csv; schedule is not actual execution count'}

    def synthetic_results(self):
        for test in self.tables.get('synthetics',[]):
            path='/api/v2/synthetics/tests/'+urllib.parse.quote(test['test_id'],safe='')+'/results'
            try:
                params={'from_ts':int(self.start.timestamp()*1000),'to_ts':int(self.end.timestamp()*1000)}
                primary_error=None
                try:payload=self.client.request(path,params)
                except APIError as exc:
                    if exc.status not in (403,404):raise
                    primary_error=str(exc);payload={'data':[]}
                results=[item.get('attributes',{}) for item in payload.get('data',[])]
                source='v2_latest_results'
                if not results and test.get('type') in ('api','browser'):
                    path=('/api/v1/synthetics/tests/browser/' if test['type']=='browser' else '/api/v1/synthetics/tests/')+urllib.parse.quote(test['test_id'],safe='')+'/results'
                    legacy=self.client.request(path,params)
                    source='v1_latest_results_sample'
                    for item in legacy.get('results',[]):
                        passed=(item.get('result') or {}).get('passed')
                        explicit=item.get('status')
                        status='passed' if passed is True else 'failed' if passed is False else explicit if explicit in ('passed','failed','no_data') else UNKNOWN
                        results.append({'status':status})
                counts=Counter(item.get('status',UNKNOWN) for item in results)
                denominator=counts['passed']+counts['failed']
                yield {'test_id':test['test_id'],'type':test['type'],'returned_results':len(results),
                       'passed':counts['passed'],'failures':counts['failed'],'no_data':counts['no_data'], 'unclassified_results':len(results)-denominator-counts['no_data'],
                       'success_rate_percent':round(100*counts['passed']/denominator,4) if denominator else UNKNOWN,
                       'status':'partial' if primary_error or counts[UNKNOWN] else 'available' if results else 'available_empty',
                       'source':source,'primary_endpoint_error':primary_error or '',
                       'test_schedule_status':test.get('status',UNKNOWN),
                       'coverage':'latest_results_sample; API does not guarantee all executions; not SLA or billing total',
                       'window_start':self.start.isoformat(),'window_end':self.end.isoformat()}
            except Exception as exc:
                yield {'test_id':test['test_id'],'status':str(exc),'success_rate_percent':UNKNOWN,'failures':UNKNOWN}

    def incidents(self):
        for payload in self.client.offset('/api/v2/incidents',size=50,size_key='page[size]'):
            for item in payload.get('data',[]):
                attrs=item.get('attributes',{});created=timestamp(attrs.get('created'))
                if not created or not self.start<=created<=self.end or attrs.get('is_test'):continue
                impact=timestamp(attrs.get('customer_impact_start'));detected=timestamp(attrs.get('detected'));resolved=timestamp(attrs.get('resolved'))
                def minutes(a,b):return round((b-a).total_seconds()/60,6) if a and b and a<=b<=self.end else None
                yield {'incident_id':item.get('id'),'title':attrs.get('title'),'severity':attrs.get('severity'),'status':attrs.get('state'),
                       'created_at':iso(attrs.get('created')),'detected_at':iso(attrs.get('detected')),
                       'impact_started_at':iso(attrs.get('customer_impact_start')),'resolved_at':iso(attrs.get('resolved')),
                       'mttr_minutes':minutes(created,resolved) if attrs.get('state') in ('resolved','completed') else None,'mttd_minutes':minutes(impact,detected),
                       'mtta_minutes':UNKNOWN,'internal_response_minutes':minutes(detected,created),
                       'mtta_basis':'No explicit first acknowledgement timestamp; internal response is reported separately'}

    def metrics(self):
        # Org-wide recent metrics, avoiding duplication per host and old configured-only names.
        payload=self.client.request('/api/v1/metrics',{'from':int(self.active_start.timestamp())})
        names=sorted(set(payload.get('metrics',[])))
        last={}
        query_status={}
        for pos in range(0,len(names),20):
            batch=names[pos:pos+20]
            try:
                query=', '.join('avg:'+name+'{*}' for name in batch)
                result=self.client.request('/api/v1/query',{'from':int(self.active_start.timestamp()),'to':int(self.end.timestamp()),'query':query})
                if result.get('status')=='error' or result.get('error'):raise RuntimeError('metric_query_error')
                for series in result.get('series') or []:
                    metric=series.get('metric');points=[p[0] for p in series.get('pointlist',[]) if len(p)>1 and p[1] is not None]
                    if points:last[metric]=max(last.get(metric,0),max(points))
                query_status.update({name:'queried' for name in batch})
            except Exception as exc:
                query_status.update({name:str(exc) for name in batch})
        def enrich(name):
            encoded=urllib.parse.quote(name,safe='')
            meta,volume={},{}
            statuses=[]
            try:meta=self.client.request('/api/v1/metrics/'+encoded)
            except Exception as exc:statuses.append('metadata: '+str(exc))
            try:volume=self.client.request('/api/v2/metrics/'+encoded+'/volumes').get('data',{}).get('attributes',{})
            except Exception as exc:statuses.append('volumes: '+str(exc))
            return {'metric':name,'active_window_hours':24,'type':meta.get('type'),'unit':meta.get('unit'),
                   'integration':meta.get('integration'),
                   'is_custom':volume.get('is_custom',UNKNOWN),
                   'custom_classification_basis':'Unknown unless API explicitly identifies custom status; integration name is not a billing classification',
                   'last_reported_at':iso(last.get(name)),'last_reported_basis':'latest nonnull query rollup bucket in last 24h; not exact raw ingestion time',
                   'query_status':query_status.get(name,UNKNOWN),'volumes':volume,
                   'detail_status':'; '.join(statuses) or 'available'}
        with ThreadPoolExecutor(max_workers=4) as executor:
            for index,row in enumerate(executor.map(enrich,names),1):
                yield row
                if index%100==0:print(f'metrics: enriched {index}/{len(names)}',flush=True)

    def build_summaries(self):
        apply_enrichment(self)
        self.coverage=[r for r in self.coverage if r.get('source') not in ('preset_integration_dashboards','retention_duration','mtta','apm_instrumentation')]
        self.apply_monitor_history()
        self.tables['total_host_details']=[dict(row) for row in self.tables.get('host_inventory',[])]
        metrics=self.tables.get('metrics',[])
        self.tables['metric_summary']=[{'unique_active_metric_names':len(metrics),'window_hours':24,
            'custom_metric_name_count':UNKNOWN if any(r.get('is_custom')==UNKNOWN for r in metrics) else sum(r.get('is_custom') is True for r in metrics),
            'explicit_custom_metric_names':sum(r.get('is_custom') is True for r in metrics),
            'unclassified_metric_names':sum(r.get('is_custom')==UNKNOWN for r in metrics),
            'custom_billable_usage_source':'usage.csv: timeseries product family; unique names are not billable timeseries',
            'metrics_with_last_reported_at':sum(r.get('last_reported_at')!=UNKNOWN for r in metrics)}]
        self.tables['trace_availability']=[{'service':r['service'],'traced':r['is_traced'],'usm':r['is_usm'],'source':'apm_service_list'} for r in self.apm]
        for row in self.tables.get('indexed_span_services',[]):
            if isinstance(row.get('indexed_span_event_count'),(int,float)) and row['indexed_span_event_count']>0:
                self.tables['trace_availability'].append({'service':row['service'],'traced':True,'usm':UNKNOWN,
                    'source':'indexed_span_analytics','indexed_span_event_count':row['indexed_span_event_count'],
                    'evidence':'Retained spans in the last 24h; does not prove current live ingestion'})
        if not self.tables['trace_availability']:
            self.tables['trace_availability'].append({'service':UNKNOWN,'traced':UNKNOWN,'usm':UNKNOWN,
                 'source':'APM service list returned no services; do not infer tracing is disabled',
                 'ingested_spans_source':'usage.csv / ingested_spans (API units preserved)',
                 'indexed_spans_source':'usage.csv / indexed_spans','retention_source':'trace_retention.csv; retention duration unknown'})
        self.tables['mttr']=[]
        def summary(name,values,basis):
            values=[v for v in values if isinstance(v,(float,int)) and math.isfinite(v) and v>=0]
            self.tables['mttr'].append({'measurement':name,'sample_count':len(values),'mean_minutes':round(statistics.mean(values),6) if values else UNKNOWN,
                'median_minutes':round(statistics.median(values),6) if values else UNKNOWN,'basis':basis,
                'window_start':self.start.isoformat(),'window_end':self.end.isoformat()})
        summary('latest_monitor_recovery',[r.get('latest_recovery_minutes') for r in self.tables.get('alerts',[])], 'Latest valid trigger/recovery pair per monitor group; recovery in window; snapshot sample, not historical incident MTTR')
        for metric in ['mttr','mttd','mtta','internal_response']:
            summary('incident_'+metric,[r.get(metric+'_minutes') for r in self.tables.get('incidents',[])],
                    {'mttr':'resolved minus created; non-test incidents created in window', 'mttd':'detected minus customer_impact_start; requires both timestamps',
                     'mtta':'Explicit acknowledged_at minus detected_at, when supplied by a verified incident source; internal response is not acknowledgement',
                     'internal_response':'created minus detected; Datadog internal-response definition'}[metric])
        self.coverage.extend([
            {'dataset':'trace_availability','source':'apm_instrumentation','status':'observed_traced_services' if any(r.get('traced') is True for r in self.tables['trace_availability']) else 'unknown',
             'record_count':sum(r.get('traced') is True for r in self.tables['trace_availability']),
             'details':'APM isTraced and positive indexed-span counts provide service evidence. Retained spans do not prove current live ingestion. USM stays unknown unless explicitly returned.'},
            {'dataset':'dashboards','source':'preset_integration_dashboards','status':'listed_presets_only','record_count':sum(str(r.get('origin','')).startswith('integration') for r in self.tables.get('dashboards',[])),
             'details':'Custom dashboards plus presets accessible through manual dashboard lists. Presets absent from accessible lists remain uncovered; UI total may differ.'},
            {'dataset':'trace_retention','source':'retention_duration','status':'verified_configuration' if self.config.get('trace_retention',{}).get('days') else 'not_exposed_by_filter_api','record_count':int(bool(self.config.get('trace_retention',{}).get('days'))),'details':'Duration requires explicit verified configuration; sampling filters do not expose retention days.'},
            {'dataset':'mttr','source':'mtta','status':'explicit_timestamp_input' if any(isinstance(r.get('mtta_minutes'),(float,int)) for r in self.tables.get('incidents',[])) else 'missing_acknowledgement_timestamp','record_count':sum(isinstance(r.get('mtta_minutes'),(float,int)) for r in self.tables.get('incidents',[])),'details':'Only explicit acknowledgement timestamps contribute MTTA; missing values are never zero.'}])
        self.tables['remediation']=remediation_rows(self)

    def collect(self, datasets=None):
        if datasets:
            for name in datasets:self.tables[name]=[]
            self.coverage=[row for row in self.coverage if row['dataset'] not in datasets]
            if 'usage' in datasets:self.usage=[]
        jobs=[
            ('host_inventory','hosts',self.hosts_collect,'hosts_read',''),
            ('applications','apm_service_list',self.apm_services,'apm_read','All environments queried.'),
            ('applications','software_catalog',self.catalog,'apm_service_catalog_read','Includes APM/USM discovered entities; registration is not runtime evidence.'),
            ('applications','service_definitions',self.definitions,'apm_service_catalog_read',''),
            ('service_relationships','software_catalog_relations',self.relations,'apm_service_catalog_read','Includes APM/USM discovered relations.'),
            ('service_relationships','apm_dependencies',self.dependencies,'apm_read','Configured and observed host environments queried; not all unknown environments.'),
            ('dashboards','custom_cloned_dashboards',self.dashboards,'dashboards_read','Offset pagination; details include author, tags, layout and modification time.'),
            ('dashboard_memberships','manual_dashboard_lists',lambda:dashboard_list_members(self),'dashboards_read','Collects accessible list members, including presets. Not a complete inventory of unlisted presets.'),
            ('alerts','monitor_groups',self.alerts,'monitors_read','Priority P1-P5 preserved as severity; missing priority unknown.'),
            ('alert_events','monitor_transitions',self.monitor_events,'events_read','Retained monitor transition history, cursor pagination; only known transitions into Alert count as triggers.'),
            ('usage','hourly_product_usage',self.usage_collect,'usage_read','All product families, current org only, whole UTC hours; delayed billing data may not be reported.'),
            ('trace_retention','retention_filters',self.retention,'apm_retention_filter_read',''),
            ('indexed_span_services','indexed_span_analytics',lambda:span_services(self),'apm_read','Independent retained-span evidence, not total ingestion.'),
            ('rum_applications','rum_applications',self.rum_apps,'rum_apps_read',''),
            ('rum_summary','sessions',lambda:self.rum_aggregate('session'),'rum_apps_read',''),
            ('rum_summary','page_views',lambda:self.rum_aggregate('view'),'rum_apps_read',''),
            ('rum_browsers','browser',lambda:self.rum_aggregate('view','@browser.name'),'rum_apps_read',''),
            ('rum_devices','device',lambda:self.rum_aggregate('view','@device.type'),'rum_apps_read',''),
            ('rum_diagnostics','unfiltered_activity_probe',lambda:rum_probe(self),'rum_apps_read','Checks all event types without app/facet filters.'),
            ('synthetics','configured_tests',self.synthetic_tests,'synthetics_read',''),
            ('synthetic_results','latest_results',self.synthetic_results,'synthetics_read','Latest result sample, not guaranteed complete execution history.'),
            ('incidents','incident_timestamps',self.incidents,'incident_read','Non-test incidents created within reporting window.'),
            ('metrics','active_metric_details',self.metrics,'metrics_read','Unique org-wide active metric names over 24h; volume lookback uses API default.'),
            ('custom_metric_names','monthly_custom_metrics',lambda:custom_metrics(self),'usage_read','Calendar-month custom-metric evidence. Absence does not classify a metric as standard.'),
            ('metric_timestamp_checks','fallback_queries',lambda:retry_metric_timestamps(self),'timeseries_query','Retries only missing timestamps, with sum and a configurable longer lookback.'),
            ('estimated_usage','usage_metric_samples',self.estimated_usage,'timeseries_query','24-hour estimated usage; separate from authoritative billing measurements.')]
        for dataset,source,func,permission,note in jobs:
            if datasets is None or dataset in datasets:
                self.run_source(dataset,source,func,permission,note)
        # A currently alerting group is not a completed monitor recovery.
        for row in self.tables.get('alerts',[]):
            if row.get('status')!='ok':row['latest_recovery_minutes']=None
        self.build_summaries()


DEFAULT_COLUMNS={
    'host_inventory':['hostname','host_id','cloud_provider','region','environment','application_id','tags'],
    'total_host_details':['hostname','host_id','cloud_provider','region','environment','application_id','tags'],
    'applications':['service','source','kind','owner','is_traced','is_usm'],
    'dashboards':['dashboard_id','name','owner','tags','type','last_modified_at'],
    'alerts':['monitor_id','name','severity','status','owner','last_triggered_at','trigger_count'],
    'metrics':['metric','is_custom','last_reported_at','volumes','detail_status'],
    'service_relationships':['source','target','relationship','api'],
    'trace_retention':['filter_id','name','enabled','rate','retention_days'],
    'rum_applications':['application_id','name','type','active'],
    'rum_summary':['event_type','dimension','dimension_value','event_count','distinct_sessions'],
    'rum_browsers':['dimension_value','event_count','distinct_sessions'],
    'rum_devices':['dimension_value','event_count','distinct_sessions'],
    'synthetics':['test_id','name','type','frequency_seconds','locations'],
    'synthetic_results':['test_id','returned_results','success_rate_percent','failures','coverage'],
    'incidents':['incident_id','mttr_minutes','mttd_minutes','mtta_minutes'],
    'usage':['timestamp','product_family','usage_type','value','status'],
    'alert_events':['event_id','monitor_id','groups','timestamp','source_state','destination_state','transition_type'],
    'consumption':['driver','product_family','usage_type','aggregate_value','raw_unit','status'],
    'trace_consumption':['driver','usage_type','aggregate_value','raw_unit','status'],
    'rum_usage':['driver','usage_type','aggregate_value','raw_unit','status'],
    'synthetic_usage':['driver','usage_type','aggregate_value','raw_unit','status'],
    'custom_metric_usage':['driver','usage_type','aggregate_value','raw_unit','status'],
    'estimated_usage':['driver','metric','timestamp','value','unit','quality'],
    'feature_availability':['dataset','source','status','record_count','details'],
}


def export(collector, output, credentials=DEFAULT_FILE, single_file=False):
    output=Path(output)
    single_file=single_file or output.suffix.lower()=='.xlsx'
    workbook=output if output.suffix.lower()=='.xlsx' else output/'datadog_maturity.xlsx'
    directory=workbook.parent if single_file else output
    directory.mkdir(parents=True,exist_ok=True)
    collector.tables.update(consumption_tables(collector.usage))
    collector.tables['vendor_pricing']=pricing_drivers(read_credentials(credentials,tool='datadog').get('pricing',{}),collector.tables['consumption'])
    collector.tables['remediation']=remediation_rows(collector)
    methodology=(Path(__file__).parent/'DATADOG-METHODOLOGY.md').read_text()
    collector.tables['feature_availability']=collector.coverage
    document={'generated_at':datetime.now(timezone.utc).isoformat(),'tool':'datadog',
              'window_start':collector.start.isoformat(),'window_end':collector.end.isoformat(), 'datasets':collector.tables}
    counts={name:len(rows) for name,rows in collector.tables.items()}
    findings=['# Datadog collection findings','',f"Reporting window (UTC): {collector.start.isoformat()} to {collector.end.isoformat()}.",
        '', 'The workbook and CSVs contain Datadog data only. Counts describe returned records, not licensed quantities.', '',
        '| Dataset | Records |','| --- | ---: |',
        *[f'| {name} | {count} |' for name,count in counts.items()], '',
        '## Findings and remaining limits', '',
        '- Applications combine APM, Software Catalog and service definitions. Source rows can refer to the same service; do not sum them as unique applications.',
        (f"- The APM service list returned {len(collector.apm)} services; indexed-span analytics returned {len(collector.tables.get('indexed_span_services',[]))} service buckets. Check trace_availability for positive evidence and its time window. Current live ingestion is not inferred from retained spans."),
        '- Dashboard coverage combines custom dashboards with accessible dashboard-list members, including listed presets. Unlisted presets may remain missing; the UI total is not assumed reconciled.',
        '- RUM application configuration and Synthetic schedules are separate from runtime activity. See active/paused flags and activity coverage before interpreting empty results.',
        '- Consumption uses reported hourly values only, preserves nulls, and lists expected versus reported hours. Partial hours are not extrapolated.',
        '- Custom metric names are matched to monthly custom-metric usage evidence. Names absent from that evidence remain unknown. Monthly custom-timeseries usage and estimated usage stay separate.',
        '- Pricing is organized by billing drivers. Missing contract/plan rates remain unknown; no invoice totals are inferred.',
        '- MTTR, MTTD, internal response and MTTA have distinct definitions and sample counts. Verified incident timestamp inputs can supply acknowledgement; missing timestamps keep MTTA unknown.',
        '', 'See the Methodology and remediation workbook sheets for formulas, scope, pagination, units, references and next actions.', '',
        '## API coverage', '', '| Dataset / source | Status | Records | Details |','| --- | --- | ---: | --- |']
    for row in collector.coverage:
        detail=str(row.get('details','')).replace('|','/').replace('\n',' ')
        findings.append(f"| {row['dataset']} / {row['source']} | {row['status']} | {row['record_count']} | {detail} |")
    findings_text='\n'.join(findings)+'\n'
    tables={'Report Summary':([{'field':k,'value':v} for k,v in document.items() if k!='datasets'],['field','value']),
            'Findings':([{'line':i,'text':line} for i,line in enumerate(findings_text.splitlines(),1)],['line','text']),
            'Methodology':([{'line':i,'text':line} for i,line in enumerate(methodology.splitlines(),1)],['line','text'])}
    tables.update({name:(rows,DEFAULT_COLUMNS.get(name,())) for name,rows in collector.tables.items()})
    write_xlsx(workbook,tables,report=document)
    if not single_file:
        (directory/'methodology.md').write_text(methodology)
        (directory/'findings.md').write_text(findings_text)
        (directory/'datadog_maturity.json').write_text(json.dumps(document,indent=2)+'\n')
        for name,rows in collector.tables.items():
            write_csv(directory/(name+'.csv'),rows,DEFAULT_COLUMNS.get(name,('status',)))


def consumption_driver(family, usage_type):
    text=(family+' '+usage_type).lower()
    if 'container' in text:return 'containers'
    if 'timeseries' in text or 'custom_metric' in text:return 'custom_metrics'
    if 'indexed_spans' in text or 'trace_search' in text:return 'indexed_spans'
    if 'ingested_span' in text:return 'ingested_spans'
    if 'synthetics' in text:
        return 'synthetic_browser_runs' if 'browser' in text else 'synthetic_api_runs' if 'api' in text or 'check_calls' in text else 'synthetic_other_runs'
    if 'rum' in text:return 'rum_sessions'
    if 'log' in text and ('byte' in text or 'ingest' in text):return 'log_ingestion'
    if 'log' in text:return 'indexed_logs'
    if 'host' in text:return 'hosts'
    return family


def consumption_tables(usage):
    groups=defaultdict(list)
    for row in usage:
        groups[(row['product_family'],row['usage_type'])].append(row)
    result=[]
    for (family,kind),rows in sorted(groups.items()):
        values=[r['value'] for r in rows if isinstance(r['value'],(float,int))]
        byte_units='bytes' in kind
        expected_hours=int((datetime.fromisoformat(rows[0]['window_end'])-datetime.fromisoformat(rows[0]['window_start'])).total_seconds()/3600)
        reported_hours=len({r['timestamp'] for r in rows if isinstance(r['value'],(int,float))})
        # Concurrency/cardinality gauges use averages; never add host/session editions together.
        additive=any(s in kind for s in ('bytes','calls','runs','events','sessions')) and not any(s in kind for s in ('host','container'))
        result.append({'driver':consumption_driver(family,kind),'product_family':family,'usage_type':kind,
            'reported_hours':reported_hours,'expected_window_hours':expected_hours,
            'returned_hours':len({r['timestamp'] for r in rows}),
            'aggregate_value':(sum(values) if additive else statistics.mean(values)) if values else UNKNOWN,
            'aggregation':'sum_of_reported_hourly_values' if additive else 'mean_of_reported_hourly_values',
            'raw_unit':'bytes' if byte_units else 'API_usage_type_units',
            'decimal_gb':sum(values)/1e9 if byte_units and values else UNKNOWN,
            'status':'not_reported' if not values else 'partial_hours' if len(values)<len(rows) or reported_hours<expected_hours else 'reported',
            'billing_basis':'Measured usage, not invoice; contract-specific percentile/high-water, allowances and editions are not inferred',
            'window_start':rows[0]['window_start'],'window_end':rows[0]['window_end']})
    return {'consumption':result,
            'trace_consumption':[r for r in result if r['driver'] in ('ingested_spans','indexed_spans')],
            'rum_usage':[r for r in result if r['driver']=='rum_sessions'],
            'synthetic_usage':[r for r in result if r['driver'].startswith('synthetic_')],
            'custom_metric_usage':[r for r in result if r['driver']=='custom_metrics']}


def pricing_drivers(pricing,consumption):
    """Normalize billing drivers. Apply only explicit, unit-labelled contract rates."""
    drivers={'hosts':'host-month','containers':'container-month','log_ingestion':'GB_ingested',
             'indexed_logs':'million_indexed_log_events','custom_metrics':'100_custom_timeseries-month',
             'ingested_spans':'GB_ingested','indexed_spans':'million_indexed_spans',
             'rum_sessions':'1000_sessions','synthetic_api_runs':'10000_API_test_runs',
             'synthetic_browser_runs':'1000_browser_test_runs'}
    configured=pricing.get('datadog',{}).get('drivers',{})
    rows=[]
    for driver,unit in drivers.items():
        override=configured.get(driver,{})
        rows.append({'driver':driver,'billing_unit':override.get('billing_unit',unit),
            'unit_rate':override.get('unit_rate',UNKNOWN),'currency':override.get('currency',UNKNOWN),
            'price_basis':'configured_contract_rate' if override.get('unit_rate') is not None else 'contract_or_product_plan_required',
            'consumption_usage_types':sorted({r['usage_type'] for r in consumption if r['driver']==driver}),
            'consumption_source':'consumption.csv and hourly usage.csv',
            'reference_url':'https://www.datadoghq.com/pricing/list/',
            'notes':'Unit is a comparison basis, not an inferred contracted SKU. No cost from current host counts or missing consumption.'})
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials',type=Path,default=DEFAULT_FILE)
    parser.add_argument('--output',type=Path,default=Path('datadog-out'))
    parser.add_argument('--days',type=int,choices=range(1,91),default=30,metavar='1..90')
    parser.add_argument('--refresh',nargs='+',help='Refresh named datasets in the existing output report, preserving its original time window')
    parser.add_argument('--input-report',type=Path,help='Existing JSON or generated Excel report to refresh or reexport')
    parser.add_argument('--config',type=Path,help='Optional report settings, owners, metadata and verified timestamp inputs')
    parser.add_argument('--single-file',action='store_true',help='Write one Excel workbook including all datasets, findings and methodology')
    parser.add_argument('--reexport',action='store_true',help='Rebuild an input JSON report without any API requests')
    args=parser.parse_args()
    if args.reexport and args.refresh:parser.error('--reexport and --refresh cannot be combined')
    if args.input_report and not (args.refresh or args.reexport):parser.error('--input-report requires --refresh or --reexport')
    load_credentials(args.credentials,tool='datadog')
    try:config=load_config(args.config)
    except (ValueError,OSError) as exc:parser.error(str(exc))
    collector=Collector(None if args.reexport else Client(),args.days,config)
    if args.refresh or args.reexport:
        report_path=args.input_report or (args.output if args.output.suffix.lower()=='.xlsx' else args.output/'datadog_maturity.json')
        if not report_path.exists():parser.error('--refresh requires an existing output/datadog_maturity.json')
        try:saved=read_report(report_path)
        except (ValueError,OSError) as exc:parser.error(str(exc))
        if saved.get('tool') != 'datadog':parser.error('Input report must belong to Datadog')
        collector.start=datetime.fromisoformat(saved['window_start'])
        collector.end=datetime.fromisoformat(saved['window_end'])
        collector.active_start=collector.end-timedelta(days=1)
        collector.tables=saved['datasets']
        valid={'host_inventory','applications','service_relationships','dashboards','alerts','alert_events','usage','trace_retention','rum_applications','rum_summary','rum_browsers','rum_devices','synthetics','synthetic_results','incidents','metrics','estimated_usage','dashboard_memberships','custom_metric_names','metric_timestamp_checks','indexed_span_services','rum_diagnostics'}
        if set(args.refresh or [])-valid:parser.error('Unknown refresh dataset: '+', '.join(sorted(set(args.refresh)-valid)))
        collector.coverage=collector.tables.get('feature_availability',[])
        collector.apm=[r for r in collector.tables.get('applications',[]) if r.get('source')=='apm']
        collector.usage=collector.tables.get('usage',[])
    if args.reexport:collector.build_summaries()
    else:collector.collect(set(args.refresh) if args.refresh else None)
    export(collector,args.output,args.credentials,single_file=args.single_file)
    print('Reports written to',args.output.resolve())
    return 0


if __name__=='__main__':
    raise SystemExit(main())
