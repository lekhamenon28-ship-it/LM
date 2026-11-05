import csv
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from licensing_usage import collect_usage, host_usage_fields
from multi_tool_inventory import Observation, dynatrace, write_outputs


class UsageTests(unittest.TestCase):
    def client(self):
        client = Mock()
        def get(path, params):
            self.assertIsInstance(params['from'], int)
            self.assertIsInstance(params['to'], int)
            selector = params['metricSelector']
            self.assertIn(':sum', selector)
            value = [2, 2, None, 2] if 'full_stack' in selector else [0.25, 0.25] if 'infrastructure' in selector else []
            return {'resolution': '1h', 'result': [{'data': [{'dimensionMap': {'dt.entity.host': 'HOST-1'}, 'values': value}]}]}
        client.get.side_effect = get
        return client

    def test_distinct_units_estimates_and_priced_cost(self):
        observations = [Observation('dynatrace', 'HOST-1', 'web', memory_gib=8)]
        rows, diagnostics = collect_usage(self.client(), observations, 7, {'currency': 'USD', 'rates': {'full_stack': 0.1, 'infrastructure': 0.2}})
        full, infra, empty = rows
        self.assertEqual(full['full_stack_gib_hours'], 6)
        self.assertIsNone(full['host_hours'])
        self.assertEqual(full['full_stack_host_hours_estimate'], 0.75)
        self.assertEqual(infra['host_hours'], 0.5)
        self.assertIsNone(empty['usage_value'])
        summary = host_usage_fields('HOST-1', rows, diagnostics)
        self.assertEqual(summary['estimated_cost'], 0.7)
        self.assertEqual(summary['host_hours'], 0.5)
        self.assertEqual(summary['memory_gib_hours_consumption'], 6)

    def test_unpriced_cost_unknown_and_missing_usage_not_zero(self):
        rows, diagnostics = collect_usage(self.client(), [])
        self.assertEqual(host_usage_fields('HOST-1', rows, diagnostics)['estimated_cost'], 'unknown')
        self.assertEqual(host_usage_fields('HOST-missing', rows, diagnostics)['host_hours'], 'unknown')
        self.assertEqual(host_usage_fields('HOST-missing', rows, diagnostics)['memory_gib_hours_consumption'], 'unknown')
        self.assertIsNone(rows[0]['full_stack_host_hours_estimate'])

    def test_billing_failure_does_not_abort_other_modes(self):
        client = self.client()
        original = client.get.side_effect
        def get(path, params):
            if 'full_stack' in params['metricSelector']:
                raise RuntimeError('HTTP 403')
            return original(path, params)
        client.get.side_effect = get
        rows, diagnostics = collect_usage(client, [])
        self.assertEqual(diagnostics['full_stack'], 'HTTP 403')
        self.assertEqual(host_usage_fields('HOST-1', rows, diagnostics)['usage_status'], 'partial')
        self.assertEqual(len(rows), 2)

    def test_export_keeps_entity_ids_and_usage_after_cross_tool_merge(self):
        rows, diagnostics = collect_usage(self.client(), [])
        obs = [Observation('dynatrace', 'HOST-1', 'web', host_status='running', monitoring_mode='FULL_STACK'), Observation('zabbix', '12', 'web')]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(obs, {}, Path(tmp), usage_rows=rows, usage_diagnostics=diagnostics)
            with (Path(tmp) / 'host_inventory.csv').open() as f:
                row = next(csv.DictReader(f))
            self.assertEqual(row['entity_id'], 'HOST-1')
            self.assertIn('12', row['entity_ids'])
            self.assertEqual(row['host_status'], 'running')
            self.assertEqual(row['monitoring_mode'], 'FULL_STACK')
            self.assertEqual(row['full_stack_gib_hours'], '6')
            self.assertEqual(row['memory_gib_hours_consumption'], '6')
            self.assertFalse((Path(tmp) / 'host_usage.csv').exists())
            self.assertEqual(row['record_type'], 'inventory_and_usage')
            self.assertIn('GiB-hour', row['usage_details'])

    def test_combined_csv_preserves_historical_entities_and_all_costs(self):
        rows, diagnostics = collect_usage(self.client(), [], pricing={'currency': 'USD', 'rates': {'full_stack': 0.1, 'infrastructure': 0.2}})
        historical = dict(rows[0], entity_id='HOST-old', usage_value=3, full_stack_gib_hours=3, estimated_cost=0.3)
        rows.append(historical)
        observations = [Observation('dynatrace', 'HOST-1', 'web'), Observation('zabbix', '2', 'other')]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / 'host_usage.csv').write_text('obsolete')
            write_outputs(observations, {}, output, usage_rows=rows, usage_diagnostics=diagnostics)
            with (output / 'host_inventory.csv').open() as f:
                combined = list(csv.DictReader(f))
            self.assertEqual(len(combined), 3)
            old = next(r for r in combined if r['entity_id'] == 'HOST-old')
            self.assertEqual(old['record_type'], 'usage_only')
            self.assertEqual(old['host_status'], 'unknown')
            self.assertEqual(old['dynatrace'], 'unknown')
            self.assertEqual(old['estimated_cost'], '0.3')
            self.assertAlmostEqual(sum(float(r['estimated_cost']) for r in combined if r['estimated_cost'] != 'unknown'), 1.0)
            self.assertFalse((output / 'host_usage.csv').exists())

    def test_host_metadata_extracted_without_inferring_offline_from_missing_data(self):
        client = Mock()
        def paged(path, params, key):
            return ([{'entityId': 'HOST-1', 'displayName': 'web', 'lastSeenTms': 1700000000000, 'properties': {'state': 'RUNNING', 'monitoringMode': 'FULL_STACK', 'physicalMemory': 8 * 1024**3}}, {'entityId': 'HOST-2', 'displayName': 'unknown', 'properties': {}}], 1) if params['entitySelector'] == 'type("HOST")' else ([], 1)
        client.paged.side_effect = paged
        client.get.return_value = {'result': []}
        with patch('multi_tool_inventory.ApiClient', return_value=client), patch('multi_tool_inventory.required', return_value='configured'):
            hosts = dynatrace()
        self.assertEqual(hosts[0].host_status, 'running')
        self.assertEqual(hosts[0].memory_gib, 8)
        self.assertEqual(hosts[0].monitoring_mode, 'FULL_STACK')
        self.assertEqual(hosts[1].host_status, 'unknown')

    def test_invalid_prices_rejected(self):
        for pricing in [{'rates': {'full_stack': -1}, 'currency': 'USD'}, {'rates': {'full_stack': 1}}, {'rates': {'full_stack': True}, 'currency': 'USD'}]:
            with self.assertRaises(ValueError):
                collect_usage(self.client(), [], pricing=pricing)
