#!/usr/bin/env python3
"""Collect run statistics from an Archon installation (read-only).

Usage:
    python -I collect_stats.py [--db PATH] [--since 30d|YYYY-MM-DD] [--project SUBSTR] [--out FILE]

Reads ~/.archon/archon.db through a read-only SQLite connection and prints (or writes)
one JSON document that render_report.py turns into the HTML report. Nothing is written
to the Archon install.
"""
import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REQUIRED = {
    "remote_agent_workflow_runs": ["id", "workflow_name", "status", "outcome", "started_at",
                                   "completed_at", "last_activity_at", "codebase_id", "user_message"],
    "remote_agent_workflow_events": ["workflow_run_id", "event_order", "event_type", "step_name",
                                     "data", "created_at"],
    "remote_agent_codebases": ["id", "name"],
}
FINISHED = ("completed", "failed", "cancelled")


# ---------- helpers ----------
def parse_ts(s):
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def jload(s):
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def pct(values, p):
    if not values:
        return None
    v = sorted(values)
    k = max(0, min(len(v) - 1, int(round(p / 100 * len(v) + 0.5)) - 1))
    return v[k]


def dstats(values):
    values = [x for x in values if x is not None]
    if not values:
        return {"n": 0, "mean": None, "median": None, "p90": None, "max": None}
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "p90": pct(values, 90), "max": max(values)}


def trunc(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def classify_failure(error, exit_reason, kind, node=None):
    """Group a failure by cause. The final error text wins; the first failed node's
    failure_kind is only a fallback (a run can fail one way, resume, and fail another)."""
    text = error or ""
    low = text.lower()
    m = re.search(r"(?:script |bash )?node '([^']+)' (?:failed|timed out)", text, re.I)
    where = m.group(1) if m else (node or "unknown node")
    if exit_reason == "launch_failed" or "could not create isolated workspace" in low:
        return "Workspace setup failed"
    if "no schema-valid structured output" in low:
        return "Structured output invalid"
    if "timed out after" in low or "idle timeout" in low:
        return "Timed out: %s" % where
    if "failed [exit" in low:
        return "Script failed: %s" % where
    if kind == "output_contract":
        return "Structured output invalid"
    if kind == "timeout":
        return "Timed out: %s" % where
    if kind == "exec_failed":
        return "Script failed: %s" % where
    if kind == "child_failed" or "child workflow" in low:
        return "Child workflow failed"
    if "loop-group" in low:
        return "Loop failed"
    if "reject" in low:
        return "Approval rejected"
    if not text:
        return "No error recorded"
    return "Other"


def cancel_category(reason, error):
    if error:
        return "Cancelled after an error"
    return {"operator": "Cancelled by operator", "approval_rejected": "Approval rejected",
            "halt_node": "Halted by a workflow node"}.get(reason or "", "Cancelled (%s)" % (reason or "no reason recorded"))


def short_workflow(name):
    return name.split(":")[-1] if name else name


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".archon" / "archon.db"))
    ap.add_argument("--since", help="window start: 30d, 12h, or YYYY-MM-DD")
    ap.add_argument("--project", help="case-insensitive substring of the project (codebase) name")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit("Archon database not found at %s (pass --db)." % db_path)
    con = sqlite3.connect("file:%s?mode=ro" % db_path.as_posix(), uri=True)
    con.row_factory = sqlite3.Row

    # schema check
    problems = []
    for table, cols in REQUIRED.items():
        have = [r[1] for r in con.execute("pragma table_info(%s)" % table)]
        if not have:
            problems.append("missing table %s" % table)
        else:
            problems += ["%s lacks column %s" % (table, c) for c in cols if c not in have]
    if problems:
        sys.exit("Archon schema is not what this script expects:\n  - " + "\n  - ".join(problems) +
                 "\nThe Archon version may have changed the database layout; update collect_stats.py.")

    now = datetime.now(timezone.utc)
    since_dt = None
    if args.since:
        m = re.fullmatch(r"(\d+)([dh])", args.since)
        if m:
            since_dt = now - (timedelta(days=int(m.group(1))) if m.group(2) == "d" else timedelta(hours=int(m.group(1))))
        else:
            since_dt = parse_ts(args.since)
            if since_dt is None:
                sys.exit("Could not parse --since %r" % args.since)

    # ---- runs ----
    rows = con.execute(
        "select r.*, c.name as project from remote_agent_workflow_runs r "
        "left join remote_agent_codebases c on c.id = r.codebase_id order by r.started_at").fetchall()
    runs = {}
    for r in rows:
        st = parse_ts(r["started_at"])
        if since_dt and (st is None or st < since_dt):
            continue
        if args.project and args.project.lower() not in (r["project"] or "").lower():
            continue
        en = parse_ts(r["completed_at"])
        wall = (en - st).total_seconds() if st and en and en >= st else None
        runs[r["id"]] = {
            "id": r["id"], "short": r["id"][:8], "workflow": r["workflow_name"] or "(unknown)",
            "project": r["project"] or "(unregistered)", "status": r["status"], "outcome": r["outcome"],
            "started_at": st.isoformat() if st else None, "completed_at": en.isoformat() if en else None,
            "last_activity_at": r["last_activity_at"], "wall_s": wall, "idle_s": 0.0, "active_s": None,
            "message": trunc(r["user_message"], 160), "cost": None, "tokens": 0, "error": None,
            "exit_reason": None, "cancel_reason": None, "first_failed_node": None,
            "category": None, "failure_kind": None, "resumes": 0, "approvals": 0,
        }
    ids = set(runs)

    # ---- events (skip hook_activity: ~87% of rows, no reporting value) ----
    ev = con.execute(
        "select workflow_run_id, event_type, step_name, data, created_at from remote_agent_workflow_events "
        "where event_type != 'hook_activity' order by workflow_run_id, event_order").fetchall()

    node_stats = defaultdict(lambda: {"completed": 0, "failed": 0, "ms": 0, "kinds": Counter(), "cost": 0.0})
    wf_cost = defaultdict(float)
    model_stats = defaultdict(lambda: {"nodes": 0, "cost": 0.0, "tokens": 0})
    tok_total = Counter()
    tool_stats = defaultdict(lambda: {"calls": 0, "failed": 0, "ms": 0})
    approval_open = defaultdict(list)
    decisions = Counter()
    loop_counts = defaultdict(int)
    resumes_total = 0
    last_end = {}  # run -> datetime of last failed/cancelled event, to measure idle gap before a resume
    first_failed_kind = {}
    runs_with_cost = set()
    nodes_without_cost = 0

    for e in ev:
        rid = e["workflow_run_id"]
        if rid not in ids:
            continue
        run = runs[rid]
        t, d, at = e["event_type"], jload(e["data"]), parse_ts(e["created_at"])

        if t in ("node_completed", "node_failed"):
            node = d.get("node") or {}
            nid, kind = node.get("id") or e["step_name"] or "?", node.get("kind") or "?"
            key = (run["workflow"], nid)
            ns = node_stats[key]
            ms = (d.get("timing") or {}).get("durationMs") or d.get("duration_ms") or 0
            ns["kind"] = kind
            if t == "node_completed":
                ns["completed"] += 1
                ns["ms"] += ms
            elif kind != "loop_group":  # a failed loop_group just echoes its failing child
                ns["failed"] += 1
                fk = d.get("failure_kind") or "unknown"
                ns["kinds"][fk] += 1
                first_failed_kind.setdefault(rid, (fk, nid))
            if kind != "loop_group":
                sp = d.get("spend") or {}
                cu = sp.get("costUsd") or {}
                if cu.get("source") not in (None, "unavailable") and isinstance(cu.get("value"), (int, float)):
                    c = float(cu["value"])
                    run["cost"] = (run["cost"] or 0.0) + c
                    runs_with_cost.add(rid)
                    wf_cost[run["workflow"]] += c
                    ns["cost"] += c
                    tk = (sp.get("tokens") or {}).get("value") or {}
                    tsum = 0
                    for k2 in ("input", "output", "cacheRead", "cacheWrite"):
                        v = tk.get(k2) or 0
                        tok_total[k2] += v
                        tsum += v
                    run["tokens"] += tsum
                    bm = (d.get("binding") or {}).get("model")
                    if isinstance(bm, dict):
                        mdl = (bm.get("resolved") or {}).get("value") or bm.get("requested")
                    else:
                        mdl = bm
                    mdl = mdl or "(unknown model)"
                    model_stats[mdl]["nodes"] += 1
                    model_stats[mdl]["cost"] += c
                    model_stats[mdl]["tokens"] += tsum
                else:
                    nodes_without_cost += 1

        elif t == "tool_completed":
            ts_ = tool_stats[d.get("tool_name") or "?"]
            ts_["calls"] += 1
            ts_["ms"] += d.get("duration_ms") or 0
            if d.get("tool_outcome") not in (None, "success"):
                ts_["failed"] += 1

        elif t == "approval_requested":
            approval_open[rid].append(at)
        elif t == "approval_received":
            run["approvals"] += 1
            decisions[d.get("decision") or "unknown"] += 1
            if approval_open[rid] and at:
                start = approval_open[rid].pop(0)
                if start:
                    run["idle_s"] += max((at - start).total_seconds(), 0)
        elif t == "loop_iteration_started":
            loop_counts[(rid, e["step_name"] or "loop")] += 1
        elif t == "workflow_resumed":
            run["resumes"] += 1
            resumes_total += 1
            if rid in last_end and at:
                run["idle_s"] += max((at - last_end[rid]).total_seconds(), 0)
                del last_end[rid]
        elif t in ("workflow_failed", "workflow_cancelled"):
            last_end[rid] = at
            term = d.get("terminal_record") or {}
            run["error"] = d.get("error") or term.get("error")
            run["first_failed_node"] = term.get("first_failed_node")
            if t == "workflow_failed":
                run["exit_reason"] = d.get("exit_reason")
            else:
                run["cancel_reason"] = d.get("cancel_reason")

    # ---- per-run derivations ----
    for rid, run in runs.items():
        if run["wall_s"] is not None:
            run["active_s"] = max(run["wall_s"] - run["idle_s"], 0)
        fk = first_failed_kind.get(rid)
        if run["status"] == "failed":
            run["failure_kind"] = fk[0] if fk else None
            if not run["first_failed_node"] and fk:
                run["first_failed_node"] = fk[1]
            run["category"] = classify_failure(run["error"], run["exit_reason"], run["failure_kind"], run["first_failed_node"])
        elif run["status"] == "cancelled":
            run["category"] = cancel_category(run["cancel_reason"], run["error"])
            if run["error"]:
                run["failure_kind"] = fk[0] if fk else None
        run["error"] = trunc(run["error"], 600) if run["error"] else None

    lst = list(runs.values())
    by_status = Counter(r["status"] for r in lst)
    finished = [r for r in lst if r["status"] in FINISHED]
    n_ok, n_fail, n_can = (by_status.get(s, 0) for s in FINISHED)
    n_active = len(lst) - len(finished)

    def rate(a, b):
        return a / b if b else None

    verified = sum(1 for r in lst if r["status"] == "completed" and r["outcome"] == "succeeded")
    outcome_other = Counter(r["outcome"] for r in lst if r["status"] == "completed" and r["outcome"] not in (None, "succeeded"))

    summary = {
        "total": len(lst), "completed": n_ok, "failed": n_fail, "cancelled": n_can, "active": n_active,
        "success_rate": rate(n_ok, len(finished)), "success_rate_excl_cancelled": rate(n_ok, n_ok + n_fail),
        "completed_outcome_verified": verified, "completed_outcome_unrecorded": n_ok - verified - sum(outcome_other.values()),
        "completed_outcome_other": dict(outcome_other),
        "cancelled_after_error": sum(1 for r in lst if r["status"] == "cancelled" and r["error"]),
        "resumed_runs": sum(1 for r in lst if r["resumes"]), "resumes": resumes_total,
    }

    def dur_block(rs):
        return {"wall": dstats([r["wall_s"] for r in rs]), "active": dstats([r["active_s"] for r in rs]),
                "idle": dstats([r["idle_s"] for r in rs if r["wall_s"] is not None])}
    durations = {"completed": dur_block([r for r in lst if r["status"] == "completed"]),
                 "failed": dur_block([r for r in lst if r["status"] == "failed"]),
                 "cancelled": dur_block([r for r in lst if r["status"] == "cancelled"]),
                 "all_finished": dur_block(finished)}

    # ---- workflows ----
    wfs = []
    for name in sorted({r["workflow"] for r in lst}):
        rs = [r for r in lst if r["workflow"] == name]
        c = Counter(r["status"] for r in rs)
        fin = [r for r in rs if r["status"] in FINISHED]
        ok_runs = [r for r in rs if r["status"] == "completed"]
        cost = sum(r["cost"] or 0 for r in rs)
        ok_cost = sum(r["cost"] or 0 for r in ok_runs)
        wfs.append({
            "name": name, "short": short_workflow(name), "runs": len(rs), "completed": c.get("completed", 0),
            "failed": c.get("failed", 0), "cancelled": c.get("cancelled", 0), "active": len(rs) - len(fin),
            "success_rate": rate(c.get("completed", 0), len(fin)),
            "wall": dstats([r["wall_s"] for r in ok_runs]), "active_time": dstats([r["active_s"] for r in ok_runs]),
            "wall_all": dstats([r["wall_s"] for r in fin]),
            "cost": cost, "cost_per_completed": rate(ok_cost, sum(1 for r in ok_runs if r["cost"] is not None)) if ok_runs else None,
            "wasted_cost": sum(r["cost"] or 0 for r in rs if r["status"] in ("failed", "cancelled")),
            "last_run": max((r["started_at"] for r in rs if r["started_at"]), default=None),
        })
    wfs.sort(key=lambda w: -w["runs"])

    # ---- failures ----
    fail_runs = [r for r in lst if r["status"] == "failed"]
    cat = defaultdict(list)
    for r in fail_runs:
        cat[r["category"]].append(r)
    failure_categories = []
    for name, rs in sorted(cat.items(), key=lambda kv: -len(kv[1])):
        failure_categories.append({
            "category": name, "count": len(rs), "cost": sum(r["cost"] or 0 for r in rs),
            "workflows": dict(Counter(short_workflow(r["workflow"]) for r in rs).most_common()),
            "nodes": dict(Counter(r["first_failed_node"] or "(no node)" for r in rs).most_common(5)),
            "examples": [{"run": r["short"], "workflow": short_workflow(r["workflow"]), "node": r["first_failed_node"],
                          "when": r["started_at"], "message": r["error"]} for r in
                         sorted(rs, key=lambda x: x["started_at"] or "", reverse=True)[:3]],
        })
    cancel_cat = Counter(r["category"] for r in lst if r["status"] == "cancelled")
    cancel_examples = [{"run": r["short"], "workflow": short_workflow(r["workflow"]), "category": r["category"],
                        "message": r["error"]} for r in lst if r["status"] == "cancelled" and r["error"]][:5]

    failing_nodes = []
    for (wf, nid), ns in node_stats.items():
        if ns["failed"]:
            tot = ns["completed"] + ns["failed"]
            failing_nodes.append({"workflow": short_workflow(wf), "node": nid, "failed": ns["failed"], "attempts": tot,
                                  "fail_rate": ns["failed"] / tot, "kinds": dict(ns["kinds"])})
    failing_nodes.sort(key=lambda n: (-n["failed"], -n["fail_rate"]))

    slow_nodes = sorted(({"workflow": short_workflow(wf), "node": nid, "mean_s": ns["ms"] / ns["completed"] / 1000,
                          "runs": ns["completed"], "total_s": ns["ms"] / 1000}
                         for (wf, nid), ns in node_stats.items()
                         if ns["completed"] and ns.get("kind") != "loop_group"), key=lambda n: -n["total_s"])[:8]

    # ---- spend ----
    total_cost = sum(r["cost"] or 0 for r in lst)
    ok_cost = sum(r["cost"] or 0 for r in lst if r["status"] == "completed")
    wasted = sum(r["cost"] or 0 for r in lst if r["status"] in ("failed", "cancelled"))
    ok_with_cost = sum(1 for r in lst if r["status"] == "completed" and r["cost"] is not None)
    in_side = tok_total["input"] + tok_total["cacheRead"] + tok_total["cacheWrite"]
    spend = {
        "total": total_cost, "completed": ok_cost, "wasted": wasted, "runs_with_cost": len(runs_with_cost),
        "cost_per_completed": rate(ok_cost, ok_with_cost), "wasted_share": rate(wasted, total_cost),
        "tokens": dict(tok_total), "cache_read_share": rate(tok_total["cacheRead"], in_side),
        "nodes_without_cost_data": nodes_without_cost,
        "by_model": sorted(({"model": m, **v} for m, v in model_stats.items()), key=lambda x: -x["cost"]),
        "top_runs": [{"run": r["short"], "workflow": short_workflow(r["workflow"]), "status": r["status"],
                      "cost": r["cost"], "message": r["message"]}
                     for r in sorted((x for x in lst if x["cost"]), key=lambda x: -x["cost"])[:5]],
    }

    # ---- human-in-the-loop, loops ----
    appr_waits = [r["idle_s"] for r in lst if r["approvals"]]
    per_loop = list(loop_counts.values())
    human = {
        "approvals": sum(decisions.values()), "decisions": dict(decisions),
        "runs_with_approvals": sum(1 for r in lst if r["approvals"]),
        "idle_median_s": statistics.median(appr_waits) if appr_waits else None,
        "idle_total_s": sum(r["idle_s"] for r in lst),
        "loop_iterations": sum(per_loop), "loops": len(per_loop),
        "loop_avg": statistics.fmean(per_loop) if per_loop else None, "loop_max": max(per_loop) if per_loop else None,
    }

    # ---- trend ----
    dated = [(parse_ts(r["started_at"]), r) for r in lst if r["started_at"]]
    span_days = (max(d for d, _ in dated) - min(d for d, _ in dated)).days + 1 if dated else 0
    bucket = "day" if span_days <= 31 else "week"
    tb = defaultdict(Counter)
    for d, r in dated:
        if bucket == "day":
            k = d.strftime("%Y-%m-%d")
        else:
            monday = (d - timedelta(days=d.weekday())).date()
            k = monday.isoformat()
        tb[k][r["status"]] += 1
    trend_rows = []
    if tb:
        keys = sorted(tb)
        # fill empty buckets so gaps are visible
        cur = datetime.fromisoformat(keys[0]).date()
        end = datetime.fromisoformat(keys[-1]).date()
        step = timedelta(days=1 if bucket == "day" else 7)
        while cur <= end:
            c = tb.get(cur.isoformat(), Counter())
            trend_rows.append({"label": cur.isoformat(), "completed": c.get("completed", 0),
                               "failed": c.get("failed", 0), "cancelled": c.get("cancelled", 0),
                               "other": sum(v for k, v in c.items() if k not in FINISHED)})
            cur += step
    trend = {"bucket": bucket, "rows": trend_rows}

    # ---- projects, tools ----
    projects = []
    for p in sorted({r["project"] for r in lst}):
        rs = [r for r in lst if r["project"] == p]
        c = Counter(r["status"] for r in rs)
        projects.append({"name": p, "runs": len(rs), "completed": c.get("completed", 0), "failed": c.get("failed", 0),
                         "cancelled": c.get("cancelled", 0), "cost": sum(r["cost"] or 0 for r in rs)})
    projects.sort(key=lambda x: -x["runs"])
    tools = sorted(({"tool": k, **v, "fail_rate": rate(v["failed"], v["calls"])} for k, v in tool_stats.items()),
                   key=lambda x: -x["calls"])[:10]

    # ---- install health ----
    home = db_path.parent
    health = {"db_path": str(db_path), "db_mb": round(db_path.stat().st_size / 1e6, 1)}
    try:
        health["events_total"] = con.execute("select count(*) from remote_agent_workflow_events").fetchone()[0]
        health["hook_activity_events"] = con.execute(
            "select count(*) from remote_agent_workflow_events where event_type='hook_activity'").fetchone()[0]
        health["conversations"] = con.execute("select count(*) from remote_agent_conversations").fetchone()[0]
        health["environments"] = {r[0] or "unknown": r[1] for r in con.execute(
            "select status, count(*) from remote_agent_isolation_environments group by 1")}
        health["codebases"] = con.execute("select count(*) from remote_agent_codebases").fetchone()[0]
    except sqlite3.Error:
        pass
    logs = home / "logs"
    if logs.is_dir():
        files = [f for f in logs.iterdir() if f.is_file()]
        health["log_files"] = len(files)
        health["log_mb"] = round(sum(f.stat().st_size for f in files) / 1e6, 2)
    try:
        health["install"] = json.loads((home / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    stuck = []
    for r in lst:
        if r["status"] not in FINISHED:
            la = parse_ts(r["last_activity_at"]) or parse_ts(r["started_at"])
            stuck.append({"run": r["short"], "workflow": short_workflow(r["workflow"]), "status": r["status"],
                          "idle_hours": round((now - la).total_seconds() / 3600, 1) if la else None})
    health["unfinished_runs"] = stuck

    # ---- slowest ----
    slowest = [{"run": r["short"], "workflow": short_workflow(r["workflow"]), "status": r["status"],
                "wall_s": r["wall_s"], "active_s": r["active_s"], "cost": r["cost"], "message": r["message"]}
               for r in sorted((x for x in finished if x["wall_s"] is not None), key=lambda x: -x["wall_s"])[:5]]

    all_start = [parse_ts(r["started_at"]) for r in lst if r["started_at"]]
    out = {
        "meta": {"generated_at": now.isoformat(), "db_path": str(db_path), "since": since_dt.isoformat() if since_dt else None,
                 "project_filter": args.project, "first_run": min(all_start).isoformat() if all_start else None,
                 "last_run": max(all_start).isoformat() if all_start else None, "span_days": span_days},
        "summary": summary, "durations": durations, "workflows": wfs,
        "failures": {"categories": failure_categories, "cancel_categories": dict(cancel_cat),
                     "cancel_error_examples": cancel_examples, "failing_nodes": failing_nodes[:10]},
        "slow_nodes": slow_nodes, "slowest_runs": slowest, "spend": spend, "human": human,
        "trend": trend, "projects": projects, "tools": tools, "health": health,
        "runs": sorted(lst, key=lambda r: r["started_at"] or "", reverse=True),
    }
    text = json.dumps(out, indent=2, ensure_ascii=True, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("Wrote %s (%d runs)" % (args.out, len(lst)))
    else:
        print(text)


if __name__ == "__main__":
    main()
