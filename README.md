# archon-reaper

A [Claude Code](https://claude.com/claude-code) skill that reports on how an [Archon](https://github.com/coleam00/Archon)
installation is performing: how many runs completed, failed or were cancelled, why runs failed, how long they take,
which workflows ran how often, and what it all cost. The result is a single, styled HTML page.

See [`examples/sample-report.html`](examples/sample-report.html) for the layout. It is built from a synthetic database;
open it in a browser.

## What the report shows

| Section | Contents |
|---|---|
| Summary | Runs, completed, failed, cancelled, success rate (with and without cancelled runs), spend, outcome bar |
| Takeaways | Headline, observations and recommendations written by Claude from the numbers |
| Workflows | Runs per workflow, outcome mix, success rate, average and median time, spend, cost per completed run |
| Archon versions | Runs, outcomes, time and spend per Archon version; dated upgrades; success by workflow and version |
| What went wrong | Failures grouped by cause with example errors and a diagnosis; nodes that fail most; cancellation reasons |
| How long runs take | Average, median, p90, hands-on time vs total time, slowest runs, slowest nodes |
| Spend and tokens | Total, per completed run, share wasted on failed or cancelled runs, by workflow and model, cache share |
| Gates, resumes and loops | Approval decisions, typical wait, resumed runs, loop iterations |
| Activity over time | Runs per day or week, split by outcome, with version changes marked |
| Projects and tools | Runs per project, tool call counts and failure rates |
| Installation health | Database size, workspaces, unfinished runs, installed Archon version and whether a newer one exists |

The Archon versions section appears when the runs cover more than one version or the install has changed version.

## Install

Requires Python 3.9 or newer (standard library only) and an Archon install with its database at `~/.archon/archon.db`.

```powershell
.\install.ps1            # Windows: links the skill into ~/.claude/skills (use -Copy to copy, -Force to replace)
```

```sh
./install.sh             # macOS / Linux: same, with --copy, --force and --uninstall
```

Then ask Claude Code something like "make an Archon report" or "why do my Archon runs fail?".

## Use without Claude

`generate.py` makes the same page with no commentary:

```sh
python skills/archon-reaper/scripts/generate.py --since 30d --open
python skills/archon-reaper/scripts/generate.py --project my-repo --out my-repo.html
```

| Option | Meaning |
|---|---|
| `--since` | Window start: `30d`, `12h`, or `YYYY-MM-DD` |
| `--project` | Case-insensitive substring of the project (codebase) name |
| `--db` | Database path (default `~/.archon/archon.db`) |
| `--narrative` | A `narrative.json` to include (see `examples/narrative.example.json`) |
| `--out`, `--open` | Output file, and open it in a browser when done |

The two steps are also available on their own: `collect_stats.py` writes JSON, and `render_report.py` turns that JSON
into HTML (add `--standalone` for a page that opens from disk).

## How it works

```
~/.archon/archon.db ──read-only──▶ collect_stats.py ──▶ stats.json ──▶ render_report.py ──▶ report.html
                                                            │                  ▲
                                                            ▼                  │
                                                  Claude reads it and   narrative.json
                                                  writes the narrative ─────────┘
```

- The database is opened with SQLite `mode=ro`. The collector also reads `install.json`, `server.json`,
  `update-check.json`, the names of the `server/<version>` folders and the files in `logs/`, all beside the database.
  Nothing in the Archon install is changed (a test checks that the database is byte-identical afterwards).
- The collector verifies the tables and columns it needs and stops with a clear message if Archon changes its schema.
- `hook_activity` events are skipped: they are about 87% of all stored events and carry nothing a report needs.
- Claude's part is the narrative: it reads the example errors in each failure group and writes the diagnosis,
  observations and recommendations. `skills/archon-reaper/SKILL.md` holds those instructions.

### Definitions

- **Success rate** is completed runs divided by finished runs. Archon can mark a run `completed` without recording an
  `outcome`, so the report also shows how many completed runs are verified `succeeded`.
- **Total time** is start to finish. **Hands-on time** removes approval waits and the gap between a failure and a resume.
- **Failure groups** come from keyword rules on the final error text (`classify_failure()` in `collect_stats.py`).
  The first failed node's `failure_kind` is only a fallback, because a run can fail one way, be resumed, and fail another.
- **Archon version** is not stored on a run, so it is inferred. `version_observations()` in `collect_stats.py` collects
  dated sightings of a version (the schema-version table, `install.json`, `server.json`, the `server/<version>` folders
  and Archon's structured log lines), and a run counts under the version in use when it started. A change is dated at
  the first sighting of the new version; the report says how many runs started in the gap before it. An install with
  no such evidence reports its runs under "unknown". A build that reports the same number as a release cannot be told
  apart from that release.
- **Spend** is the cost the provider reported per agent node. On a subscription plan it is an API-equivalent figure, not
  a bill. Loop-group nodes are excluded because they repeat their children; script nodes report no cost.

## Extending

- **New metric:** add a key to the JSON in `collect_stats.py`, then a section function in `render_report.py`.
- **New failure category:** add a rule to `classify_failure()` and a test in `tests/test_report.py`.
- **More detail than the database holds:** per-run JSONL logs are under `~/.archon/workspaces/<owner>/<repo>/logs/`.

## Development

```sh
python -m unittest discover -s tests -v      # standard library only
python examples/make_sample.py               # regenerate examples/sample-report.html
```

The tests build a small synthetic Archon home (`tests/fixture.py`): a database that mirrors the tables the collector
reads, plus the log and install files that date an upgrade. No real run data is needed or stored in the repo.

```
skills/archon-reaper/
  SKILL.md                 instructions Claude follows
  scripts/collect_stats.py database -> stats.json
  scripts/render_report.py stats.json (+ narrative) -> HTML
  scripts/generate.py      both steps, no Claude needed
tests/                     unit tests and the synthetic database builder
examples/                  sample report and an example narrative
install.ps1, install.sh    link or copy the skill into ~/.claude/skills
```

## License

[MIT](LICENSE)
