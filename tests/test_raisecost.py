"""Behavioural tests for raisecost: exact counts, line attribution, CLI."""

import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest

from raisecost import Profiler, calibrate

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _miss(d, key):
    try:
        return d[key]
    except KeyError:
        return None


def _hit(d, key):
    return d[key]


class CountTests(unittest.TestCase):
    def test_exact_counts_and_line_attribution(self):
        base = inspect.getsourcelines(_miss)[1]
        with Profiler(measure_overhead=False) as prof:
            for i in range(37):
                _miss({}, i)
        rep = prof.report()
        self.assertEqual(rep["total_raises"], 37)
        self.assertEqual(rep["total_handled"], 37)
        self.assertEqual(rep["raise_types"], {"KeyError": 37})
        raise_site = rep["top_raise_sites"][0]
        self.assertEqual(raise_site["function"], "_miss")
        self.assertEqual(raise_site["line"], base + 2)   # return d[key]
        self.assertEqual(raise_site["raises"], 37)
        handler = [h for h in rep["top_handler_sites"] if h["function"] == "_miss"][0]
        self.assertEqual(handler["line"], base + 3)      # except KeyError
        self.assertEqual(handler["handled"], 37)

    def test_hit_path_records_nothing(self):
        d = {"k": 1}
        with Profiler(measure_overhead=False) as prof:
            for _ in range(100):
                _hit(d, "k")
        self.assertEqual(prof.total_raises, 0)
        self.assertEqual(prof.report()["top_raise_sites"], [])

    def test_no_measurement_when_disabled(self):
        prof = Profiler(measure_overhead=False)
        rep = prof.report()
        self.assertEqual(rep["total_raises"], 0)
        self.assertIsNone(rep["estimated_tax_seconds"])
        self.assertIsNone(rep["calibration_ns_per_raise"])

    def test_report_is_json_serialisable_and_bounded(self):
        with Profiler(measure_overhead=True) as prof:
            for i in range(10):
                _miss({}, i)
        rep = prof.report()
        blob = json.dumps(rep)
        self.assertIn("total_raises", blob)
        self.assertEqual(rep["total_raises"], 10)
        self.assertIsNotNone(rep["estimated_tax_seconds"])
        self.assertGreater(rep["calibration_ns_per_raise"], 0.0)
        self.assertLess(rep["calibration_ns_per_raise"], 20000.0)
        self.assertGreater(rep["estimated_tax_seconds"], 0.0)
        self.assertEqual(rep["tax_share_of_baseline"], None)

    def test_tool_id_is_released(self):
        prof = Profiler(measure_overhead=False)
        prof.start()
        tid = prof._tool_id
        self.assertIsNotNone(sys.monitoring.get_tool(tid))
        prof.stop()
        self.assertIsNone(sys.monitoring.get_tool(tid))

    def test_calibration_is_a_positive_differential(self):
        ns = calibrate(repeats=2000)
        self.assertGreaterEqual(ns, 0.0)
        self.assertLess(ns, 20000.0)


class CliTests(unittest.TestCase):
    SCRIPT = (
        "def boom(d, k):\n"
        "    try:\n"
        "        return d[k]\n"
        "    except KeyError:\n"
        "        return None\n"
        "\n"
        "for i in range(7):\n"
        "    boom({}, i)\n"
        "print('done')\n"
    )

    def _spawn(self, script, *extra):
        return subprocess.run(
            [sys.executable, "-m", "raisecost", "run", *extra, script],
            cwd=REPO, capture_output=True, text=True, timeout=120,
        )

    def test_cli_writes_json_and_prints_table(self):
        with tempfile.TemporaryDirectory() as td:
            script = os.path.join(td, "workload.py")
            out = os.path.join(td, "report.json")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(self.SCRIPT)
            proc = self._spawn(script, "--json", out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("raises: 7", proc.stdout)
            self.assertIn("top raise sites", proc.stdout)
            with open(out, encoding="utf-8") as fh:
                rep = json.load(fh)
            self.assertEqual(rep["total_raises"], 7)
            self.assertEqual(rep["top_raise_sites"][0]["line"], 3)
            self.assertTrue(rep["top_raise_sites"][0]["file"].endswith("workload.py"))
            self.assertIn("done", proc.stdout)

    def test_cli_baseline_run_reports_share(self):
        with tempfile.TemporaryDirectory() as td:
            script = os.path.join(td, "workload.py")
            out = os.path.join(td, "report.json")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(self.SCRIPT)
            proc = self._spawn(script, "--baseline", "--json", out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, encoding="utf-8") as fh:
                rep = json.load(fh)
            self.assertIsNotNone(rep["baseline_seconds"])
            self.assertGreater(rep["baseline_seconds"], 0.0)
            self.assertIsNotNone(rep["tax_share_of_baseline"])
            self.assertIn("baseline runtime:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
