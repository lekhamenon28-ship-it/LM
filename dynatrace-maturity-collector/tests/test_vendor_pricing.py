import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vendor_pricing import page_text, parse_vendor, collect_vendor_pricing, dynatrace_pricing, write_pricing_outputs, SOURCES

DT = '''<h1>List Price (USD)</h1><p>Full-Stack Monitoring $0.013 per memory-GiB-hour</p>
<p>Infrastructure Monitoring $0.045 per hour for any size host</p>
<p>Foundation &amp; Discovery $0.012 per hour for any size host</p>'''


class VendorPricingTests(unittest.TestCase):
    def test_extracts_vendor_values_and_exact_units_not_monthly_marketing_price(self):
        rows = parse_vendor('dynatrace', page_text(DT))
        self.assertEqual([r['unit_rate'] for r in rows], [0.013, 0.045, 0.012])
        self.assertEqual(rows[0]['billing_unit'], 'GiB-hour')
        self.assertEqual(parse_vendor('dynatrace', 'Full-Stack Monitoring $58 / month'), [])
        self.assertEqual(parse_vendor('dynatrace', page_text(DT.replace('(USD)', '(EUR)'))), [])

    def test_script_text_and_conflicting_prices_are_not_trusted(self):
        text = page_text(DT + '<script>Full-Stack Monitoring $999 per memory-GiB-hour</script>')
        self.assertEqual(parse_vendor('dynatrace', text)[0]['unit_rate'], 0.013)
        conflicting = page_text(DT + '<p>Full-Stack Monitoring $8 per memory-GiB-hour</p>')
        self.assertNotIn('full_stack', [r['plan'] for r in parse_vendor('dynatrace', conflicting)])

    def test_datadog_contract_terms_remain_distinct(self):
        rows = parse_vendor('datadog', 'Infrastructure Pro Per infra host, per month Per infra host, per month $ 15 $ 18 $ 18')
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['billing_unit'], 'host-month')
        self.assertIn('annual_commitment', rows[0]['price_basis'])
        self.assertIn('on_demand', rows[2]['price_basis'])

    def test_solarwinds_editions_do_not_cross_match_rates(self):
        text = ('Essentials Core hybrid infrastructure monitoring for teams. Starts at: $ 8 per node per month '
                'Advanced Essentials plus deeper monitoring. Starts at: $ 14 per node per month '
                'Everything in Essentials, plus features. Premier The most comprehensive option with Advanced capabilities. Starts at: $ 17.50 per node per month')
        rows = parse_vendor('solarwinds', text)
        self.assertEqual([(r['plan'], r['unit_rate']) for r in rows], [('essentials', 8), ('advanced', 14), ('premier', 17.5)])

    def test_splunk_quote_and_zabbix_license_are_not_false_host_prices(self):
        rows = parse_vendor('splunk', 'Splunk Enterprise ingest and workload. Get a quote')
        self.assertIsNone(rows[0]['unit_rate'])
        self.assertEqual(rows[0]['price_basis'], 'quote_required')
        rows = parse_vendor('zabbix', 'Zabbix is Open Source Software under AGPLv3')
        self.assertEqual(rows[0]['billing_unit'], 'software-license')
        self.assertIn('Support subscriptions', rows[0]['notes'])

    def test_lookup_failure_isolated_and_offline_mode_never_fetches(self):
        def fetch(url):
            if url == SOURCES['dynatrace'][1]:
                return DT
            raise RuntimeError('secret-response-body')
        catalog = collect_vendor_pricing(fetch=fetch)
        self.assertEqual(len(catalog['prices']), 3)
        self.assertEqual(len(catalog['references']), 5)
        self.assertNotIn('secret-response-body', str(catalog))
        offline = collect_vendor_pricing(fetch=lambda _: self.fail('Network in fixture mode'), enabled=False)
        self.assertEqual(offline['prices'], [])
        self.assertEqual(offline['references'][0]['status'], 'not_fetched')

    def test_contract_overrides_public_rates_and_currencies_never_mix(self):
        catalog = collect_vendor_pricing(fetch=lambda _: DT)
        resolved = dynatrace_pricing({'dynatrace': {'currency': 'USD', 'rates': {'full_stack': 0.005}}}, catalog)
        self.assertEqual(resolved['rates']['full_stack'], 0.005)
        self.assertEqual(resolved['rates']['infrastructure'], 0.045)
        self.assertEqual(resolved['rate_metadata']['full_stack']['price_source'], 'configured_contract_rate')
        resolved = dynatrace_pricing({'dynatrace': {'currency': 'INR', 'rates': {'full_stack': 1}}}, catalog)
        self.assertEqual(resolved['rates'], {'full_stack': 1})
        legacy = dynatrace_pricing({'currency': 'USD', 'rates': {'full_stack': 0.002}}, catalog)
        self.assertEqual(legacy['rates']['full_stack'], 0.002)
        failed = dynatrace_pricing({}, {'prices': []})
        self.assertEqual(failed['rates'], {})

    def test_public_rate_reaches_usage_cost_and_source_columns(self):
        from unittest.mock import Mock
        from licensing_usage import collect_usage
        client = Mock()
        client.get.return_value = {"resolution": "15m", "result": [{"data": [{"dimensionMap": {"dt.entity.host": "HOST-1"}, "values": [2, 2]}]}]}
        catalog = collect_vendor_pricing(fetch=lambda _: DT)
        pricing = dynatrace_pricing({}, catalog)
        rows, _ = collect_usage(client, [], pricing=pricing)
        self.assertEqual(rows[0]["estimated_cost"], 0.052)
        self.assertEqual(rows[0]["price_source"], "vendor_public_list")
        self.assertEqual(rows[0]["price_reference"], SOURCES["dynatrace"][1])

    def test_reference_outputs_include_vendor_urls_and_retrieval_times(self):
        catalog = collect_vendor_pricing(fetch=lambda _: DT)
        with tempfile.TemporaryDirectory() as temp:
            write_pricing_outputs(catalog, Path(temp))
            refs = (Path(temp) / 'references.md').read_text()
            for _, url in SOURCES.values():
                self.assertIn(url, refs)
            self.assertTrue(catalog['references'][0]['retrieved_at'])
            self.assertTrue((Path(temp) / 'vendor_pricing.csv').exists())
