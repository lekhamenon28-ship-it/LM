import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from datadog_maturity import Collector, export
from report_export import read_report, write_xlsx
from datadog_enrichment import (load_config, dashboard_list_members, custom_metrics, span_services,
    rum_probe, retry_metric_timestamps, apply_enrichment, remediation_rows)


class EnrichmentTests(unittest.TestCase):
    def test_list_presets_deduplicate_without_id_collisions_and_refresh_is_idempotent(self):
        c=Collector(Mock())
        c.tables={'dashboards':[{'dashboard_id':'1','name':'custom','origin':'custom_or_cloned'}],
          'dashboard_memberships':[{'list_id':'a','dashboard_id':'1','name':'preset','type':'integration_screenboard'},
                                   {'list_id':'b','dashboard_id':'1','name':'preset','type':'integration_screenboard'}]}
        apply_enrichment(c);apply_enrichment(c)
        self.assertEqual(len(c.tables['dashboards']),2)
        preset=next(r for r in c.tables['dashboards'] if r['origin'].startswith('integration'))
        self.assertEqual(preset['dashboard_lists'],['a','b'])
        c.tables['dashboard_memberships']=[];apply_enrichment(c)
        self.assertEqual(len(c.tables['dashboards']),1)

    def test_incomplete_dashboard_list_preserves_rows_but_reports_partial(self):
        client=Mock()
        client.request.side_effect=[{'dashboard_lists':[{'id':1,'name':'test'}]},
                                    {'total':2,'dashboards':[{'id':'a','type':'integration_timeboard'}]}]
        c=Collector(client)
        c.run_source('dashboard_memberships','lists',lambda:dashboard_list_members(c))
        self.assertEqual(c.coverage[0]['status'],'partial')
        self.assertEqual(len(c.tables['dashboard_memberships']),1)

    def test_custom_usage_paginates_and_absent_names_remain_unknown(self):
        client=Mock();c=Collector(client)
        c.start=datetime(2026,11,1,tzinfo=timezone.utc);c.end=c.start+timedelta(days=2)
        client.request.side_effect=[{'usage':[{'metric_name':'custom.a','avg_metric_hour':3}],
            'metadata':{'pagination':{'next_record_id':'cursor'}}}, {'usage':[],'metadata':{}}]
        c.tables={'custom_metric_names':list(custom_metrics(c)),
                  'metrics':[{'metric':'custom.a','is_custom':'unknown'},{'metric':'other','is_custom':'unknown'}]}
        apply_enrichment(c)
        self.assertIs(c.tables['metrics'][0]['is_custom'],True)
        self.assertEqual(c.tables['metrics'][1]['is_custom'],'unknown')
        self.assertEqual(client.request.call_args.args[1]['next_record_id'],'cursor')

    def test_indexed_spans_provide_independent_trace_evidence(self):
        client=Mock();c=Collector(client)
        client.request.return_value={'data':[{'attributes':{'by':{'service':'api'},'computes':{'c0':4}}}],'meta':{}}
        c.tables={'indexed_span_services':list(span_services(c))}
        body=client.request.call_args.kwargs['body']
        self.assertEqual(body['data']['type'],'aggregate_request')
        self.assertEqual(body['data']['attributes']['group_by'][0]['facet'],'service')
        c.build_summaries()
        row=c.tables['trace_availability'][0]
        self.assertEqual(row['service'],'api');self.assertIs(row['traced'],True)
        self.assertEqual(row['usm'],'unknown')
        self.assertEqual(next(r for r in c.coverage if r['source']=='apm_instrumentation')['status'],'observed_traced_services')

    def test_span_warnings_do_not_become_zero_or_positive_evidence(self):
        client=Mock();c=Collector(client)
        client.request.return_value={'data':[],'meta':{'warnings':[{'code':'timeout'}]}}
        with self.assertRaisesRegex(RuntimeError,'partial'):list(span_services(c))

    def test_rum_probe_has_no_event_type_or_facet_filter(self):
        client=Mock();c=Collector(client)
        client.request.return_value={'data':{'buckets':[{'computes':{'c0':12}}]},'meta':{}}
        rows=list(rum_probe(c))
        self.assertEqual(rows[0]['retained_event_count'],12)
        body=client.request.call_args.kwargs['body']
        self.assertEqual(body['filter']['query'],'*');self.assertNotIn('group_by',body)

    def test_verified_host_mapping_fills_only_missing_values_and_owner_is_not_author(self):
        c=Collector(Mock(),config={'host_metadata':{'host-a':{'region':'west','environment':'prod'}},
            'owners':{'monitors':{'7':'infra'}}})
        c.tables={'host_inventory':[{'hostname':'host-a','host_id':'1','region':'east','environment':'unknown'}],
            'alerts':[{'monitor_id':7,'owner':'unknown'}],
            'dashboards':[{'dashboard_id':'abc','owner':'creator@example.com','owner_basis':'author_handle (creator; not an assigned team owner)'}]}
        apply_enrichment(c)
        self.assertEqual(c.tables['host_inventory'][0]['region'],'east')
        self.assertEqual(c.tables['host_inventory'][0]['environment'],'prod')
        self.assertEqual(c.tables['alerts'][0]['owner'],'infra')
        self.assertEqual(c.tables['dashboards'][0]['owner'],'unknown')
        self.assertEqual(c.tables['dashboards'][0]['author'],'creator@example.com')

    def test_mtta_requires_explicit_acknowledgement_and_valid_order(self):
        now=datetime.now(timezone.utc)
        extra={'incident_id':'i-1','source':'incident-system export','created_at':(now-timedelta(hours=1)).isoformat(),
               'detected_at':(now-timedelta(hours=1,minutes=5)).isoformat(),
               'acknowledged_at':(now-timedelta(hours=1,minutes=2)).isoformat()}
        c=Collector(Mock(),config={'incident_timestamps':[extra]});c.build_summaries()
        self.assertEqual(c.tables['incidents'][0]['mtta_minutes'],3)
        summary=next(r for r in c.tables['mttr'] if r['measurement']=='incident_mtta')
        self.assertEqual(summary['sample_count'],1)
        extra['acknowledged_at']=(now-timedelta(hours=2)).isoformat()
        bad=Collector(Mock(),config={'incident_timestamps':[extra]});bad.build_summaries()
        self.assertEqual(bad.tables['incidents'][0]['mtta_minutes'],'unknown')
        self.assertEqual(bad.tables['incidents'][0]['acknowledgement_status'],'invalid_timestamp_order')

    def test_configuration_validation_rejects_unverified_retention_and_duplicate_incidents(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json'
            for config in [{'trace_retention':{'days':15}}, {'billing_lag_hours':-1}, {'host_metadata':{'a':{'unsupported':'value'}}}]:
                p.write_text(json.dumps(config))
                with self.assertRaises(ValueError):load_config(p)

    def test_metric_fallback_does_not_overwrite_existing_timestamps(self):
        client=Mock();c=Collector(client)
        ts=int((c.end-timedelta(hours=2)).timestamp()*1000)
        client.request.return_value={'series':[{'metric':'missing','pointlist':[[ts,3]]}]}
        c.tables={'metrics':[{'metric':'present','last_reported_at':'keep'}, {'metric':'missing','last_reported_at':'unknown'}]}
        rows=list(retry_metric_timestamps(c))
        self.assertEqual(c.tables['metrics'][0]['last_reported_at'],'keep')
        self.assertNotEqual(c.tables['metrics'][1]['last_reported_at'],'unknown')
        self.assertEqual(len(rows),1)
        self.assertNotIn('present',client.request.call_args.args[1]['query'])

    def test_synthetic_legacy_fallback_uses_explicit_passed_flag(self):
        client=Mock();c=Collector(client)
        c.tables={'synthetics':[{'test_id':'abc','type':'api','status':'paused'}]}
        client.request.side_effect=[{'data':[]},{'results':[{'result':{'passed':True}},{'result':{'passed':False}}]}]
        row=list(c.synthetic_results())[0]
        self.assertEqual(row['success_rate_percent'],50)
        self.assertEqual(row['test_schedule_status'],'paused')
        self.assertEqual(row['source'],'v1_latest_results_sample')
        self.assertIn('/api/v1/synthetics/tests/',client.request.call_args.args[0])

    def test_numeric_browser_status_is_not_guessed(self):
        client=Mock();c=Collector(client)
        c.tables={'synthetics':[{'test_id':'abc','type':'browser'}]}
        client.request.side_effect=[{'data':[]},{'results':[{'status':0,'result':{'errorCount':0}}]}]
        row=list(c.synthetic_results())[0]
        self.assertEqual(row['success_rate_percent'],'unknown')
        self.assertEqual(row['unclassified_results'],1)

    def test_single_file_export_contains_notes_and_all_datasets_only(self):
        c=Collector(None);c.build_summaries()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);creds=root/'creds.json';creds.write_text('{}');creds.chmod(0o600)
            out=root/'output/report.xlsx'
            export(c,out,creds,single_file=True)
            self.assertEqual(list(out.parent.iterdir()),[out])
            ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            with ZipFile(out) as z:
                self.assertIsNone(z.testzip())
                sheets=ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',ns)
                names={s.get('name') for s in sheets}
                self.assertTrue({'Report Summary','Findings','Methodology','remediation'}<=names)
                for name in z.namelist():ET.fromstring(z.read(name))
                self.assertEqual(next(s for s in sheets if s.get('name')=='Report Data').get('state'),'hidden')
            self.assertEqual(read_report(out)['datasets'],c.tables)

    def test_api_incident_resolved_after_reporting_end_is_not_in_mttr(self):
        client=Mock();c=Collector(client)
        incident={'id':'i-1','attributes':{'created':(c.end-timedelta(hours=2)).isoformat(),
            'detected':(c.end-timedelta(hours=2)).isoformat(),'resolved':(c.end+timedelta(hours=1)).isoformat(),'state':'resolved'}}
        client.offset.return_value=[{'data':[incident]}]
        self.assertIsNone(list(c.incidents())[0]['mttr_minutes'])
        incident['attributes']['resolved']=(c.end-timedelta(hours=1)).isoformat()
        incident['attributes']['state']='active'
        self.assertIsNone(list(c.incidents())[0]['mttr_minutes'])

    def test_timezone_free_acknowledgement_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json'
            p.write_text(json.dumps({'incident_timestamps':[{'incident_id':'a','source':'export',
                'created_at':'2026-11-01T00:00:00+00:00','acknowledged_at':'2026-11-01T00:01:00'}]}))
            with self.assertRaisesRegex(ValueError,'timezone-free'):load_config(p)

    def test_workbook_snapshot_roundtrips_nulls_unicode_and_long_strings(self):
        snapshot={'datasets':{'example':[{'value':None,'flag':True,'note':'α<&"'*20000}]}}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'report.xlsx'
            write_xlsx(p,{'Summary':([{'count':1}],[])},report=snapshot)
            self.assertEqual(read_report(p),snapshot)


if __name__=='__main__':unittest.main()
