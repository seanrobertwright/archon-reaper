#!/usr/bin/env python3
"""One-step report without Claude: collect statistics, then render a standalone HTML file.

    python generate.py [--out report.html] [--narrative narrative.json] [--open]
                       [--db PATH] [--since 30d] [--project SUBSTR]

The filters are passed to collect_stats.py. The page is rendered with --standalone so it opens
straight from disk. Without --narrative there is no headline, takeaways or failure notes; that
commentary is what the Claude skill adds.
"""
import argparse
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="archon-report.html")
    ap.add_argument("--narrative")
    ap.add_argument("--open", action="store_true", help="open the finished report in the default browser")
    ap.add_argument("--db")
    ap.add_argument("--since")
    ap.add_argument("--project")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        stats = Path(tmp) / "stats.json"
        cmd = [sys.executable, "-I", str(HERE / "collect_stats.py"), "--out", str(stats)]
        for flag in ("db", "since", "project"):
            if getattr(a, flag):
                cmd += ["--" + flag, getattr(a, flag)]
        subprocess.run(cmd, check=True)
        cmd = [sys.executable, "-I", str(HERE / "render_report.py"), str(stats), a.out, "--standalone"]
        if a.narrative:
            cmd += ["--narrative", a.narrative]
        subprocess.run(cmd, check=True)

    if a.open:
        webbrowser.open(Path(a.out).resolve().as_uri())


if __name__ == "__main__":
    main()
