#!/usr/bin/env python3
"""Regenerate examples/sample-report.html from the synthetic test fixture.

Run from the repo root:  python examples/make_sample.py
No real Archon data is used.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import fixture  # noqa: E402

# A throwaway Archon home: the fixture writes version evidence beside the database.
# Relative on purpose: the report footer prints the path it was given.
home = Path("examples/.sample")
db = home / "archon.db"
(ROOT / home).mkdir(exist_ok=True)
try:
    fixture.build(ROOT / db)
    subprocess.run([sys.executable, "-I", "skills/archon-reaper/scripts/generate.py",
                    "--db", db.as_posix(), "--narrative", "examples/narrative.example.json",
                    "--out", "examples/sample-report.html"], check=True, cwd=ROOT)
finally:
    shutil.rmtree(ROOT / home, ignore_errors=True)
