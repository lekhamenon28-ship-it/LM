import csv
import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alert_reporting import prepare_alerts, host_alert_fields
from multi_tool_inventory import Observation, write_outputs
from operational_inventory import dynatrace_alerts


class AlertReportingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)

    def alert(self, identity='P-1', days=1, hosts=None, alert_type='PERFORMANCE'):
        return {'tool': 'dynatrace', 'source_id': identity, 'name': 'Slow response', 'alert_type': alert_type,
                'opened_at': (self.now-timedelta(days=days)).isoformat(), 'measurement': 'problem_resolution',
                'affected_host_entity_ids': hosts or ['HOST-1']}

    def test_distinct_occurrences_window_latest_and_multiple_hosts(self):
        recent = self.alert('P-2', days=2, hosts=['HOST-1', 'HOST-2'])
        latest = self.alert('P-1', days=1)
        future = self.alert('P-future', days=-1)
        old = self.alert('P-old', days=31)
        rows, events, start, end = prepare_alerts([old, recent, latest, latest, future], [], now=self.now)
        fields = host_alert_fields({'entity_ids': {'dynatrace': 'HOST-1'}}, events, {'dynatrace': 'available'}, start, end)
        self.assertEqual(fields['recent_occurrences_count'], 2)
        self.assertEqual(fields['latest_occurrence_timestamp'], latest['opened_at'])
        self.assertEqual(fields['alert_details'][0]['recent_occurrences_count'], 2)
        other = host_alert_fields({'entity_ids': {'dynatrace': 'HOST-2'}}, events, {'dynatrace': 'available'}, start, end)
        self.assertEqual(other['recent_occurrences_count'], 1)
        self.assertEqual(rows[1]['recent_occurrences_count'], 1)

    def test_failed_query_unknown_but_successful_host_query_zero(self):
        _, events, start, end = prepare_alerts([], [], now=self.now)
        host = {'entity_ids': {'dynatrace': 'HOST-1'}}
        self.assertEqual(host_alert_fields(host, events, {'dynatrace': 'HTTP 403'}, start, end)['recent_occurrences_count'], 'unknown')
        self.assertEqual(host_alert_fields(host, events, {'dynatrace': 'available'}, start, end)['recent_occurrences_count'], 0)

    def test_monitor_and_rule_snapshots_do_not_become_history_counts(self):
        row = dict(self.alert(), tool='datadog', measurement='latest_monitor_recovery')
        rows, events, _, _ = prepare_alerts([row], [], now=self.now)
        self.assertIsNone(rows[0]['recent_occurrences_count'])
        self.assertEqual(len(events), 0)

    def test_dynatrace_host_ids_deduplicated_and_service_not_assigned_to_host(self):
        client = Mock()
        host = {'entityId': {'id': 'HOST-1', 'type': 'HOST'}, 'name': 'web'}
        service = {'entityId': {'id': 'SERVICE-1', 'type': 'SERVICE'}, 'name': 'web'}
        client.paged.return_value = ([{'problemId': 'P-1', 'title': 'CPU high', 'severityLevel': 'RESOURCE_CONTENTION',
                                     'startTime': 1793876400000, 'endTime': -1, 'affectedEntities': [host, service], 'impactedEntities': [host]},
                                    {'problemId': 'P-service', 'title': 'Slow response', 'affectedEntities': [service]}], 1)
        with patch('operational_inventory.dt_client', return_value=client):
            rows = dynatrace_alerts([])
        self.assertEqual(rows[0].affected_host_entity_ids, ['HOST-1'])
        self.assertEqual(rows[0].alert_type, 'RESOURCE_CONTENTION')
        self.assertEqual(rows[1].affected_host_entity_ids, [])
        self.assertIsNone(rows[0].duration_minutes)

    def test_name_mapping_requires_same_tool_and_unambiguous_identity(self):
        obs = [Observation('datadog', '1', 'web'), Observation('datadog', '2', 'web')]
        row = dict(self.alert(), tool='datadog', host='web', affected_host_entity_ids=[])
        rows, _, _, _ = prepare_alerts([row], obs, now=self.now)
        self.assertEqual(rows[0]['affected_host_entity_ids'], [])

    def test_exports_combined_and_detail_columns_with_utc_times(self):
        now = datetime.now(timezone.utc)
        row = self.alert()
        row['opened_at'] = (now - timedelta(hours=1)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            write_outputs([Observation('dynatrace', 'HOST-1', 'web')], {}, path,
                          operational={'alerts': [row]}, operational_diagnostics={'alerts': {'dynatrace': 'available'}})
            with (path / 'host_inventory.csv').open() as f:
                report = next(csv.DictReader(f))
            self.assertEqual(report['alert_type'], 'PERFORMANCE')
            self.assertEqual(report['recent_occurrences_count'], '1')
            self.assertEqual(report['affected_host_entity_ids'], 'HOST-1')
            self.assertEqual(report['latest_occurrence_timestamp'], row['opened_at'])
            self.assertEqual(json.loads(report['alert_details'])[0]['recent_occurrences_count'], 1)
            with (path / 'alerts.csv').open() as f:
                detail = next(csv.DictReader(f))
            self.assertEqual(json.loads(detail['affected_host_entity_ids']), ['HOST-1'])
