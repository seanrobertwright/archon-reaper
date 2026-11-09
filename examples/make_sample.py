#!/usr/bin/env python3
"""Regenerate examples/sample-report.html from the synthetic test fixture.

Run from the repo root:  python examples/make_sample.py
No real Archon data is used.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import fixture  # noqa: E402

db = Path("examples/.sample.db")  # relative on purpose: the report footer prints the path it was given
fixture.build(ROOT / db)
try:
    subprocess.run([sys.executable, "-I", "skills/archon-report/scripts/generate.py",
                    "--db", str(db), "--narrative", "examples/narrative.example.json",
                    "--out", "examples/sample-report.html"], check=True, cwd=ROOT)
finally:
    (ROOT / db).unlink(missing_ok=True)
