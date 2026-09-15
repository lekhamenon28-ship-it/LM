import csv
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multi_tool_inventory import Observation, merge, write_outputs


class InventoryTests(unittest.TestCase):
    def test_exact_ip_alias_merges_tools(self):
        rows = merge([
            Observation("dynatrace", "HOST-1", "web.example.net", aliases=["10.0.0.1"], signals=["cpu"]),
            Observation("zabbix", "1001", "web", aliases=["10.0.0.1"], signals=["disk"]),
            Observation("zabbix", "1002", "other", signals=["cpu"]),
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["tools"], ["dynatrace", "zabbix"])
        self.assertEqual(rows[1]["signals"], ["cpu", "disk"])

    def test_unavailable_connector_is_unknown_in_table(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            write_outputs([Observation("zabbix", "1", "web", signals=["cpu"])], {"zabbix": "available", "datadog": "HTTP 403"}, output)
            with (output / "host_inventory.csv").open(newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["zabbix"], "yes")
            self.assertEqual(row["datadog"], "unknown")
            self.assertEqual(row["memory"], "unknown")
            self.assertEqual(row["cpu"], "yes")
            self.assertEqual(len(json.loads((output / "host_inventory.json").read_text())["hosts"]), 1)

    def test_catalog_tables_include_tool_and_status(self):
        root = Path(__file__).resolve().parents[1]
        fixture = json.loads((root / "multi-tool-fixture.json").read_text())
        observations = [Observation(**item) for item in fixture["observations"]]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            write_outputs(observations, fixture["diagnostics"], output, fixture["catalog"], fixture["catalog_diagnostics"])
            for kind in ("applications", "dashboards", "metrics"):
                with (output / (kind + ".csv")).open(newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertTrue(rows)
                self.assertIn("tool", rows[0])
                self.assertIn("status", rows[0])
            self.assertEqual(len(json.loads((output / "host_inventory.json").read_text())["catalog"]["metrics"]), 5)


if __name__ == "__main__":
    unittest.main()
