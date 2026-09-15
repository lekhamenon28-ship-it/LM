import argparse
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import calculate_assessment, load_fixture, write_outputs


class CollectorTests(unittest.TestCase):
    def args(self):
        return argparse.Namespace(management_zone=None, tag=None, from_time="now-30d", to_time="now")

    def test_fixture_produces_scored_tenant_inventory(self):
        root = Path(__file__).resolve().parents[1]
        assessment = calculate_assessment(load_fixture(root / "sample-fixture.json"), self.args())
        self.assertEqual(assessment["inventory"]["totalEntities"], 6)
        self.assertEqual(assessment["metadata"]["scope"], "tenant-wide")
        self.assertIsNotNone(assessment["overallScore"])
        self.assertEqual(assessment["dimensions"]["rca"]["evidence"]["rootCauseCoveragePct"], 50.0)

    def test_all_outputs_are_written(self):
        root = Path(__file__).resolve().parents[1]
        assessment = calculate_assessment(load_fixture(root / "sample-fixture.json"), self.args())
        with tempfile.TemporaryDirectory() as temp:
            write_outputs(assessment, Path(temp))
            for name in ("assessment.json", "inventory.csv", "recommendations.csv", "report.html"):
                self.assertTrue((Path(temp) / name).exists())
            self.assertIn("overallScore", json.loads((Path(temp) / "assessment.json").read_text()))


if __name__ == "__main__":
    unittest.main()
