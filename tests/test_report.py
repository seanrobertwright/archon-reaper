"""Tests for collect_stats.py and render_report.py. Standard library only.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "archon-report" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect_stats  # noqa: E402
import fixture  # noqa: E402
import render_report  # noqa: E402


def run_collector(db, *extra):
    out = Path(db).with_suffix(".json")
    proc = subprocess.run([sys.executable, "-I", str(SCRIPTS / "collect_stats.py"), "--db", str(db), "--out", str(out), *extra],
                          capture_output=True, text=True)
    return proc, out


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = fixture.build(Path(cls.tmp.name) / "archon.db")
        cls.before = hashlib.sha256(cls.db.read_bytes()).hexdigest()
        proc, out = run_collector(cls.db)
        assert proc.returncode == 0, proc.stderr
        cls.d = json.loads(out.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_run_counts(self):
        s = self.d["summary"]
        self.assertEqual((s["total"], s["completed"], s["failed"], s["cancelled"], s["active"]), (10, 4, 3, 2, 1))
        self.assertAlmostEqual(s["success_rate"], 4 / 9)
        self.assertAlmostEqual(s["success_rate_excl_cancelled"], 4 / 7)

    def test_outcome_is_reported_separately_from_status(self):
        s = self.d["summary"]
        self.assertEqual(s["completed_outcome_verified"], 3)
        self.assertEqual(s["completed_outcome_unrecorded"], 1)

    def test_cancelled_with_error_is_flagged(self):
        self.assertEqual(self.d["summary"]["cancelled_after_error"], 1)
        self.assertEqual(self.d["failures"]["cancel_categories"]["Cancelled by operator"], 1)
        self.assertEqual(self.d["failures"]["cancel_categories"]["Cancelled after an error"], 1)

    def test_failure_categories(self):
        cats = {c["category"]: c["count"] for c in self.d["failures"]["categories"]}
        self.assertEqual(cats, {"Timed out: gate": 1, "Workspace setup failed": 1, "Script failed: record": 1})

    def test_workflow_counts(self):
        wf = {w["name"]: w for w in self.d["workflows"]}
        w = wf[fixture.PACK]   # r1 r2 r8 r9 completed, r3 r7 failed, r10 still running
        self.assertEqual((w["runs"], w["completed"], w["failed"], w["cancelled"], w["active"]), (7, 4, 2, 0, 1))

    def test_durations_and_idle_time(self):
        runs = {r["id"]: r for r in self.d["runs"]}
        self.assertEqual(runs["r1"]["wall_s"], 1800)
        self.assertEqual(runs["r8"]["idle_s"], 600)       # approval wait
        self.assertEqual(runs["r8"]["active_s"], 1200)
        self.assertEqual(runs["r9"]["idle_s"], 1800)      # gap between failure and resume
        self.assertEqual(runs["r9"]["resumes"], 1)

    def test_spend_excludes_loop_groups_and_unavailable_cost(self):
        sp = self.d["spend"]
        self.assertAlmostEqual(sp["total"], 5.0)           # 1.5 + 0.5 + 2.0 + 1.0, not +99
        self.assertAlmostEqual(sp["wasted"], 2.0)          # r3 failed
        self.assertEqual({m["model"] for m in sp["by_model"]}, {"claude-sonnet-5-5", "claude-haiku-5-5"})

    def test_hook_activity_is_skipped_but_counted_in_health(self):
        self.assertEqual(self.d["health"]["hook_activity_events"], 5)

    def test_unfinished_run_listed(self):
        self.assertEqual([u["run"] for u in self.d["health"]["unfinished_runs"]], ["r10"])

    def test_database_is_not_modified(self):
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), self.before)

    def test_project_filter(self):
        proc, out = run_collector(self.db, "--project", "other")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["summary"]["total"], 1)

    def test_since_filter_can_empty_the_window(self):
        proc, out = run_collector(self.db, "--since", "2099-01-01")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["summary"]["total"], 0)


class SchemaAndEdgeTests(unittest.TestCase):
    def test_missing_database(self):
        proc = subprocess.run([sys.executable, "-I", str(SCRIPTS / "collect_stats.py"), "--db", str(ROOT / "nope.db")],
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not found", proc.stderr)

    def test_schema_drift_gives_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "archon.db"
            con = sqlite3.connect(str(db))
            con.execute("create table remote_agent_workflow_runs (id text)")
            con.commit()
            con.close()
            proc, _ = run_collector(db)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("schema is not what this script expects", proc.stderr)

    def test_classify_failure_prefers_final_error_text(self):
        # first failure was a timeout (kind), but the final error is a script exit: text wins
        got = collect_stats.classify_failure("'gate': Script node 'gate' failed [exit 1]: not green", "node_error", "timeout", "gate")
        self.assertEqual(got, "Script failed: gate")

    def test_classify_failure_fallbacks(self):
        self.assertEqual(collect_stats.classify_failure("", None, None), "No error recorded")
        self.assertEqual(collect_stats.classify_failure("weird", None, None), "Other")
        self.assertEqual(collect_stats.classify_failure("x", None, "timeout", "build"), "Timed out: build")


class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = fixture.build(Path(cls.tmp.name) / "archon.db")
        proc, out = run_collector(db)
        assert proc.returncode == 0, proc.stderr
        cls.d = json.loads(out.read_text(encoding="utf-8"))
        cls.n = {"headline": "Headline <b>text</b>.", "observations": ["One."], "recommendations": ["Do it."],
                 "failure_notes": {"Timed out: gate": "Gate is slow."}}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_renders_artifact_fragment(self):
        html = render_report.render(self.d, None)
        self.assertTrue(html.lstrip().startswith("<title>Archon Run Report</title>"))
        for tag in (r"<!doctype", r"<html[ >]", r"<head[ >]", r"<body[ >]"):
            self.assertIsNone(re.search(tag, html, re.I), tag)
        for section in ("Workflows", "What went wrong", "How long runs take", "Spend and tokens", "Installation health"):
            self.assertIn(section, html)

    def test_error_text_is_escaped(self):
        html = render_report.render(self.d, None)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_narrative_is_included_and_escaped(self):
        html = render_report.render(self.d, self.n)
        self.assertIn("Gate is slow.", html)
        self.assertIn("Do it.", html)
        self.assertIn("Headline &lt;b&gt;text&lt;/b&gt;.", html)

    def test_theme_tokens_for_both_modes(self):
        html = render_report.render(self.d, None)
        self.assertIn("prefers-color-scheme:dark", html)
        self.assertIn(':root[data-theme="dark"]', html)

    def test_empty_window(self):
        db = fixture.build(Path(self.tmp.name) / "empty.db")
        proc, out = run_collector(db, "--since", "2099-01-01")
        html = render_report.render(json.loads(out.read_text(encoding="utf-8")), None)
        self.assertIn("No runs matched", html)

    def test_cli_standalone_flag(self):
        stats = Path(self.tmp.name) / "stats.json"
        stats.write_text(json.dumps(self.d), encoding="utf-8")
        out = Path(self.tmp.name) / "r.html"
        subprocess.run([sys.executable, "-I", str(SCRIPTS / "render_report.py"), str(stats), str(out), "--standalone"], check=True,
                       capture_output=True)
        text = out.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!doctype html>"))
        self.assertEqual(text.count("<body>"), 1)

    def test_formatters(self):
        self.assertEqual(render_report.dur(45), "45s")
        self.assertEqual(render_report.dur(1800), "30m")
        self.assertEqual(render_report.dur(7500), "2h 05m")
        self.assertEqual(render_report.dur(None), "–")
        self.assertEqual(render_report.money(4.8), "$4.80")
        self.assertEqual(render_report.pc(0.5357), "54%")


if __name__ == "__main__":
    unittest.main()
