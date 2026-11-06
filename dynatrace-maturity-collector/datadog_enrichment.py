"""Additional read-only Datadog coverage and explicitly sourced report inputs."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import urllib.parse

from collector_common import timestamp

UNKNOWN = 'unknown'


def verified_timestamp(value):
    if not isinstance(value,str):return False
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.tzinfo is not None
    except ValueError:return False


def load_config(path=None):
    if path is None:
        path=Path(__file__).parent/'datadog-report.json'
        if not path.exists():return {}
    data=json.loads(Path(path).read_text())
    if not isinstance(data,dict):raise ValueError('Report configuration must be an object')
    allowed={'host_metadata','owners','incident_timestamps','trace_retention','expected_dashboard_count','billing_lag_hours','metric_lookback_days','environments'}
    if set(data)-allowed:raise ValueError('Unknown report configuration fields: '+', '.join(sorted(set(data)-allowed)))
    for field in ('host_metadata','owners','trace_retention'):
        if not isinstance(data.get(field,{}),dict):raise ValueError(field+' must be an object')
    for field in ('incident_timestamps','environments'):
        if not isinstance(data.get(field,[]),list):raise ValueError(field+' must be an array')
    if any(not isinstance(env,str) or not env.strip() for env in data.get('environments',[])):
        raise ValueError('environments must contain nonempty strings')
    for field,low,high,default in [('billing_lag_hours',0,168,72),('metric_lookback_days',1,30,7)]:
        value=data.get(field,default)
        if type(value) is not int or not low<=value<=high:raise ValueError(f'{field} must be an integer from {low} to {high}')
    count=data.get('expected_dashboard_count')
    if count is not None and (type(count) is not int or count<0):raise ValueError('expected_dashboard_count must be a nonnegative integer or null')
    host_fields={'cloud_provider','region','availability_zone','environment','application_id','application','service','owner','account_id','instance_id'}
    for identity,values in data.get('host_metadata',{}).items():
        if not isinstance(values,dict) or set(values)-host_fields or any(not isinstance(v,str) or not v.strip() for v in values.values()):
            raise ValueError('host_metadata entries require supported fields with nonempty string values')
    for kind,values in data.get('owners',{}).items():
        if kind not in ('monitors','dashboards') or not isinstance(values,dict) or any(not isinstance(v,str) or not v.strip() for v in values.values()):
            raise ValueError('owners must map monitors/dashboard IDs to nonempty owner strings')
    retention=data.get('trace_retention',{})
    if retention.get('days') is not None:
        if type(retention['days']) is not int or retention['days']<1 or not isinstance(retention.get('source'),str) or not retention['source'].strip() or not verified_timestamp(retention.get('verified_at')):
            raise ValueError('trace_retention requires positive days, a source, and a valid verified_at timestamp')
    seen=set()
    for row in data.get('incident_timestamps',[]):
        if not isinstance(row,dict) or not isinstance(row.get('incident_id'),(str,int)) or isinstance(row.get('incident_id'),bool) or not str(row['incident_id']).strip() or not isinstance(row.get('source'),str) or not row['source'].strip() or not verified_timestamp(row.get('created_at')):
            raise ValueError('incident_timestamps require incident_id, source, and created_at')
        if str(row['incident_id']) in seen:raise ValueError('Duplicate incident timestamp input')
        seen.add(str(row['incident_id']))
        for field in ('created_at','detected_at','impact_started_at','acknowledged_at','resolved_at'):
            if row.get(field) is not None and not verified_timestamp(row[field]):raise ValueError('Invalid or timezone-free incident timestamp: '+field)
    return data


def dashboard_list_members(c):
    lists=c.client.request('/api/v1/dashboard/lists/manual').get('dashboard_lists',[])
    for item in lists:
        list_id=str(item['id'])
        payload=c.client.request('/api/v2/dashboard/lists/manual/'+urllib.parse.quote(list_id,safe='')+'/dashboards')
        members=payload.get('dashboards',[])
        for member in members:
            if member.get('id') is None:raise RuntimeError('Dashboard-list member is missing its identity')
            author=member.get('author') or {}
            yield {'list_id':list_id,'list_name':item.get('name'),'dashboard_id':str(member.get('id')),
                   'name':member.get('title'),'type':member.get('type'), 'integration_id':member.get('integration_id'),
                   'url':member.get('url'),'author':author.get('handle') or author.get('email') or UNKNOWN,
                   'tags':member.get('tags',[]),'created_at':member.get('created'), 'last_modified_at':member.get('modified')}
        # This API has no documented pagination arguments. Never assume an incomplete list is complete.
        if payload.get('total') is not None and int(payload['total'])>len(members):
            raise RuntimeError(f'Dashboard list {list_id} returned fewer members than its total')


def custom_metrics(c):
    months=set()
    point=c.start.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    while point<=c.end:
        months.add(point.strftime('%Y-%m'))
        point=(point.replace(day=28)+timedelta(days=4)).replace(day=1)
    for month in sorted(months):
        params={'month':month,'limit':5000}
        seen=set()
        while True:
            payload=c.client.request('/api/v1/usage/top_avg_metrics',params,timeout=20)
            for item in payload.get('usage',[]):
                if item.get('metric_name'):
                    yield {'metric':item['metric_name'],'month':month,'metric_category':item.get('metric_category'),
                           'average_hourly_custom_timeseries':item.get('avg_metric_hour'),
                           'maximum_hourly_custom_timeseries':item.get('max_metric_hour'),
                           'source':'usage/top_avg_metrics','period_basis':'calendar month; may extend beyond reporting window'}
            cursor=payload.get('metadata',{}).get('pagination',{}).get('next_record_id')
            if not cursor:break
            if cursor in seen:raise RuntimeError('Custom metric usage repeated a cursor')
            seen.add(cursor);params['next_record_id']=cursor


def span_services(c):
    body={'filter':{'from':c.active_start.isoformat(),'to':c.end.isoformat(),'query':'*'},
          'compute':[{'aggregation':'count','type':'total'}], 'group_by':[{'facet':'service','limit':1000}]}
    payload=c.client.request('/api/v2/spans/analytics/aggregate',body={'data':{'type':'aggregate_request','attributes':body}})
    meta=payload.get('meta',{})
    if meta.get('warnings') or meta.get('status') in ('timeout','partial'):
        raise RuntimeError('Indexed-span aggregation returned partial results/warnings')
    buckets=payload.get('data',[])
    for item in buckets:
        attrs=item.get('attributes',{})
        yield {'service':attrs.get('by',{}).get('service',UNKNOWN),
               'indexed_span_event_count':attrs.get('computes',attrs.get('compute',{})).get('c0',UNKNOWN),
               'evidence':'retained indexed-span events; not total ingested spans or billing usage',
               'window_start':c.active_start.isoformat(),'window_end':c.end.isoformat()}
    if len(buckets)>=1000:raise RuntimeError('Indexed-span service groups reached API bucket limit; partial service discovery')


def rum_probe(c):
    body={'filter':{'from':c.start.isoformat(),'to':c.end.isoformat(),'query':'*'},
          'compute':[{'aggregation':'count','type':'total'}]}
    payload=c.client.request('/api/v2/rum/analytics/aggregate',body=body)
    meta=payload.get('meta',{})
    if meta.get('warnings') or meta.get('status') in ('timeout','partial'):
        raise RuntimeError('Unfiltered RUM probe returned partial results/warnings')
    buckets=payload.get('data',{}).get('buckets',[])
    values=[b.get('computes',{}).get('c0') for b in buckets]
    known=[v for v in values if isinstance(v,(int,float))]
    count=sum(known) if known else 0 if not buckets else UNKNOWN
    yield {'scope':'all RUM event types, applications and environments; no group-by filter',
           'retained_event_count':count,'configured_applications':len(c.tables.get('rum_applications',[])),
           'active_applications':sum(r.get('active') is True for r in c.tables.get('rum_applications',[])),
           'diagnosis':'activity_returned' if isinstance(count,(int,float)) and count>0 else 'no_retained_activity_returned' if count==0 else UNKNOWN,
           'next_action':'Compare app/site/consent/sampling and traffic in RUM Explorer; an empty search does not prove SDK failure',
           'window_start':c.start.isoformat(),'window_end':c.end.isoformat()}


def retry_metric_timestamps(c):
    missing=[r for r in c.tables.get('metrics',[]) if r.get('last_reported_at') in (None,UNKNOWN)]
    for days,aggregator in [(1,'sum'),(c.config.get('metric_lookback_days',7),'avg')]:
        for pos in range(0,len(missing),20):
            rows=[r for r in missing[pos:pos+20] if r.get('last_reported_at') in (None,UNKNOWN)]
            if not rows:continue
            query=', '.join(aggregator+':'+r['metric']+'{*}' for r in rows)
            try:
                payload=c.client.request('/api/v1/query',{'from':int((c.end-timedelta(days=days)).timestamp()),'to':int(c.end.timestamp()),'query':query})
                if payload.get('status')=='error' or payload.get('error'):raise RuntimeError('Metric fallback query failed')
                latest={}
                for series in payload.get('series') or []:
                    points=[p[0] for p in series.get('pointlist',[]) if len(p)>1 and p[1] is not None]
                    if points:latest[series.get('metric')]=max(latest.get(series.get('metric'),0),max(points))
                for row in rows:
                    value=latest.get(row['metric'])
                    if value:
                        row['last_reported_at']=timestamp(value).isoformat()
                        row['last_reported_basis']=f'latest nonnull {aggregator} query rollup bucket over {days} days; not exact raw ingestion time'
                    yield {'metric':row['metric'],'lookback_days':days,'aggregator':aggregator,
                           'status':'timestamp_recovered' if value else 'no_samples_returned'}
            except Exception as exc:
                for row in rows:yield {'metric':row['metric'],'lookback_days':days,'status':'unavailable','detail_status':str(exc)}


def apply_enrichment(c):
    """Merge only explicit identities/evidence; never turn absence into zero or false."""
    applications=[row for row in c.tables.get('applications',[]) if row.get('source')!='indexed_span_analytics']
    for row in c.tables.get('indexed_span_services',[]):
        if row.get('service') not in (None,UNKNOWN,'') and isinstance(row.get('indexed_span_event_count'),(int,float)) and row['indexed_span_event_count']>0:
            applications.append({'service':row['service'],'source':'indexed_span_analytics','kind':'service',
                'is_traced':True,'is_usm':UNKNOWN,'environment':UNKNOWN,
                'evidence':'Retained indexed-span events over 24h; not proof of current live ingestion',
                'indexed_span_event_count':row['indexed_span_event_count']})
    c.tables['applications']=applications
    for name in ('rum_applications','synthetics'):
        for row in c.tables.get(name,[]):
            if not row.get('owner_basis'):
                row['author']=row.get('owner',UNKNOWN)
                tags=row.get('tags') if isinstance(row.get('tags'),list) else []
                owners=[t.split(':',1)[1] for t in tags if isinstance(t,str) and ':' in t and t.split(':',1)[0].lower() in ('owner','team')]
                row['owner']='; '.join(sorted(set(owners))) or UNKNOWN
                row['owner_basis']='explicit owner/team tag; author is separate'
    dashboards=[row for row in c.tables.get('dashboards',[]) if not str(row.get('origin','')).endswith('_dashboard_list')]
    c.tables['dashboards']=dashboards
    def dashboard_kind(row):
        origin=str(row.get('origin',''))
        return 'integration' if origin.startswith('integration') else 'host' if origin.startswith('host_') else 'custom'
    by_key={(dashboard_kind(r),str(r['dashboard_id'])):r for r in dashboards}
    for row in dashboards:
        if 'author_handle' in str(row.get('owner_basis','')):
            row['author']=row.get('owner',UNKNOWN)
            tags=row.get('tags') if isinstance(row.get('tags'),list) else []
            owners=[t.split(':',1)[1] for t in tags if isinstance(t,str) and t.split(':',1)[0].lower() in ('owner','team') and ':' in t]
            row['owner']='; '.join(sorted(set(owners))) or UNKNOWN
            row['owner_basis']='explicit owner/team tag; author is separate'
        row['dashboard_lists']=[]
    for member in c.tables.get('dashboard_memberships',[]):
        kind='integration' if str(member.get('type','')).startswith('integration') else 'host' if str(member.get('type','')).startswith('host_') else 'custom'
        key=(kind,str(member['dashboard_id']))
        if key not in by_key:
            row={k:member.get(k,UNKNOWN) for k in ('dashboard_id','name','tags','type','url','created_at','last_modified_at')}
            row.update(owner=UNKNOWN,author=member.get('author',UNKNOWN),owner_basis='unknown; list author is separate',
                       origin=kind+'_dashboard_list',detail_status='available_from_dashboard_list',dashboard_lists=[])
            by_key[key]=row;dashboards.append(row)
        by_key[key]['dashboard_lists'].append(member['list_id'])
    for row in dashboards:
        row['dashboard_lists']=sorted(set(row['dashboard_lists']))
        owner=c.config.get('owners',{}).get('dashboards',{}).get(str(row['dashboard_id']))
        if owner:
            row['owner']=owner;row['owner_basis']='explicit report configuration'
    for row in c.tables.get('alerts',[]):
        if row.get('owner') in (None,UNKNOWN,''):
            owner=c.config.get('owners',{}).get('monitors',{}).get(str(row['monitor_id']))
            if owner:row.update(owner=owner,owner_basis='explicit report configuration')
    lag_cutoff=datetime.now(timezone.utc)-timedelta(hours=c.config.get('billing_lag_hours',72))
    for row in c.tables.get('usage',[]):
        if row.get('value') is None:
            ts=timestamp(row.get('timestamp'))
            row['status']='billing_pending' if ts and ts>=lag_cutoff else 'not_reported'
        elif row.get('value')!=UNKNOWN:row['status']='reported'
    for row in c.tables.get('host_inventory',[]):
        provenance=row.setdefault('metadata_provenance',{})
        for identity in (row.get('hostname'),str(row.get('host_id'))):
            for field,value in c.config.get('host_metadata',{}).get(identity,{}).items():
                if row.get(field) in (None,UNKNOWN,''):
                    row[field]=value;provenance[field]='report configuration: '+identity
        row['metadata_status']='partial' if any(row.get(k) in (None,UNKNOWN,'') for k in ('cloud_provider','region','environment','application_id')) else 'available'
    custom={}
    for row in c.tables.get('custom_metric_names',[]):custom.setdefault(row['metric'],set()).add(row['month'])
    for row in c.tables.get('metrics',[]):
        if row['metric'] in custom:
            row['custom_usage_months']=sorted(custom[row['metric']])
            if row.get('is_custom') in (None,UNKNOWN):
                row['is_custom']=True;row['custom_classification_basis']='custom metric usage API evidence in '+', '.join(sorted(custom[row['metric']]))
    retention=c.config.get('trace_retention',{})
    if retention.get('days'):
        if not c.tables.get('trace_retention'):
            c.tables['trace_retention']=[{'filter_id':UNKNOWN,'name':'organization retention setting'}]
        for row in c.tables['trace_retention']:
            row.update(retention_days=retention['days'],retention_basis='explicit verified configuration',
                       retention_source=retention['source'],retention_verified_at=retention['verified_at'])
    incident_map={str(row['incident_id']):row for row in c.tables.get('incidents',[]) if row.get('incident_id') is not None}
    for extra in c.config.get('incident_timestamps',[]):
        created=timestamp(extra['created_at'])
        if not c.start<=created<=c.end:continue
        identity=str(extra['incident_id'])
        row=incident_map.get(identity)
        if row is None:
            row={'incident_id':identity,'created_at':extra['created_at'],'source':'explicit external timestamp input','timestamp_source':extra['source']}
            c.tables.setdefault('incidents',[]).append(row);incident_map[identity]=row
        for field in ('created_at','detected_at','impact_started_at','acknowledged_at','resolved_at'):
            if row.get(field) in (None,UNKNOWN,'') and extra.get(field):row[field]=extra[field]
        row['timestamp_source']=extra['source']
        for output,first,last in [('mttr_minutes','created_at','resolved_at'),('mttd_minutes','impact_started_at','detected_at'),('mtta_minutes','detected_at','acknowledged_at'),('internal_response_minutes','detected_at','created_at')]:
            a,b=timestamp(row.get(first)),timestamp(row.get(last))
            row[output]=round((b-a).total_seconds()/60,6) if a and b and c.start<=created<=c.end and a<=b<=c.end else UNKNOWN
        row['mtta_basis']='explicit acknowledged_at minus detected_at; source: '+extra['source']
        ack,detected=timestamp(row.get('acknowledged_at')),timestamp(row.get('detected_at'))
        row['acknowledgement_status']='invalid_timestamp_order' if ack and detected and ack<detected else 'available' if isinstance(row['mtta_minutes'],(float,int)) else 'missing_timestamps'


def remediation_rows(c):
    checks=[]
    def add(issue,status,action,evidence):checks.append({'issue':issue,'status':status,'action':action,'evidence':evidence})
    dashboards=c.tables.get('dashboards',[])
    expected=c.config.get('expected_dashboard_count')
    add('dashboard_completeness','unreconciled' if expected is None or len(dashboards)!=expected else 'expected_count_matched',
        'Compare the same organization and UI filter. Dashboard-list APIs cover listed presets only; provide expected_dashboard_count for reconciliation.',
        {'captured':len(dashboards),'expected':expected,'listed_integration_presets':sum(str(r.get('origin','')).startswith('integration') for r in dashboards)})
    span_rows=c.tables.get('indexed_span_services',[])
    observed=[r for r in span_rows if isinstance(r.get('indexed_span_event_count'),(int,float)) and r['indexed_span_event_count']>0]
    add('apm_tracing','observed' if observed or any(r.get('is_traced') is True for r in c.apm) else 'requires_datadog_validation',
        'If APM Explorer is also empty, verify tracer/Agent connectivity, DD_SERVICE/DD_ENV and traffic. Catalog registration alone is insufficient.',
        {'apm_services':len(c.apm),'indexed_span_services':len(observed)})
    links=c.tables.get('service_relationships',[])
    runtime=sum(r.get('api')=='apm_service_dependencies' for r in links)
    add('service_dependencies','runtime_links_observed' if runtime else 'catalog_links_only' if links else 'no_links_returned',
        'Compare Service Map and exact environments. Catalog-defined relationships are not measured runtime calls; the collector does not invent links from co-located services.',
        {'relationships':len(links),'runtime_calls':runtime})
    paused=sum(r.get('status')=='paused' for r in c.tables.get('synthetics',[]))
    add('synthetic_execution','paused_tests' if paused else 'review_results',
        'Review paused tests and resume only intended schedules in Datadog. The collector never starts tests or changes scheduling.',{'paused_tests':paused})
    rum_count=[r.get('retained_event_count') for r in c.tables.get('rum_diagnostics',[])]
    add('rum_activity','observed' if any(isinstance(v,(int,float)) and v>0 for v in rum_count) else 'requires_datadog_validation',
        'Verify applicationId, client token, site, consent, nonzero session sampling and real traffic in RUM Explorer.',{'unfiltered_event_counts':rum_count})
    add('missing_host_metadata','requires_source_tags_or_mapping' if any(r.get('metadata_status')=='partial' for r in c.tables.get('host_inventory',[])) else 'complete',
        'Populate source tags or exact host_metadata mappings in datadog-report.json; the collector fills only missing fields.',
        {'hosts_with_partial_metadata':sum(r.get('metadata_status')=='partial' for r in c.tables.get('host_inventory',[]))})
    missing=sum(r.get('last_reported_at') in (None,UNKNOWN) for r in c.tables.get('metrics',[]))
    add('metric_timestamps','missing_samples' if missing else 'complete','Check metric emitters and query access; sum and longer-window fallback queries already attempted.',{'missing_timestamps':missing})
    custom=sum(r.get('is_custom') is True for r in c.tables.get('metrics',[]))
    unknown=sum(r.get('is_custom') in (None,UNKNOWN) for r in c.tables.get('metrics',[]))
    add('custom_metric_classification','partially_classified' if unknown else 'classified',
        'Positive monthly usage evidence identifies custom metric names. Unmatched names remain unknown; reconcile with Plan & Usage rather than assuming standard metrics.',
        {'identified_custom_metric_names':custom,'unclassified_names':unknown})
    missing_owners={name:sum(r.get('owner') in (None,UNKNOWN,'') for r in c.tables.get(name,[])) for name in ('alerts','dashboards')}
    add('ownership','requires_source_tags_or_mapping' if any(missing_owners.values()) else 'complete',
        'Provide explicit owner/team tags or owners.monitors/owners.dashboards ID mappings. Authors are not assigned owners.',missing_owners)
    add('mtta','available' if any(isinstance(r.get('mtta_minutes'),(int,float)) for r in c.tables.get('incidents',[])) else 'missing_acknowledgement',
        'Supply explicit incident_timestamps from the incident system, including detected_at, acknowledged_at, created_at and source. Internal response is not MTTA.',{})
    pending=sum(r.get('status')=='billing_pending' for r in c.tables.get('usage',[]))
    add('usage_reporting','billing_pending' if pending else 'review_null_hours',
        'Retry after the reporting lag. Old missing measurements remain unknown; estimates are separate from billing data.',{'recent_pending_measurements':pending})
    missing_rates=sum(r.get('unit_rate') in (None,UNKNOWN) for r in c.tables.get('vendor_pricing',[]))
    add('retention_and_rates','requires_verified_settings' if not c.config.get('trace_retention',{}).get('days') or missing_rates else 'configured',
        'Add verified trace_retention days/source/date and contracted pricing.datadog.drivers rates in credentials.json. No prices or retention durations are inferred.',{'billing_drivers_missing_rates':missing_rates})
    return checks
