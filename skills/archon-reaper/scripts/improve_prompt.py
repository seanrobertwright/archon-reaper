#!/usr/bin/env python3
"""Build a prompt that turns an Archon run report into changes to the user's development system.

    python improve_prompt.py stats.json [--narrative narrative.json] [--out prompt.md]

The prompt is meant to be pasted into a fresh Claude session on the same machine. It carries the
evidence from stats.json (the failure groups, their example errors with run ids, the nodes that
fail most, the spend lost and the versions) and, when a narrative is given, the report's diagnoses
and recommendations, labelled as hypotheses to confirm. The rest of the prompt tells that session
how to find the root causes and where each fix belongs: a workflow, an instruction file, a skill,
a hook or check, or the environment.

The text is built only from stats.json and the narrative, so the same input gives the same prompt.
Standard library only.
"""
import argparse
import json
import sys
from pathlib import Path

# Limits that keep the prompt readable and well inside a session's context.
MAX_GROUPS = 8
MAX_EXAMPLES = 3
MAX_MESSAGE = 500
MAX_NODES = 8
MAX_WORKFLOWS = 8
MAX_PROJECTS = 8


def _money(v):
    return "$%.2f" % v if isinstance(v, (int, float)) else "no cost reported"


def _pc(v):
    return "%.1f%%" % (v * 100) if isinstance(v, (int, float)) else "n/a"


def _day(iso):
    return iso[:10] if isinstance(iso, str) and len(iso) >= 10 else "unknown date"


def _clip(text, limit=MAX_MESSAGE):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _counts(mapping):
    """'implement 9, archon-ship 2' from a {name: count} dict, largest first."""
    if not mapping:
        return "none"
    items = sorted(mapping.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return ", ".join("%s %d" % (k, v) for k, v in items)


def _quote(text):
    """Text inside a Markdown blockquote-free inline quote, with backticks made harmless."""
    return '"%s"' % _clip(text).replace("`", "'")


def _evidence(d, n):
    s, sp, meta = d["summary"], d.get("spend") or {}, d.get("meta") or {}
    lines = ["## The evidence", ""]
    scope = []
    if meta.get("since"):
        scope.append("since %s" % meta["since"])
    if meta.get("project_filter"):
        scope.append("projects matching '%s'" % meta["project_filter"])
    lines.append("- Period: %s to %s (%s days)%s." % (
        _day(meta.get("first_run")), _day(meta.get("last_run")), meta.get("span_days", "?"),
        ", filtered to " + " and ".join(scope) if scope else ""))
    lines.append("- Runs: %d. Completed %d, failed %d, cancelled %d. Success rate %s (completed out of finished)." % (
        s["total"], s["completed"], s["failed"], s["cancelled"], _pc(s.get("success_rate"))))
    if s.get("cancelled_after_error"):
        lines.append("- %d cancelled runs hit an error first: count them as failures." % s["cancelled_after_error"])
    if s.get("completed_outcome_unrecorded"):
        lines.append("- %d completed runs recorded no outcome, so \"completed\" is not proof they did their job." %
                     s["completed_outcome_unrecorded"])
    if sp.get("total") is not None:
        lines.append("- Spend: %s in total; %s (%s) on runs that failed or were cancelled. Spend is provider-reported." % (
            _money(sp.get("total")), _money(sp.get("wasted")), _pc(sp.get("wasted_share"))))
    v = d.get("versions") or {}
    by_version = v.get("by_version") or []
    if len(by_version) > 1:
        parts = ["%s: %d runs, %s success" % (b["version"], b["runs"], _pc(b.get("success_rate"))) for b in by_version]
        lines.append("- Archon versions: %s. A run's version is inferred from the install, not recorded." % "; ".join(parts))
    if v.get("update_available"):
        lines.append("- Archon %s is installed and %s is available." % (v.get("current"), v.get("latest_available")))
    lines.append("")

    groups = sorted(d["failures"]["categories"], key=lambda c: (-(c.get("cost") or 0), -c["count"], c["category"]))
    notes = (n or {}).get("failure_notes") or {}
    if groups:
        lines += ["### Failure groups, costliest first", ""]
        lines.append("Each group is a keyword classification of the run's final error, so a group can mix causes. "
                     "Read the examples before trusting the label.")
        lines.append("")
        for i, c in enumerate(groups[:MAX_GROUPS], 1):
            lines.append("%d. **%s**: %d runs, %s spent on them. Workflows: %s. Nodes: %s. Versions: %s." % (
                i, c["category"], c["count"], _money(c.get("cost")), _counts(c.get("workflows")),
                _counts(c.get("nodes")), _counts(c.get("versions"))))
            if notes.get(c["category"]):
                lines.append("   - The report's diagnosis, a hypothesis to confirm: %s" % _clip(notes[c["category"]]))
            for ex in (c.get("examples") or [])[:MAX_EXAMPLES]:
                lines.append("   - Run `%s` (%s, node %s, Archon %s, %s): %s" % (
                    ex.get("run"), ex.get("workflow"), ex.get("node") or "none", ex.get("version") or "unknown",
                    _day(ex.get("when")), _quote(ex.get("message"))))
        if len(groups) > MAX_GROUPS:
            rest = groups[MAX_GROUPS:]
            lines.append("%d smaller groups (%d runs) are left out." % (len(rest), sum(c["count"] for c in rest)))
        lines.append("")

    nodes = d["failures"].get("failing_nodes") or []
    if nodes:
        lines += ["### Nodes that fail most, counted per attempt", "",
                  "A node can fail and be retried inside the same run, so these counts are higher than the run failures above.", ""]
        for x in nodes[:MAX_NODES]:
            lines.append("- %s / %s: %d of %d attempts failed (%s); kinds: %s." % (
                x["workflow"], x["node"], x["failed"], x["attempts"], _pc(x.get("fail_rate")), _counts(x.get("kinds"))))
        lines.append("")

    wfs = d.get("workflows") or []
    if wfs:
        lines += ["### Workflows, by number of runs", ""]
        for w in wfs[:MAX_WORKFLOWS]:
            thin = " Too few runs for a meaningful rate." if w["runs"] < 5 else ""
            lines.append("- `%s`: %d runs, %s success, %s spent, %s on failed or cancelled runs.%s" % (
                w.get("name") or w.get("short"), w["runs"], _pc(w.get("success_rate")), _money(w.get("cost")),
                _money(w.get("wasted_cost")), thin))
        lines.append("")

    projects = d.get("projects") or []
    if projects:
        lines += ["### Projects", ""]
        for p in projects[:MAX_PROJECTS]:
            lines.append("- `%s`: %d runs, %d failed, %d cancelled." % (p["name"], p["runs"], p["failed"], p["cancelled"]))
        lines.append("")

    lines += ["### KPI summary", "", "Ratios run from 0 to 1, money is in USD and time in seconds; null means the stats hold no figure.", ""]
    lines += kpi_markdown(d)
    lines.append("")

    recs = [recommendation(r) for r in (n or {}).get("recommendations") or []]
    recs = [r for r in recs if r["action"]]
    if recs:
        lines += ["### The report's recommendations, as starting points", "",
                  "Weigh each one against what you find; keep, change or drop it, and say which and why.", ""]
        for i, r in enumerate(recs, 1):
            lines.append("%d. %s" % (i, _clip(r["action"])))
            if r["reasoning"]:
                lines.append("   - Why: %s" % _clip(r["reasoning"]))
            if r["evidence"]:
                lines.append("   - Evidence: %s" % _clip(r["evidence"]))
            for label, key in (("Pro", "pros"), ("Con", "cons")):
                for x in r[key]:
                    lines.append("   - %s: %s" % (label, _clip(x)))
        lines.append("")
    return lines


def _num(v, digits=4):
    """A machine value: ints stay ints, floats are rounded, anything else is None."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v)) if digits == 0 else round(v, digits)
    return v


def kpi_summary(d):
    """The report's key figures as rows of {key, value, unit, meaning}, for agents to read.

    Keys are stable snake_case names; values are raw (ratios from 0 to 1, money in USD, time in
    seconds); a figure the stats do not hold is None. The report's table and the prompt both use
    these rows, so an agent reads the same numbers in either place."""
    s, sp, meta = d.get("summary") or {}, d.get("spend") or {}, d.get("meta") or {}
    durs = ((d.get("durations") or {}).get("completed") or {})
    wall, active = durs.get("wall") or {}, durs.get("active") or {}
    toks = sp.get("tokens") or {}
    v = d.get("versions") or {}
    health = d.get("health") or {}
    human = d.get("human") or {}
    groups = sorted((d.get("failures") or {}).get("categories") or [], key=lambda c: (-c["count"], -(c.get("cost") or 0), c["category"]))
    costly = sorted(groups, key=lambda c: (-(c.get("cost") or 0), -c["count"], c["category"]))
    nodes = sorted((d.get("failures") or {}).get("failing_nodes") or [], key=lambda x: (-x["failed"], x["workflow"], x["node"]))
    wfs = d.get("workflows") or []
    rows = [
        ("period_start", _day(meta.get("first_run")) if meta.get("first_run") else None, "date", "Start of the first run in the window (UTC)."),
        ("period_end", _day(meta.get("last_run")) if meta.get("last_run") else None, "date", "Start of the last run in the window (UTC)."),
        ("period_days", meta.get("span_days"), "days", "Days the window covers."),
        ("runs_total", s.get("total"), "runs", "Workflow runs in the window."),
        ("runs_completed", s.get("completed"), "runs", "Runs that ended completed."),
        ("runs_failed", s.get("failed"), "runs", "Runs that ended failed."),
        ("runs_cancelled", s.get("cancelled"), "runs", "Runs that ended cancelled."),
        ("runs_active", s.get("active"), "runs", "Runs not finished when the stats were read."),
        ("success_rate", _num(s.get("success_rate")), "ratio", "completed / (completed + failed + cancelled)."),
        ("success_rate_excl_cancelled", _num(s.get("success_rate_excl_cancelled")), "ratio", "completed / (completed + failed)."),
        ("completed_outcome_verified", s.get("completed_outcome_verified"), "runs", "Completed runs that recorded the outcome 'succeeded'."),
        ("completed_outcome_unrecorded", s.get("completed_outcome_unrecorded"), "runs", "Completed runs that recorded no outcome."),
        ("cancelled_after_error", s.get("cancelled_after_error"), "runs", "Cancelled runs that hit an error first: failures in disguise."),
        ("resumed_runs", s.get("resumed_runs"), "runs", "Runs resumed at least once."),
        ("approvals", human.get("approvals"), "count", "Approval decisions received across all runs."),
        ("spend_total_usd", _num(sp.get("total"), 2), "USD", "Provider-reported spend across all runs."),
        ("spend_wasted_usd", _num(sp.get("wasted"), 2), "USD", "Spend on runs that failed or were cancelled."),
        ("spend_wasted_share", _num(sp.get("wasted_share")), "ratio", "spend_wasted_usd / spend_total_usd."),
        ("cost_per_completed_usd", _num(sp.get("cost_per_completed"), 2), "USD", "Spend on completed runs / completed runs that reported a cost."),
        ("tokens_input", toks.get("input"), "tokens", "Input tokens as reported; cache reads and cache writes are counted separately."),
        ("tokens_output", toks.get("output"), "tokens", "Output tokens reported."),
        ("cache_read_share", _num(sp.get("cache_read_share")), "ratio", "Cache reads / (input + cache reads + cache writes)."),
        ("duration_completed_median_s", _num(wall.get("median"), 0), "seconds", "Median total time of a completed run."),
        ("duration_completed_p90_s", _num(wall.get("p90"), 0), "seconds", "90th percentile total time of a completed run."),
        ("hands_on_completed_median_s", _num(active.get("median"), 0), "seconds", "Median time of a completed run without approval waits and failure gaps."),
        ("failure_groups", len(groups), "groups", "Distinct failure categories."),
        ("top_failure_group", groups[0]["category"] if groups else None, "name", "The failure category with the most runs; a tie goes to the costlier."),
        ("top_failure_group_runs", groups[0]["count"] if groups else None, "runs", "Runs in top_failure_group."),
        ("costliest_failure_group", costly[0]["category"] if costly else None, "name", "The failure category with the most spend."),
        ("costliest_failure_group_usd", _num(costly[0].get("cost"), 2) if costly else None, "USD", "Spend on runs in costliest_failure_group."),
        ("top_failing_node", "%s/%s" % (nodes[0]["workflow"], nodes[0]["node"]) if nodes else None, "workflow/node", "The node with the most failed attempts."),
        ("top_failing_node_fail_rate", _num(nodes[0].get("fail_rate")) if nodes else None, "ratio", "Failed attempts / attempts for top_failing_node."),
        ("top_workflow", (wfs[0].get("name") or wfs[0].get("short")) if wfs else None, "name", "The workflow with the most runs."),
        ("top_workflow_success_rate", _num(wfs[0].get("success_rate")) if wfs else None, "ratio", "success_rate of top_workflow."),
        ("archon_version_current", v.get("current"), "version", "Archon version installed now."),
        ("archon_version_latest", v.get("latest_available"), "version", "Newest Archon release seen, when known."),
        ("archon_update_available", v.get("update_available"), "boolean", "Whether a newer Archon release is available."),
        ("db_size_mb", _num(health.get("db_mb"), 1), "MB", "Size of the Archon database file."),
    ]
    return [{"key": k, "value": val, "unit": u, "meaning": m} for k, val, u, m in rows]


def _cell(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v).replace("|", "\\|")


def kpi_markdown(d):
    """kpi_summary as a Markdown table."""
    lines = ["| key | value | unit | meaning |", "| --- | --- | --- | --- |"]
    lines += ["| %s | %s | %s | %s |" % (r["key"], _cell(r["value"]), r["unit"], r["meaning"]) for r in kpi_summary(d)]
    return lines


def recommendation(r):
    """A narrative recommendation as a dict with action, reasoning, evidence, pros and cons.

    A recommendation may be a plain string (the older narrative format) or an object; either way
    the result has every key, with empty values where the narrative gave none.
    """
    if isinstance(r, str):
        return {"action": r.strip(), "reasoning": "", "evidence": "", "pros": [], "cons": []}
    if not isinstance(r, dict):
        return {"action": "", "reasoning": "", "evidence": "", "pros": [], "cons": []}

    def text(key):
        v = r.get(key)
        return v.strip() if isinstance(v, str) else ""

    def items(key):
        v = r.get(key)
        if isinstance(v, str):
            v = [v]
        return [x.strip() for x in v or [] if isinstance(x, str) and x.strip()]

    return {"action": text("action"), "reasoning": text("reasoning"), "evidence": text("evidence"),
            "pros": items("pros"), "cons": items("cons")}


BRIEF = """# Improve my development process from my Archon run history

You are working with me on my own machine. My local Archon install runs AI coding workflows for my projects, and its run history shows the same failures coming back. Find the causes and change my development system so they stop: my instruction files (CLAUDE.md, AGENTS.md), my skills, my Claude Code plugins and hooks, my Archon workflows and their scripts, my checks, and the environment the runs start from.

The evidence below comes from an archon-reaper report of my Archon database. Treat its diagnoses as hypotheses: confirm each one against the logs and files before you act on it."""

METHOD = """## How to work

1. **Map my development system before changing anything.** Read, and change nothing yet:
   - `~/.claude/CLAUDE.md`, and each affected project's `CLAUDE.md` and `AGENTS.md`;
   - my skills (`~/.claude/skills/` and each project's `.claude/skills/`) and installed Claude Code plugins, with their hooks;
   - the Archon workflows involved, at their source: the project's `.archon/workflows/`, `~/.archon/workflows/`, or the pack or plugin that provides a workflow named like `owner/pack:workflow`; and `~/.archon/config.yaml`;
   - each project's git hooks and CI.
   Write down which file owns which behaviour. A fix belongs in the file that owns the behaviour.
2. **Confirm each failure group from the runs themselves.** Open the example runs: the per-run logs at `~/.archon/workspaces/<owner>/<repo>/logs/<run-id>.jsonl` (a short id is the first 8 characters of the run id), or `~/.archon/archon.db` opened read-only (`mode=ro`). Read the failing node's definition and its script or prompt. Do not write to the database, and do not launch, resume, cancel or approve runs.
3. **Find the root causes.** Put each failure in one class:
   - input: the ticket, spec or message could not be worked;
   - workflow defect: the YAML, a prompt, a script, a gate, a timeout or an output reference;
   - agent behaviour: something the implementing agent does or misses that an instruction should settle;
   - environment: a dirty checkout, a lock file, a leftover worktree, a missing tool or credential;
   - provider or Archon fault: a crash or a bug outside my files.
   Merge groups that share a cause, and split a group that mixes causes. Fix causes, not error messages.
4. **Rank them** by spend lost and runs lost, then by how often they recur. Leave a one-off alone unless its fix is trivial, and say when a figure is too thin to act on.
5. **Design one precise fix per root cause, at the layer that owns it:**
   - workflow defect: change the workflow, script or prompt at its source, not a local copy;
   - agent behaviour: the narrowest instruction that reaches the agent, in this order: the node's prompt, the project's `AGENTS.md` or `CLAUDE.md`, my global `CLAUDE.md` only when it holds in every project;
   - a procedure that recurs: a skill, or a change to the skill that already covers it;
   - an invariant that must always hold: a hook, a gate script or a check, because a sentence in an instruction file is not enforcement;
   - environment: a preflight step in the workflow that fails fast with a message saying what to fix, plus any one-time cleanup listed separately for me to run;
   - Archon fault: a minimal reproduction and draft issue text; when a newer Archon release fixes it, cite its release notes and propose the upgrade.
   Before adding a rule or a skill, search for one that already covers the case and amend it. Prefer a shorter or clearer instruction to one more instruction. Keep each change as small as the cause allows.
6. **Show me the plan and stop.** For each fix give: the root cause; the evidence (run ids, counts, spend); the file and the exact change as a diff; the reasoning, meaning why this layer and this change rather than the alternatives you considered; its pros and its cons, including what it costs in spend, maintenance or false alarms; the effect you expect; and how to verify it. Order the fixes by impact. Then wait for my approval. Ask me only about decisions that are mine to make.
7. **Apply what I approve.** Work in the repository that owns each file, on a branch, following that repository's own conventions for tests, lint and commit messages. Validate every changed workflow with `archon validate workflows <name>`. Do not launch workflows or make model calls to test a fix unless I agree, because they spend.
8. **Report back:** what changed and where, what is left undone and why, and what the next archon-reaper report should show if each fix worked, such as a node's failure rate or a failure group that should disappear.

## Rules

- Every claim comes from the evidence below or from a file or log you read; quote it.
- Never read, copy or change secrets, credentials or provider keys.
- Do not edit generated or vendored copies; change their source.
- Keep instruction files short and imperative, with one rule per behaviour."""


def build_prompt(d, n=None):
    """The prompt as Markdown text, from collect_stats output and an optional narrative."""
    parts = [BRIEF, ""]
    if n and n.get("headline"):
        parts += ["Summary from the report: %s" % _clip(n["headline"], 800), ""]
    parts += _evidence(d, n)
    parts.append(METHOD)
    return "\n".join(parts).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stats")
    ap.add_argument("--narrative")
    ap.add_argument("--out", help="write the prompt here; without it the prompt is printed")
    a = ap.parse_args()
    d = json.loads(Path(a.stats).read_text(encoding="utf-8"))
    n = json.loads(Path(a.narrative).read_text(encoding="utf-8")) if a.narrative else None
    text = build_prompt(d, n)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print("Wrote %s (%d characters)" % (a.out, len(text)))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
