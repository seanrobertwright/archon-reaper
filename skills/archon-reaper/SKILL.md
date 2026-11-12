---
name: archon-reaper
description: Build an HTML report on how an Archon installation is performing. It covers run counts (completed, failed, cancelled), success rate, a grouped summary of what went wrong, average duration, runs per workflow, results per Archon version across upgrades, spend, approval waits and install health. Use when the user asks for an Archon report, stats, metrics, run history, "how are my workflows doing", "why do my runs fail", or "what has Archon cost me". NOT for inspecting or controlling a single run (use archon-cli).
---

# Archon Report

Reads the local Archon database (read-only), computes statistics, and publishes a styled HTML artifact.
Two scripts do the numbers and the layout; you add the judgment (the narrative).

## Steps

1. **Collect.** Run the collector with the scratchpad as the output location:

   ```bash
   python -I ~/.claude/skills/archon-reaper/scripts/collect_stats.py --out <scratchpad>/stats.json
   ```

   Optional filters: `--since 30d` (or `12h`, or `2026-10-01`), `--project <substring of project name>`,
   `--db <path>` (default `~/.archon/archon.db`). If the user says "last week" or "for the quix repo", pass the filter.
   If the script exits with a schema error, Archon changed its database layout; report the exact message and stop.
   Do not guess at a fix without reading the schema (`pragma table_info`).

2. **Read `stats.json` and write `narrative.json`** next to it. This is the part the scripts cannot do.
   Read `summary`, `failures.categories` (including each `examples[].message`), `failures.failing_nodes`,
   `spend`, `workflows`, and `versions`, then write:

   ```json
   {
     "headline": "One or two plain sentences: how the period went.",
     "observations": ["3-6 findings, most important first, each with a number"],
     "recommendations": ["0-4 concrete next steps tied to evidence"],
     "failure_notes": {"<exact category name>": "One or two sentences on the likely cause."},
     "version_note": "One or two sentences comparing Archon versions. Omit when the runs cover one version."
   }
   ```

   Rules for the narrative:
   - Every claim must come from `stats.json`. Quote counts, node names, and error text; do not speculate past them.
   - Diagnose from the example error messages. For instance, repeated "timed out after 120000ms" on one node points
     at that node's timeout setting, and a gate that says the implementer did not reach green is an upstream
     problem and not a gate bug. Say which node and workflow.
   - Separate input problems (a run launched against a ticket the workflow could not use) from workflow defects
     and from infrastructure faults (worktree creation). The "Other" and "Script or gate failed" categories often
     mix these, so read their examples before labelling them.
   - Call out when a number is thin (a workflow with 2 runs has no meaningful success rate).
   - Flag cancelled runs that carry an error as failures in disguise (`summary.cancelled_after_error`).
   - When `versions.by_version` has more than one entry, compare them: success rate, the failure categories that
     appear on one version only (`failures.categories[].versions`), and the per-workflow figures in
     `versions.by_version[].workflows`. Compare the same workflow across versions, because the workflow mix usually
     changes too. A version difference is a correlation; say "after the upgrade", not "because of the upgrade",
     unless the error text names the cause.
   - A run's version is inferred, not recorded (see Definitions). Mention `versions.changes[].runs_in_gap`,
     `versions.runs_spanning_change` and `versions.unattributed_runs` when they are large enough to move a comparison.
   - If `versions.update_available` is true, say which version is installed and which is available.
   - Spend is provider-reported and may be an API-equivalent figure. Say "spend", not "bill".
   - Plain sentences. No filler, no hedging stacks.

3. **Render.**

   ```bash
   python -I ~/.claude/skills/archon-reaper/scripts/render_report.py <scratchpad>/stats.json <scratchpad>/archon-report.html --narrative <scratchpad>/narrative.json
   ```

4. **Publish** `archon-report.html` with the Artifact tool. Load the `artifact-design` skill first, as the Artifact tool
   requires, but do not redesign the page: the renderer already follows its contract (tokens, light and dark themes,
   phone layout, Google Fonts only). Use the title already in the file, an icon such as `chart`, and a one-sentence
   description such as "Run outcomes, failures, durations and spend for the local Archon install."
   To refresh an earlier report, publish to the same file path so the URL is kept.

5. **Reply** with the link and three or four lines: the headline numbers, the single biggest problem, and the top
   recommendation. Do not paste the report.

## What the report contains

KPI strip (runs, completed, failed, cancelled, success rate, spend) and an outcome bar; takeaways; per-workflow table
(runs, outcome mix, success, average, median, spend); Archon versions (runs, outcomes, time and spend per version, dated
version changes, success by workflow and version; shown when the runs cover more than one version or the install has
changed version); failure groups with example errors and the versions they occurred on; nodes that fail most;
cancellation reasons; duration (average, median, p90, hands-on vs total, slowest runs, slowest nodes); spend by workflow
and model with the share spent on failed or cancelled runs; approvals, resumes and loop iterations; activity over time
with version changes marked; projects; tool calls; installation health (DB size, workspaces, unfinished runs, installed
version and whether a newer one is available).

## Definitions (keep consistent when you write the narrative)

- **Success rate** = completed / (completed + failed + cancelled). The excluding-cancelled variant is in `summary`.
- **Completed is not verified success.** `summary.completed_outcome_verified` counts completed runs whose `outcome` is
  `succeeded`; the rest never recorded an outcome. Mention this if the gap is large.
- **Total time** = `completed_at - started_at`. **Hands-on time** subtracts approval waits and gaps between a failure
  and a resume.
- **Failure category** comes from keyword rules in `classify_failure()` in `collect_stats.py`. To add a category,
  add a rule there; the renderer picks it up automatically.
- Loop-group nodes are excluded from spend and failing-node tallies because they repeat their children.
- **Archon version of a run** is inferred. Archon stores no version on a run, so `version_observations()` in
  `collect_stats.py` gathers dated sightings of a version from the install: the `remote_agent_schema_version` table,
  `install.json`, `server.json`, the `server/<version>` folders and Archon's structured log lines. `build_timeline()`
  turns them into periods, and a run counts under the version whose period had started when the run began
  (`runs[].archon_version`; `null`, shown as "unknown", when the run predates all evidence).
- **A version change** is dated at the first sighting of the new version. Runs started between the last sighting of
  the old version and that moment stay with the old version and are counted in `versions.changes[].runs_in_gap`.
  A run that started before a change and ended after it stays with its starting version
  (`runs[].spanned_version_change`).
- Only release numbers such as `0.12.1` count as versions. A build that reports the same number as a release (a source
  checkout, for example) cannot be told apart from that release.

## Extending

- A new metric goes in `collect_stats.py` (add a key to the JSON) and a section function in `render_report.py`.
- The `hook_activity` events are skipped on purpose (about 87% of all events and no reporting value).
- A new source of version evidence is one more `(datetime, version, source)` tuple in `version_observations()`.
- The database is opened with `mode=ro`. Never write to it. For anything the DB lacks, the per-run JSONL logs live in
  `~/.archon/workspaces/<owner>/<repo>/logs/<run-id>.jsonl` (see archon-cli `manage-run/troubleshooting.md`).
