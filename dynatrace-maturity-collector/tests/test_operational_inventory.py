import csv
import json
import tempfile
import time
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multi_tool_inventory import Observation, write_outputs
from operational_inventory import alert_row, datadog_alerts, dynatrace_traces, mttr_rows, timestamp


class OperationalTests(unittest.TestCase):
    def test_seconds_and_milliseconds_produce_same_duration(self):
        seconds = alert_row("zabbix", "1", "CPU", 1_700_000_000, 1_700_001_800, "trigger_recovery")
        millis = alert_row("dynatrace", "2", "CPU", 1_700_000_000_000, 1_700_001_800_000, "problem_resolution")
        self.assertEqual(seconds.duration_minutes, 30.0)
        self.assertEqual(millis.duration_minutes, 30.0)
        self.assertEqual(timestamp(1_700_000_000), timestamp(1_700_000_000_000))

    def test_mttr_excludes_open_alerts_and_separates_measurements(self):
        alerts = [
            {"tool": "dynatrace", "measurement": "problem_resolution", "duration_minutes": 10},
            {"tool": "dynatrace", "measurement": "problem_resolution", "duration_minutes": 30},
            {"tool": "dynatrace", "measurement": "problem_resolution", "duration_minutes": None},
            {"tool": "datadog", "measurement": "latest_monitor_recovery", "duration_minutes": 5},
        ]
        rows = mttr_rows(alerts)
        self.assertEqual(rows[1]["sample_count"], 2)
        self.assertEqual(rows[1]["mean_minutes"], 20.0)
        self.assertEqual(rows[1]["median_minutes"], 20.0)

    def test_fixture_writes_operational_tables(self):
        root = Path(__file__).resolve().parents[1]
        fixture = json.loads((root / "multi-tool-fixture.json").read_text())
        observations = [Observation(**item) for item in fixture["observations"]]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            write_outputs(observations, fixture["diagnostics"], output, fixture["catalog"],
                          fixture["catalog_diagnostics"], fixture["operational"], fixture["operational_diagnostics"])
            for kind in ("service_relationships", "alerts", "trace_availability", "mttr"):
                with (output / (kind + ".csv")).open(newline="") as handle:
                    self.assertTrue(list(csv.DictReader(handle)))
            with (output / "mttr.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(next(row for row in rows if row["tool"] == "dynatrace")["sample_count"], "1")

    def test_datadog_group_host_and_latest_recovery(self):
        start = int(time.time()) - 1200
        monitor = {"id": 7, "name": "High CPU", "priority": 2, "state": {"groups": {
            "host:web.example.net,service:api": {"status": "OK", "last_triggered_ts": start, "last_resolved_ts": start + 1200}
        }}}
        with patch("operational_inventory.dd_client", return_value=("https://api.example", {})), \
             patch("operational_inventory.get_json", return_value=[monitor]) as fetch:
            rows = datadog_alerts([])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].host, "web.example.net")
        self.assertEqual(rows[0].service, "api")
        self.assertEqual(rows[0].duration_minutes, 20.0)
        self.assertIn("page_size=1000", fetch.call_args.args[0])

    def test_dynatrace_span_result_maps_service_id(self):
        applications = [{"tool": "dynatrace", "source_id": "SERVICE-1", "name": "api", "host": "web"},
                        {"tool": "dynatrace", "source_id": "SERVICE-2", "name": "worker", "host": "web"}]
        result = {"state": "SUCCEEDED", "result": {"records": [{"dt.entity.service": "SERVICE-1", "span_count": 4}]}}
        with patch.dict("os.environ", {"DT_PLATFORM_URL": "https://dt.apps.dynatrace.com", "DT_PLATFORM_TOKEN": "test"}), \
             patch("operational_inventory.post_json", return_value={"requestToken": "query-id"}), \
             patch("operational_inventory.get_json", return_value=result):
            rows = dynatrace_traces(applications)
        self.assertEqual([row.status for row in rows], ["yes", "unknown"])
        self.assertEqual(rows[0].evidence, "Grail.spans.active_24h")


if __name__ == "__main__":
    unittest.main()
