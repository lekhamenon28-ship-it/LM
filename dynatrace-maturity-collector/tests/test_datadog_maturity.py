import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree
from zipfile import ZipFile
from datetime import datetime, timedelta, timezone

from datadog_maturity import Client, Collector, APIError, host_row, tag_map, consumption_tables, pricing_drivers, UNKNOWN
from report_export import write_csv, write_xlsx


class DatadogMaturityTests(unittest.TestCase):
    def test_tags_sources_conflicts_and_cloud_identity(self):
        row=host_row({'id':1,'name':' VM-1 ', 'sources':['azure'],
            'tags_by_source':{'azure':['Region:centralindia','ENV:prod','app-id:abc:123','env:staging']}})
        self.assertEqual(row['hostname'],'VM-1')
        self.assertEqual(row['cloud_provider'],'azure')
        self.assertEqual(row['region'],'centralindia')
        self.assertEqual(row['application_id'],'abc:123')
        self.assertEqual(row['environment'],'prod; staging')
        self.assertNotIn('entity_id',row)
        self.assertEqual(tag_map({'foo':['bar','baz']}),{'foo':['bar','baz']})

    def test_dashboards_paginate_and_repeated_offsets_fail(self):
        client=Client.__new__(Client)
        client.request=Mock(side_effect=[{'dashboards':[{'id':'a'},{'id':'b'}]}, {'dashboards':[{'id':'c'}]}])
        pages=list(client.offset('/api/v1/dashboard','dashboards',2,'count','start'))
        self.assertEqual(sum(len(p['dashboards']) for p in pages),3)
        self.assertEqual(client.request.call_args.args[1]['start'],2)
        client.request=Mock(return_value={'data':[{'id':'a'}]})
        with self.assertRaisesRegex(RuntimeError,'repeated'):
            list(client.offset('/example',size=1))

    def test_incident_short_final_page_zero_next_offset_is_terminal(self):
        client=Client.__new__(Client)
        client.request=Mock(side_effect=[{'data':[{'id':'1'},{'id':'2'}],'meta':{'pagination':{'next_offset':2}}},
                                        {'data':[{'id':'3'}],'meta':{'pagination':{'next_offset':0}}}])
        self.assertEqual(sum(len(p['data']) for p in client.offset('/api/v2/incidents',size=2)),3)

    def test_partial_pages_are_preserved_with_partial_status(self):
        collector=Collector(Mock())
        def source():
            yield {'id':1}
            raise APIError(403,'/example','forbidden')
        collector.run_source('example','test',source)
        self.assertEqual(collector.tables['example'],[{'id':1}])
        self.assertEqual(collector.coverage[0]['status'],'partial')
        self.assertEqual(collector.coverage[0]['http_status'],403)

    def test_no_data_does_not_mean_disabled(self):
        collector=Collector(Mock())
        collector.run_source('apm','apm',lambda:iter([]))
        self.assertEqual(collector.coverage[0]['status'],'available_empty')
        self.assertIn('enablement is unknown',collector.coverage[0]['details'])

    def test_trigger_history_deduplication_and_renotifications(self):
        collector=Collector(Mock())
        collector.coverage=[{'dataset':'alert_events','status':'available'}]
        event={'event_id':'1','monitor_id':2,'groups':['env:prod','host:a'],'timestamp':'2026-11-01T00:00:00Z',
               'source_state':'OK','destination_state':'Alert','transition_type':'alert'}
        collector.tables={'alerts':[{'monitor_id':2,'group':'host:a,env:prod','last_triggered_at':UNKNOWN}],
            'alert_events':[event,dict(event),{**event,'event_id':'2','source_state':'Alert','transition_type':'renotify'}]}
        collector.apply_monitor_history()
        self.assertEqual(collector.tables['alerts'][0]['trigger_count'],1)
        collector.coverage[0]['status']='partial'
        collector.apply_monitor_history()
        self.assertEqual(collector.tables['alerts'][0]['trigger_count'],UNKNOWN)

    def test_consumption_nulls_gauges_and_additive_values(self):
        def row(family,kind,value,time):
            return {'product_family':family,'usage_type':kind,'value':value,'timestamp':time,'window_start':'2026-11-01T00:00:00+00:00','window_end':'2026-11-01T02:00:00+00:00'}
        data=[row('infra_hosts','host_count',10,'a'),row('infra_hosts','host_count',20,'b'),
              row('ingested_spans','ingested_events_bytes',1e9,'a'),row('ingested_spans','ingested_events_bytes',None,'b'),
              row('timeseries','num_custom_timeseries',None,'a')]
        rows=consumption_tables(data)
        hosts=next(r for r in rows['consumption'] if r['driver']=='hosts')
        self.assertEqual(hosts['aggregate_value'],15)
        self.assertEqual(rows['trace_consumption'][0]['decimal_gb'],1)
        self.assertEqual(rows['trace_consumption'][0]['status'],'partial_hours')
        self.assertEqual(rows['custom_metric_usage'][0]['aggregate_value'],UNKNOWN)
        prices=pricing_drivers({},rows['consumption'])
        self.assertTrue(all(r['unit_rate']==UNKNOWN for r in prices))

    def test_mttr_mean_median_and_missing_mtta(self):
        collector=Collector(Mock())
        collector.tables={'alerts':[{'monitor_id':1,'group':'','last_triggered_at':UNKNOWN,'latest_recovery_minutes':10},
                                    {'monitor_id':2,'group':'','last_triggered_at':UNKNOWN,'latest_recovery_minutes':30}],
                          'incidents':[{'mttr_minutes':-1,'mttd_minutes':5,'mtta_minutes':None,'internal_response_minutes':1},
                                       {'mttr_minutes':20,'mttd_minutes':None,'mtta_minutes':None,'internal_response_minutes':2}]}
        collector.build_summaries()
        summary={r['measurement']:r for r in collector.tables['mttr']}
        self.assertEqual(summary['latest_monitor_recovery']['median_minutes'],20)
        self.assertEqual(summary['incident_mttr']['sample_count'],1)
        self.assertEqual(summary['incident_mtta']['sample_count'],0)
        self.assertEqual(summary['incident_mtta']['mean_minutes'],UNKNOWN)

    def test_read_only_and_pagination_origin_guard(self):
        client=Client.__new__(Client)
        client.base='https://api.datadoghq.com';client.headers={};client.blocked={}
        with self.assertRaisesRegex(RuntimeError,'outside'):
            client.request('https://evil.example/next')
        with self.assertRaisesRegex(RuntimeError,'read-only'):
            client.request('/api/v1/synthetics/tests/trigger',body={})

    def test_csv_excel_are_typed_and_formula_safe(self):
        rows=[{'name':'=DANGEROUS()','count':12,'tags':['env:prod'],'missing':None}]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_csv(root/'out.csv',rows)
            with (root/'out.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(next(csv.DictReader(f))['name'],"'=DANGEROUS()")
            write_xlsx(root/'out.xlsx',{'hosts':(rows,())})
            with ZipFile(root/'out.xlsx') as z:
                self.assertIsNone(z.testzip())
                for name in z.namelist():ElementTree.fromstring(z.read(name))
                sheet=ElementTree.fromstring(z.read('xl/worksheets/sheet1.xml'))
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                self.assertEqual(sheet.find('.//s:c[@r="A2"]',ns).get('t'),'inlineStr')
                self.assertIsNone(sheet.find('.//s:f',ns))

    def test_usage_is_daily_and_preserves_nulls_and_cursors(self):
        client=Mock()
        collector=Collector(client)
        collector.start=datetime(2026,11,1,tzinfo=timezone.utc)
        collector.end=collector.start+timedelta(days=2)
        entry={'attributes':{'product_family':'timeseries','timestamp':'2026-11-01T00:00:00Z',
            'measurements':[{'usage_type':'num_custom_timeseries','value':None}]}}
        client.request.side_effect=[{'data':[entry],'meta':{'pagination':{'next_record_id':'next'}}},
                                    {'data':[],'meta':{}},{'data':[],'meta':{}}]
        rows=list(collector.usage_collect())
        self.assertEqual(rows[0]['status'],'not_reported')
        self.assertIsNone(rows[0]['value'])
        self.assertEqual(client.request.call_count,3)
        args=client.request.call_args.args[1]
        self.assertEqual(args['filter[timestamp][start]'],'2026-11-01T00:00:00+00:00')

    def test_metrics_are_unique_and_last_reported_is_nonnull_bucket(self):
        client=Mock()
        def request(path,params=None):
            if path=='/api/v1/metrics':return {'metrics':['a.metric','a.metric']}
            if path=='/api/v1/query':return {'series':[{'metric':'a.metric','pointlist':[[1700000000000,1],[1700000060000,None]]}]}
            if path.endswith('/volumes'):return {'data':{'attributes':{'distinct_volume':8}}}
            return {'type':'gauge','integration':'system'}
        client.request.side_effect=request
        rows=list(Collector(client).metrics())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['last_reported_at'],'2023-11-14T22:13:20+00:00')
        self.assertEqual(rows[0]['is_custom'],UNKNOWN)
        self.assertEqual(rows[0]['volumes']['distinct_volume'],8)

    def test_estimated_usage_keeps_counts_and_gauges_separate(self):
        client=Mock()
        client.request.return_value={'series':[{'pointlist':[[1700000000000,5]]}]}
        collector=Collector(client)
        collector.tables={'metrics':[{'metric':'datadog.estimated_usage.apm.ingested_spans','type':'count'},
                                     {'metric':'datadog.estimated_usage.metrics.custom','type':'gauge'}]}
        rows=list(collector.estimated_usage())
        self.assertEqual(len(rows),2)
        self.assertIn('as_count()',client.request.call_args_list[0].args[1]['query'])
        self.assertIn('rollup(avg,3600)',client.request.call_args_list[1].args[1]['query'])
        self.assertTrue(all('not billing' in r['quality'] for r in rows))


if __name__=='__main__':unittest.main()
