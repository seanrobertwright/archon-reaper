"""Builds a small synthetic Archon database for tests and the sample report.

The layout mirrors the tables and columns collect_stats.py reads. No real data is used.
build() also writes logs/serve.log, install.json and update-check.json into the database's
folder, so point it at a folder of its own.
Run directly to write a fixture:  python tests/fixture.py <path-to-new.db>
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
create table remote_agent_codebases (id text primary key, name text, repository_url text, default_cwd text,
  default_branch text, ai_assistant_type text, kind text, commands text, created_at text, updated_at text);
create table remote_agent_workflow_runs (id text primary key, conversation_id text, codebase_id text, workflow_name text,
  user_message text, status text, outcome text, current_step_index integer, metadata text, parent_conversation_id text,
  user_id text, parent_run_id text, adopted_from_run_id text, started_at text, completed_at text, last_activity_at text,
  working_path text, output_root text, checkout_baseline text);
create table remote_agent_workflow_events (id text primary key, workflow_run_id text, event_order integer,
  event_type text, step_index integer, step_name text, data text, created_at text);
create table remote_agent_conversations (id text primary key);
create table remote_agent_isolation_environments (id text primary key, status text);
create table remote_agent_schema_version (id integer primary key, created_app_version text, app_version text,
  created_at text, applied_at text, schema_fingerprint text);
"""

PACK = "demo-pack:implement"
OLD_VERSION, NEW_VERSION, LATEST_VERSION = "0.11.1", "0.12.1", "0.13.0"


def epoch_ms(hms):
    """Milliseconds since the epoch for a UTC time of day on the fixture's date."""
    h, m, s = (int(x) for x in hms.split(":"))
    return int(datetime(2026, 10, 1, h, m, s, tzinfo=timezone.utc).timestamp() * 1000)


def write_home(home):
    """The files beside the database that date the upgrade from OLD_VERSION to NEW_VERSION:
    last sign of the old version at 12:10, first sign of the new one at 12:45."""
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    lines = [
        {"level": 40, "time": epoch_ms("12:10:00"), "pid": 1, "module": "db.sqlite", "appVersion": OLD_VERSION, "msg": "db.check"},
        {"level": 30, "time": epoch_ms("12:45:00"), "pid": 2, "module": "cli.serve", "version": NEW_VERSION,
         "msg": "server_dist.download_started"},
        # not Archon's own log lines: neither may count as a version sighting
        {"time": epoch_ms("15:00:00"), "version": "9.9.9"},
        {"level": 30, "time": epoch_ms("15:30:00"), "pid": 3, "module": "agent", "version": "8.8.8", "msg": "tool output"},
    ]
    text = "".join(json.dumps(x) + "\n" for x in lines) + 'agent said "appVersion":"7.7.7" in plain text\n'
    (logs / "serve.log").write_text(text, encoding="utf-8")
    (home / "install.json").write_text(json.dumps({"binary": "archon", "version": NEW_VERSION}), encoding="utf-8")
    (home / "update-check.json").write_text(json.dumps({"latestVersion": LATEST_VERSION}), encoding="utf-8")


def spend(cost, model="sonnet"):
    return {
        "tokens": {"source": "provider", "value": {"input": 1000, "output": 200, "cacheRead": 500, "cacheWrite": 100}},
        "costUsd": {"source": "provider", "value": cost},
        "numTurns": {"source": "provider", "value": 3},
    }, {"model": {"requested": model, "resolved": {"source": "provider", "value": "claude-%s-5-5" % model}}}


def node_event(kind_event, nid, kind, ms, cost=None, model="sonnet", failure_kind=None):
    d = {"node": {"id": nid, "kind": kind}, "invocation": {"loopPath": []}, "timing": {"durationMs": ms}}
    if cost is not None:
        d["spend"], d["binding"] = spend(cost, model)
    else:
        d["spend"] = {"costUsd": {"source": "unavailable", "reason": "not_applicable"}}
    if failure_kind:
        d["failure_kind"] = failure_kind
    return kind_event, nid, d


def build(path, versions=True):
    """Write the database. With versions=True also record the version history: the
    schema-version row in the database and the files written by write_home() beside it.
    versions=False gives an install with no version evidence at all."""
    path = Path(path)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    if versions:
        write_home(path.parent)
        con.execute("insert into remote_agent_schema_version values(1,?,?,?,?,?)",
                    (OLD_VERSION, NEW_VERSION, "2026-09-30 08:00:00", "2026-10-01 20:00:00", "fixture"))
    con.executemany("insert into remote_agent_codebases(id,name) values(?,?)", [("c1", "acme/app"), ("c2", "acme/other")])
    con.executemany("insert into remote_agent_conversations(id) values(?)", [("v1",), ("v2",)])
    con.executemany("insert into remote_agent_isolation_environments(id,status) values(?,?)", [("e1", "active"), ("e2", "destroyed")])

    runs = {}   # id -> (codebase, workflow, status, outcome, started, completed, message, events)
    T = "2026-10-01 %s"

    def run(rid, cb, wf, status, outcome, start, end, msg, events):
        runs[rid] = (cb, wf, status, outcome, T % start if start else None, (T % end) if end else None, msg, events)

    run("r1", "c1", PACK, "completed", "succeeded", "10:00:00", "10:30:00", "Implement issue 1",
        [node_event("node_completed", "build", "agent", 90000, 1.5, "sonnet"),
         # a loop_group repeats its children; the collector must not count its cost
         node_event("node_completed", "implement", "loop_group", 95000, 99.0, "opus"),
         ("workflow_completed", None, {"duration_ms": 1800000})])
    run("r2", "c1", PACK, "completed", None, "11:00:00", "11:10:00", "Implement issue 2",
        [node_event("node_completed", "build", "agent", 60000, 0.5, "haiku")])
    run("r3", "c1", PACK, "failed", None, "12:00:00", "12:20:00", "Implement issue 3",
        [node_event("node_completed", "build", "agent", 60000, 2.0, "sonnet"),
         node_event("node_failed", "gate", "exec", 120000, None, failure_kind="timeout"),
         ("workflow_failed", None, {"error": "DAG workflow 'x' completed with failures: 'gate': Script node 'gate' timed out after 120000ms",
                                    "exit_reason": "node_error", "terminal_record": {"first_failed_node": "gate"}})])
    run("r4", "c2", "demo-pack:setup", "failed", None, "12:30:00", "12:30:05", "Set up",
        [("workflow_failed", None, {"error": "Detached run failed to start: Could not create isolated workspace (git worktree add failed)",
                                    "exit_reason": "launch_failed", "terminal_record": {}})])
    run("r5", "c1", "archon-assist", "cancelled", None, "09:00:00", "09:05:00", "Quick question",
        [("workflow_cancelled", None, {"cancel_reason": "operator", "terminal_record": {"error": None}})])
    run("r6", "c1", "demo-pack:wayfinder", "cancelled", None, "09:10:00", "09:11:00", "#7",
        [node_event("node_failed", "load", "exec", 500, None, failure_kind="exec_failed"),
         ("workflow_cancelled", None, {"cancel_reason": "operator",
                                       "terminal_record": {"error": "Script node 'load' failed [exit 1]: bad input"}})])
    run("r7", "c1", PACK, "failed", None, "13:40:00", "13:41:00", "Implement issue 7",
        [("workflow_failed", None, {"error": "<script>alert(1)</script> Script node 'record' failed [exit 1]: x",
                                    "exit_reason": "node_error", "terminal_record": {"first_failed_node": "record"}})])
    run("r8", "c1", PACK, "completed", "succeeded", "13:00:00", "13:30:00", "Implement issue 8",
        [("approval_requested", None, {}), ("approval_received", None, {"decision": "approved", "comment": "ok"}),
         node_event("node_completed", "build", "agent", 60000, 1.0, "sonnet")])
    run("r9", "c1", PACK, "completed", "succeeded", "14:00:00", "15:00:00", "Implement issue 9",
        [("workflow_failed", None, {"error": "boom", "exit_reason": "node_error", "terminal_record": {}}),
         ("workflow_resumed", None, {}),
         ("workflow_completed", None, {"duration_ms": 3600000})])
    run("r10", "c1", PACK, "running", None, "16:00:00", None, "Still going", [])

    # event timestamps that matter for idle-time maths
    stamps = {("r8", "approval_requested"): "13:00:00", ("r8", "approval_received"): "13:10:00",
              ("r9", "workflow_failed"): "14:10:00", ("r9", "workflow_resumed"): "14:40:00"}
    n = 0
    for rid, (cb, wf, status, outcome, start, end, msg, events) in runs.items():
        con.execute("insert into remote_agent_workflow_runs(id,codebase_id,workflow_name,user_message,status,outcome,"
                    "started_at,completed_at,last_activity_at) values(?,?,?,?,?,?,?,?,?)",
                    (rid, cb, wf, msg, status, outcome, start, end, end or start))
        order = 0
        for ev in events:
            etype, step, data = ev
            n += 1
            order += 1
            created = T % stamps.get((rid, etype), (end or start).split(" ")[1])
            con.execute("insert into remote_agent_workflow_events values(?,?,?,?,?,?,?,?)",
                        ("ev%d" % n, rid, order, etype, None, step, json.dumps(data), created))
        # hook_activity noise the collector must skip
        for _ in range(5 if rid == "r1" else 0):
            n += 1
            order += 1
            con.execute("insert into remote_agent_workflow_events values(?,?,?,?,?,?,?,?)",
                        ("ev%d" % n, rid, order, "hook_activity", None, None, "{}", T % "10:00:00"))
    con.commit()
    con.close()
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/fixture.py <output.db>")
    print("wrote", build(sys.argv[1]))
