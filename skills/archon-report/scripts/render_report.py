#!/usr/bin/env python3
"""Render collect_stats.py JSON into a self-contained HTML report.

Usage:
    python -I render_report.py stats.json report.html [--narrative narrative.json] [--standalone]

The output is an Artifact-ready page fragment (title, style, content; no <html>/<head>/<body>).
Pass --standalone to wrap it in a full document so it opens correctly from disk.

narrative.json (optional; written by Claude after reading stats.json):
{
  "headline":       "One or two sentences that sum up the period.",
  "observations":   ["3-6 short findings, most important first"],
  "recommendations":["0-4 concrete next steps"],
  "failure_notes":  {"<exact category name from stats>": "One-two sentence diagnosis."}
}
"""
import argparse
import json
from datetime import datetime
from html import escape as _e
from pathlib import Path


def e(x):
    return _e("" if x is None else str(x), quote=True)


# ---------- formatting ----------
def dur(s):
    if s is None:
        return "–"
    s = float(s)
    if s < 1:
        return "<1s"
    if s < 90:
        return "%ds" % round(s)
    m = s / 60
    if m < 90:
        return "%dm" % round(m)
    h = m / 60
    if h < 48:
        total_min = int(round(m))
        return "%dh %02dm" % (total_min // 60, total_min % 60)
    return "%.1fd" % (h / 24)


def money(v):
    if v is None:
        return "–"
    if v >= 100:
        return "$%s" % format(round(v), ",")
    return "$%.2f" % v


def pc(v, digits=0):
    return "–" if v is None else ("%." + str(digits) + "f%%") % (v * 100)


def num(v):
    return format(int(v), ",")


def tokens(v):
    v = float(v or 0)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= div:
            return "%.1f%s" % (v / div, unit)
    return "%d" % v


def dlabel(iso, with_year=False):
    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    return d.strftime("%b ") + str(d.day) + ((", %d" % d.year) if with_year else "")


def stacked(ok, bad, cancel, other=0, cls="seg"):
    tot = ok + bad + cancel + other
    if not tot:
        return '<div class="stack"></div>'
    parts = []
    for v, k, label in ((ok, "ok", "completed"), (bad, "fail", "failed"), (cancel, "cancel", "cancelled"), (other, "other", "active")):
        if v:
            parts.append('<i class="%s %s" style="width:%.3f%%" title="%d %s"></i>' % (cls, k, v / tot * 100, v, label))
    return '<div class="stack">%s</div>' % "".join(parts)


def hbar(value, maximum, label, sub=""):
    w = (value / maximum * 100) if maximum else 0
    return ('<div class="hb"><span class="hb-l">%s</span><span class="hb-t"><i style="width:%.2f%%"></i></span>'
            '<span class="hb-v">%s</span></div>' % (label, max(w, 0.8 if value else 0), sub))


def wf_name(full):
    if ":" in full:
        pack, name = full.rsplit(":", 1)
        return '<b>%s</b><small>%s</small>' % (e(name), e(pack))
    return "<b>%s</b>" % e(full)


# ---------- sections ----------
def kpis(d):
    s, sp = d["summary"], d["spend"]
    cells = [
        ("Runs", num(s["total"]), "since %s" % dlabel(d["meta"]["first_run"], True) if d["meta"]["first_run"] else "", ""),
        ("Completed", num(s["completed"]), "%s have a verified outcome" % num(s["completed_outcome_verified"]), "ok"),
        ("Failed", num(s["failed"]), "%s of runs" % pc(s["failed"] / s["total"]) if s["total"] else "", "fail"),
        ("Cancelled", num(s["cancelled"]), "%s after an error" % num(s["cancelled_after_error"]) if s["cancelled_after_error"] else "no errors attached", "cancel"),
        ("Success rate", pc(s["success_rate"]), "%s excluding cancelled" % pc(s["success_rate_excl_cancelled"]), ""),
        ("Spend", money(sp["total"]), "%s on failed or cancelled runs" % money(sp["wasted"]) if sp["runs_with_cost"] else "no cost data", ""),
    ]
    out = []
    for label, val, sub, k in cells:
        out.append('<div class="kpi %s"><span class="kpi-l">%s</span><span class="kpi-v">%s</span><span class="kpi-s">%s</span></div>' % (k, e(label), e(val), e(sub)))
    bar = stacked(s["completed"], s["failed"], s["cancelled"], s["active"])
    legend = ('<div class="legend"><span><i class="sw ok"></i>Completed</span><span><i class="sw fail"></i>Failed</span>'
              '<span><i class="sw cancel"></i>Cancelled</span></div>')
    return '<section class="kpis">%s</section><div class="wide-stack">%s%s</div>' % ("".join('%s' % c for c in out), bar, legend)


def takeaways(n):
    if not n:
        return ""
    parts = []
    if n.get("observations"):
        parts.append('<div><h3>What stands out</h3><ul>%s</ul></div>' % "".join("<li>%s</li>" % e(x) for x in n["observations"]))
    if n.get("recommendations"):
        parts.append('<div><h3>Worth doing next</h3><ul>%s</ul></div>' % "".join("<li>%s</li>" % e(x) for x in n["recommendations"]))
    if not parts:
        return ""
    return '<section class="takeaways">%s</section>' % "".join(parts)


def workflows(d):
    rows = []
    for w in d["workflows"]:
        rows.append(
            "<tr><td class='name'>%s</td><td class='n'>%s</td><td class='bar'>%s</td>"
            "<td class='n ok'>%s</td><td class='n fail'>%s</td><td class='n cancel'>%s</td><td class='n'>%s</td>"
            "<td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td></tr>" % (
                wf_name(w["name"]), w["runs"], stacked(w["completed"], w["failed"], w["cancelled"], w["active"]),
                w["completed"], w["failed"], w["cancelled"], pc(w["success_rate"]),
                dur(w["wall"]["mean"]), dur(w["wall"]["median"]), money(w["cost"]) if w["cost"] else "–",
                money(w["cost_per_completed"]) if w["cost_per_completed"] else "–"))
    return ('<section id="workflows"><h2>Workflows</h2><p class="lede">How often each workflow ran and how those runs ended. '
            'Duration columns use completed runs only, measured start to finish.</p>'
            '<div class="scroll"><table><thead><tr><th>Workflow</th><th class="n">Runs</th><th>Outcome mix</th>'
            '<th class="n">Done</th><th class="n">Failed</th><th class="n">Cancel</th><th class="n">Success</th>'
            '<th class="n">Avg time</th><th class="n">Median</th><th class="n">Spend</th><th class="n">Per done run</th></tr></thead>'
            '<tbody>%s</tbody></table></div></section>' % "".join(rows))


def failures(d, n):
    f, s = d["failures"], d["summary"]
    notes = (n or {}).get("failure_notes", {})
    cards = []
    for c in f["categories"]:
        ex = "".join(
            "<li><span class='meta'>%s · %s%s · run %s</span><code>%s</code></li>" % (
                e(x["workflow"]), dlabel(x["when"]), (" · node " + e(x["node"])) if x["node"] else "", e(x["run"]), e(x["message"] or "no message recorded"))
            for x in c["examples"])
        chips = "".join("<span class='chip'>%s <b>%d</b></span>" % (e(k), v) for k, v in c["workflows"].items())
        nodes = "".join("<span class='chip mono'>%s <b>%d</b></span>" % (e(k), v) for k, v in c["nodes"].items())
        note = '<p class="note">%s</p>' % e(notes[c["category"]]) if notes.get(c["category"]) else ""
        cards.append(
            '<article class="fcard"><header><h3>%s</h3><span class="fcount">%d<small> run%s · %s of failures</small></span></header>%s'
            '<div class="chips">%s%s</div><details><summary>Example errors</summary><ul class="ex">%s</ul></details></article>' % (
                e(c["category"]), c["count"], "" if c["count"] == 1 else "s", pc(c["count"] / s["failed"]) if s["failed"] else "",
                note, chips, nodes, ex))
    cc = f["cancel_categories"]
    cancel_html = ""
    if cc:
        cancel_html = ('<div class="panel"><h3>Cancelled runs</h3><ul class="plain">%s</ul>' % "".join(
            "<li><span>%s</span><b>%d</b></li>" % (e(k), v) for k, v in sorted(cc.items(), key=lambda kv: -kv[1])))
        if f["cancel_error_examples"]:
            cancel_html += ("<p class='hint'>Runs cancelled after an error behave like failures. Example: <code>%s</code></p>" %
                            e(f["cancel_error_examples"][0]["message"] or ""))
        cancel_html += "</div>"
    nodes_html = ""
    if f["failing_nodes"]:
        rows = "".join("<tr><td>%s</td><td class='mono'>%s</td><td class='n'>%d of %d</td><td class='n'>%s</td><td>%s</td></tr>" % (
            e(x["workflow"]), e(x["node"]), x["failed"], x["attempts"], pc(x["fail_rate"]),
            e(", ".join("%s ×%d" % (k, v) for k, v in x["kinds"].items()))) for x in f["failing_nodes"])
        nodes_html = ('<div class="panel"><h3>Nodes that fail most</h3><div class="scroll"><table><thead><tr><th>Workflow</th><th>Node</th>'
                      '<th class="n">Failed attempts</th><th class="n">Rate</th><th>Cause</th></tr></thead><tbody>%s</tbody></table></div></div>' % rows)
    if not cards:
        cards = ['<p class="empty">No failed runs in this window.</p>']
    return ('<section id="failures"><h2>What went wrong</h2><p class="lede">%d failed runs, grouped by cause. Cancellations are listed separately.</p>'
            '<div class="fgrid">%s</div><div class="two">%s%s</div></section>' % (s["failed"], "".join(cards), nodes_html, cancel_html))


def durations(d):
    du = d["durations"]
    c = du["completed"]
    cards = [
        ("Average", dur(c["wall"]["mean"]), "completed runs, start to finish"),
        ("Median", dur(c["wall"]["median"]), "half of runs finish faster"),
        ("Slowest 10%", dur(c["wall"]["p90"]), "p90 of completed runs"),
        ("Hands-on time", dur(c["active"]["mean"]), "average, excluding approval waits and pauses"),
    ]
    cardhtml = "".join('<div class="mini"><span class="kpi-l">%s</span><span class="mv">%s</span><span class="kpi-s">%s</span></div>' % (a, b, e(x)) for a, b, x in cards)
    ws = [w for w in d["workflows"] if w["wall"]["mean"]]
    mx = max([w["wall"]["mean"] for w in ws], default=0)
    bars = "".join(hbar(w["wall"]["mean"], mx, wf_name(w["name"]), "%s · %d runs" % (dur(w["wall"]["mean"]), w["wall"]["n"])) for w in sorted(ws, key=lambda w: -w["wall"]["mean"]))
    other = []
    for k, label in (("failed", "Failed runs"), ("cancelled", "Cancelled runs")):
        if du[k]["wall"]["n"]:
            other.append("<li><span>%s</span><b>%s avg</b></li>" % (label, dur(du[k]["wall"]["mean"])))
    slow = "".join("<tr><td class='mono'>%s</td><td>%s</td><td><span class='pill %s'>%s</span></td><td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td></tr>" % (
        e(r["run"]), e(r["workflow"]), {"completed": "ok", "failed": "fail", "cancelled": "cancel"}.get(r["status"], ""), e(r["status"]),
        dur(r["wall_s"]), dur(r["active_s"]), money(r["cost"]) if r["cost"] else "–") for r in d["slowest_runs"])
    nodes = "".join("<tr><td>%s</td><td class='mono'>%s</td><td class='n'>%s</td><td class='n'>%d</td><td class='n'>%s</td></tr>" % (
        e(n["workflow"]), e(n["node"]), dur(n["mean_s"]), n["runs"], dur(n["total_s"])) for n in d["slow_nodes"])
    return ('<section id="duration"><h2>How long runs take</h2><div class="minis">%s</div><div class="two">'
            '<div class="panel"><h3>Average time by workflow</h3>%s<ul class="plain tight">%s</ul></div>'
            '<div class="panel"><h3>Slowest runs</h3><div class="scroll"><table><thead><tr><th>Run</th><th>Workflow</th><th>Status</th>'
            '<th class="n">Total</th><th class="n">Hands-on</th><th class="n">Spend</th></tr></thead><tbody>%s</tbody></table></div></div></div>'
            '<div class="panel"><h3>Where the time goes (by node)</h3><div class="scroll"><table><thead><tr><th>Workflow</th><th>Node</th>'
            '<th class="n">Avg</th><th class="n">Runs</th><th class="n">Total</th></tr></thead><tbody>%s</tbody></table></div></div></section>' % (
                cardhtml, bars or '<p class="empty">No completed runs.</p>', "".join(other), slow, nodes))


def spend(d):
    sp = d["spend"]
    if not sp["runs_with_cost"]:
        return '<section id="spend"><h2>Spend</h2><p class="empty">No cost data recorded in this window.</p></section>'
    cards = [
        ("Total spend", money(sp["total"]), "%d runs reported cost" % sp["runs_with_cost"]),
        ("Per completed run", money(sp["cost_per_completed"]), "average"),
        ("Spent on failed or cancelled", money(sp["wasted"]), "%s of total" % pc(sp["wasted_share"])),
        ("Cache reads", pc(sp["cache_read_share"]), "share of input-side tokens"),
    ]
    cardhtml = "".join('<div class="mini"><span class="kpi-l">%s</span><span class="mv">%s</span><span class="kpi-s">%s</span></div>' % (a, b, e(x)) for a, b, x in cards)
    mx = max([m["cost"] for m in sp["by_model"]], default=0)
    models = "".join(hbar(m["cost"], mx, "<b class='mono'>%s</b>" % e(m["model"]), "%s · %s tokens · %d node%s" % (money(m["cost"]), tokens(m["tokens"]), m["nodes"], "" if m["nodes"] == 1 else "s")) for m in sp["by_model"])
    wfs = sorted([w for w in d["workflows"] if w["cost"]], key=lambda w: -w["cost"])
    mxw = max([w["cost"] for w in wfs], default=0)
    wbars = "".join(hbar(w["cost"], mxw, wf_name(w["name"]), "%s · %s wasted" % (money(w["cost"]), money(w["wasted_cost"]))) for w in wfs)
    top = "".join("<tr><td class='mono'>%s</td><td>%s</td><td><span class='pill %s'>%s</span></td><td class='n'>%s</td></tr>" % (
        e(r["run"]), e(r["workflow"]), {"completed": "ok", "failed": "fail", "cancelled": "cancel"}.get(r["status"], ""), e(r["status"]), money(r["cost"])) for r in sp["top_runs"])
    t = sp["tokens"]
    return ('<section id="spend"><h2>Spend and tokens</h2><div class="minis">%s</div><div class="two">'
            '<div class="panel"><h3>By workflow</h3>%s</div><div class="panel"><h3>By model</h3>%s'
            '<p class="hint">Tokens: %s input, %s cache read, %s cache write, %s output.</p></div></div>'
            '<div class="panel"><h3>Most expensive runs</h3><div class="scroll"><table><thead><tr><th>Run</th><th>Workflow</th><th>Status</th><th class="n">Spend</th></tr></thead><tbody>%s</tbody></table></div></div>'
            '<p class="hint">Costs are what the provider reported per agent node. On a subscription plan they are an API-equivalent figure, not a bill. '
            'Script and bash nodes report no cost, so %s nodes are not counted.</p></section>' % (
                cardhtml, wbars, models, tokens(t.get("input")), tokens(t.get("cacheRead")), tokens(t.get("cacheWrite")), tokens(t.get("output")),
                top, num(sp["nodes_without_cost_data"])))


def human(d):
    h = d["human"]
    s = d["summary"]
    dec = h["decisions"]
    cards = [
        ("Approval decisions", num(h["approvals"]), "%s approved, %s rejected" % (dec.get("approved", 0), dec.get("rejected", 0))),
        ("Typical wait", dur(h["idle_median_s"]), "median pause on runs with a gate or a resume"),
        ("Total paused time", dur(h["idle_total_s"]), "across all runs"),
        ("Resumed runs", num(s["resumed_runs"]), "%s resume%s in total" % (s["resumes"], "" if s["resumes"] == 1 else "s")),
        ("Loop iterations", num(h["loop_iterations"]), ("avg %.1f per loop, max %d" % (h["loop_avg"], h["loop_max"])) if h["loops"] else "no loops ran"),
    ]
    return ('<section id="human"><h2>Gates, resumes and loops</h2><div class="minis five">%s</div></section>' % "".join(
        '<div class="mini"><span class="kpi-l">%s</span><span class="mv">%s</span><span class="kpi-s">%s</span></div>' % (a, b, e(x)) for a, b, x in cards))


def trend(d):
    rows = d["trend"]["rows"]
    if not rows:
        return ""
    mx = max(r["completed"] + r["failed"] + r["cancelled"] + r["other"] for r in rows) or 1
    cols = []
    n = len(rows)
    every = 1 if n <= 14 else (2 if n <= 28 else 4)
    for i, r in enumerate(rows):
        tot = r["completed"] + r["failed"] + r["cancelled"] + r["other"]
        segs = "".join('<i class="%s" style="height:%.2f%%"></i>' % (k, r[key] / mx * 100) for k, key in (("ok", "completed"), ("fail", "failed"), ("cancel", "cancelled"), ("other", "other")) if r[key])
        lab = dlabel(r["label"]) if (i % every == 0) else ""
        tip = "%s: %d run%s (%d completed, %d failed, %d cancelled)" % (dlabel(r["label"]), tot, "" if tot == 1 else "s", r["completed"], r["failed"], r["cancelled"])
        cols.append('<div class="col" title="%s"><span class="cn">%s</span><div class="cs">%s</div><span class="cl">%s</span></div>' % (e(tip), tot or "", segs, e(lab)))
    return ('<section id="trend"><h2>Activity over time</h2><p class="lede">Runs started per %s, split by how they ended. Tallest bar: %d.</p>'
            '<div class="scroll"><div class="trend" style="min-width:%dpx">%s</div></div></section>' % (
                "week" if d["trend"]["bucket"] == "week" else "day", mx, n * 22, "".join(cols)))


def projects(d):
    if len(d["projects"]) < 1:
        return ""
    rows = "".join("<tr><td><b>%s</b></td><td class='n'>%d</td><td class='bar'>%s</td><td class='n'>%s</td></tr>" % (
        e(p["name"]), p["runs"], stacked(p["completed"], p["failed"], p["cancelled"]), money(p["cost"]) if p["cost"] else "–") for p in d["projects"])
    tl = d["tools"]
    trows = "".join("<tr><td class='mono'>%s</td><td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td></tr>" % (
        e(t["tool"]), num(t["calls"]), pc(t["fail_rate"], 1), dur(t["ms"] / t["calls"] / 1000) if t["calls"] else "–") for t in tl)
    tools_html = ('<div class="panel"><h3>Tool calls</h3><div class="scroll"><table><thead><tr><th>Tool</th><th class="n">Calls</th><th class="n">Failed</th><th class="n">Avg time</th></tr></thead><tbody>%s</tbody></table></div></div>' % trows) if tl else ""
    return ('<section id="projects"><h2>Projects and tools</h2><div class="two"><div class="panel"><h3>Runs by project</h3><div class="scroll"><table><thead><tr><th>Project</th><th class="n">Runs</th><th>Outcome mix</th><th class="n">Spend</th></tr></thead><tbody>%s</tbody></table></div></div>%s</div></section>' % (rows, tools_html))


def health(d):
    h = d["health"]
    items = []
    items.append(("Database", "%s MB" % h.get("db_mb"), "%s events across %s runs" % (num(h.get("events_total", 0)), num(d["summary"]["total"]))))
    if h.get("events_total"):
        items.append(("Hook activity", pc(h["hook_activity_events"] / h["events_total"]), "of all stored events; the largest share of the database"))
    env = h.get("environments") or {}
    if env:
        items.append(("Workspaces", "%s active" % env.get("active", 0), ", ".join("%s %s" % (v, k) for k, v in env.items() if k != "active") or "none retired"))
    if "log_files" in h:
        items.append(("Run logs", "%s files" % h["log_files"], "%s MB on disk" % h["log_mb"]))
    un = h.get("unfinished_runs") or []
    items.append(("Unfinished runs", str(len(un)), ("; ".join("%s %s idle %sh" % (u["workflow"], u["status"], u["idle_hours"]) for u in un[:3])) if un else "nothing stuck"))
    inst = h.get("install") or {}
    if inst.get("version"):
        items.append(("Archon version", "v%s" % inst["version"], "%s project%s registered" % (h.get("codebases", "?"), "" if h.get("codebases") == 1 else "s")))
    cells = "".join('<div class="mini%s"><span class="kpi-l">%s</span><span class="mv sm">%s</span><span class="kpi-s">%s</span></div>' % (" warn" if (a == "Unfinished runs" and un) else "", e(a), e(b), e(c)) for a, b, c in items)
    return '<section id="health"><h2>Installation health</h2><div class="minis">%s</div></section>' % cells


def notes(d):
    m = d["meta"]
    win = "all runs on record"
    if m.get("since"):
        win = "runs since %s" % dlabel(m["since"], True)
    if m.get("project_filter"):
        win += ", project matching “%s”" % m["project_filter"]
    return ('<footer><h3>How to read this</h3><ul><li><b>Success rate</b> is completed runs divided by finished runs (completed, failed, cancelled). '
            'A run marked completed can still have an unrecorded outcome; the Completed tile says how many are verified.</li>'
            '<li><b>Total time</b> runs from start to finish and includes time a run sat waiting for approval or between a failure and a resume. '
            '<b>Hands-on time</b> subtracts those waits.</li>'
            '<li><b>Failure groups</b> come from keyword rules on the recorded error text, so “Other” means no rule matched.</li></ul>'
            '<p class="stamp">Window: %s. Generated %s from <span class="mono">%s</span>, opened read-only.</p></footer>' % (
                e(win), e(datetime.fromisoformat(m["generated_at"]).strftime("%b %d, %Y %H:%M UTC")), e(m["db_path"])))


CSS = """
:root{
  /* Layout: ledger-style report. Summary strip first, then one section per question. */
  --bg:#f2f4f6; --surface:#ffffff; --surface-2:#f7f9fa; --ink:#15202b; --muted:#566676; --line:#d8e0e7; --line-2:#e8edf1;
  --accent:#2b5c8a; --accent-soft:#e3edf6;
  --ok:#23785a; --ok-soft:#dff1ea; --fail:#c2402a; --fail-soft:#fae6e1; --cancel:#a97c1c; --cancel-soft:#f6ecd4; --other:#8896a4;
  --font-body:"Schibsted Grotesk",system-ui,-apple-system,"Segoe UI",sans-serif; --font-mono:"JetBrains Mono",ui-monospace,"SF Mono",Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0e151d; --surface:#16212d; --surface-2:#1b2836; --ink:#e5ecf2; --muted:#93a4b5; --line:#2a3b4c; --line-2:#223140;
  --accent:#7fb2e0; --accent-soft:#1f3347;
  --ok:#4bb88f; --ok-soft:#173a30; --fail:#ef7d68; --fail-soft:#432722; --cancel:#d6ab4a; --cancel-soft:#3b3017; --other:#6f8193; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0e151d; --surface:#16212d; --surface-2:#1b2836; --ink:#e5ecf2; --muted:#93a4b5; --line:#2a3b4c; --line-2:#223140;
  --accent:#7fb2e0; --accent-soft:#1f3347;
  --ok:#4bb88f; --ok-soft:#173a30; --fail:#ef7d68; --fail-soft:#432722; --cancel:#d6ab4a; --cancel-soft:#3b3017; --other:#6f8193; color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:var(--font-body);font-size:15px;line-height:1.5;margin:0}
.page{max-width:1160px;margin:0 auto;padding-block:32px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:flex;flex-direction:column;gap:28px}
.mono,code{font-family:var(--font-mono);font-size:.86em}
h1{font-size:clamp(28px,4.2vw,42px);line-height:1.08;margin:0;letter-spacing:-.02em;font-weight:700;text-wrap:balance}
h2{font-size:21px;margin:0 0 6px;letter-spacing:-.01em;font-weight:650;text-wrap:balance}
h3{font-size:13px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600}
.eyebrow{font-family:var(--font-mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
.head{display:flex;flex-direction:column;gap:10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.head .sub{color:var(--muted);font-family:var(--font-mono);font-size:12.5px}
.headline{font-size:18px;max-width:68ch;margin:6px 0 10px;text-wrap:pretty}
.lede{color:var(--muted);max-width:70ch;margin:0 0 14px}
section{min-width:0}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 16px;display:flex;flex-direction:column;gap:2px;min-width:0}
.kpi-l{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600}
.kpi-v{font-size:34px;font-weight:700;letter-spacing:-.02em;line-height:1.1;font-variant-numeric:tabular-nums}
.kpi-s{font-size:12.5px;color:var(--muted)}
.kpi.ok .kpi-v{color:var(--ok)}.kpi.fail .kpi-v{color:var(--fail)}.kpi.cancel .kpi-v{color:var(--cancel)}
.wide-stack{display:flex;flex-direction:column;gap:8px;margin-top:-10px}
.stack{display:flex;height:10px;border-radius:2px;overflow:hidden;background:var(--line-2);gap:2px;min-width:90px}
.wide-stack .stack{height:14px}
.stack i{display:block;height:100%}
.ok{--c:var(--ok)}.fail{--c:var(--fail)}.cancel{--c:var(--cancel)}.other{--c:var(--other)}
.stack i.ok,.sw.ok{background:var(--ok)}.stack i.fail,.sw.fail{background:var(--fail)}.stack i.cancel,.sw.cancel{background:var(--cancel)}.stack i.other{background:var(--other)}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--muted)}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.takeaways{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.takeaways>div{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:16px 18px}
.takeaways ul{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:8px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:16px 18px;min-width:0}
.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:12px}
.two:first-child{margin-top:0}
section>.panel{margin-top:12px}
.minis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:12px}
.minis.five{grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:0}
.mini{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:12px 14px;display:flex;flex-direction:column;gap:2px;min-width:0}
.mini.warn{border-color:var(--cancel);background:var(--cancel-soft)}
.mv{font-size:26px;font-weight:650;letter-spacing:-.01em;font-variant-numeric:tabular-nums;line-height:1.2}
.mv.sm{font-size:20px}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);text-align:left;font-weight:600;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--line-2);vertical-align:middle}
tbody tr:last-child td{border-bottom:0}
th.n,td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.name b{display:block;font-weight:600}td.name small{display:block;color:var(--muted);font-size:12px;font-family:var(--font-mono)}
td.bar{min-width:120px;width:18%}
td.n.ok{color:var(--ok)}td.n.fail{color:var(--fail)}td.n.cancel{color:var(--cancel)}
.pill{font-size:12px;padding:2px 8px;border-radius:99px;font-weight:600;background:var(--line-2);color:var(--muted)}
.pill.ok{background:var(--ok-soft);color:var(--ok)}.pill.fail{background:var(--fail-soft);color:var(--fail)}.pill.cancel{background:var(--cancel-soft);color:var(--cancel)}
.hb{display:grid;grid-template-columns:minmax(110px,32%) 1fr auto;gap:10px;align-items:center;padding:5px 0}
.hb-l b{font-weight:600}.hb-l small{display:block;color:var(--muted);font-size:12px;font-family:var(--font-mono)}
.hb-t{height:10px;background:var(--line-2);border-radius:2px;overflow:hidden;display:block}
.hb-t i{display:block;height:100%;background:var(--accent);border-radius:2px}
.hb-v{font-size:12.5px;color:var(--muted);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.plain{list-style:none;margin:0;padding:0}
.plain li{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--line-2)}
.plain li:last-child{border-bottom:0}.plain.tight{margin-top:10px}
.fgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.fcard{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:16px 18px;min-width:0;display:flex;flex-direction:column;gap:10px}
.fcard header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.fcard h3{margin:0;color:var(--fail);font-size:14px;text-transform:none;letter-spacing:0;font-weight:650}
.fcount{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}.fcount small{font-size:12.5px;font-weight:400;color:var(--muted)}
.note{margin:0;padding:8px 12px;background:var(--surface-2);border-left:3px solid var(--accent);border-radius:2px;text-wrap:pretty}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12.5px;padding:2px 9px;border-radius:99px;background:var(--accent-soft);color:var(--ink)}
.chip b{font-variant-numeric:tabular-nums;margin-left:3px}.chip.mono{background:var(--line-2)}
details summary{cursor:pointer;color:var(--accent);font-size:13px;font-weight:600}
details summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ex{list-style:none;margin:10px 0 0;padding:0;display:flex;flex-direction:column;gap:10px}
.ex .meta{display:block;font-size:12px;color:var(--muted);margin-bottom:2px}
.ex code{display:block;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--surface-2);border:1px solid var(--line-2);border-radius:4px;padding:8px 10px;font-size:12px}
.hint{color:var(--muted);font-size:13px;margin:12px 0 0;max-width:80ch}.hint code{overflow-wrap:anywhere}
.empty{color:var(--muted)}
.trend{display:flex;align-items:flex-end;gap:4px;height:210px;padding-top:6px}
.col{flex:1 1 0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;min-width:16px}
.cs{width:100%;max-width:26px;height:150px;display:flex;flex-direction:column-reverse;gap:1px}
.cs i{display:block;width:100%}.cs i.ok{background:var(--ok)}.cs i.fail{background:var(--fail)}.cs i.cancel{background:var(--cancel)}.cs i.other{background:var(--other)}
.cn{font-size:11px;color:var(--muted);height:16px;font-variant-numeric:tabular-nums}
.cl{font-size:11px;color:var(--muted);height:30px;margin-top:6px;white-space:nowrap;font-family:var(--font-mono)}
footer{border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:13.5px}
footer ul{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:6px;max-width:80ch}
.stamp{margin:14px 0 0;font-size:12.5px}
a{color:var(--accent)}
@media (max-width:860px){
  .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
  .takeaways,.two,.fgrid{grid-template-columns:minmax(0,1fr)}
  .minis,.minis.five{grid-template-columns:repeat(2,minmax(0,1fr))}
  .hb{grid-template-columns:1fr auto}.hb-t{grid-column:1/-1;order:3}
}
@media (max-width:420px){.kpi-v{font-size:28px}.minis,.minis.five{grid-template-columns:minmax(0,1fr)}}
"""


def render(d, n):
    m = d["meta"]
    first = dlabel(m["first_run"], True) if m["first_run"] else "no runs"
    last = dlabel(m["last_run"], True) if m["last_run"] else ""
    span = "%s to %s · %d day%s" % (first, last, m["span_days"], "" if m["span_days"] == 1 else "s") if m["first_run"] else "no runs in this window"
    head = ('<header class="head"><span class="eyebrow">Archon · run report</span><h1>Archon Run Report</h1>%s<span class="sub">%s</span></header>' % (
        ('<p class="headline">%s</p>' % e(n["headline"])) if n and n.get("headline") else "", e(span)))
    body = [head]
    if d["summary"]["total"] == 0:
        body.append('<p class="empty">No runs matched this window. Try a wider --since or no --project filter.</p>')
    else:
        body += [kpis(d), takeaways(n), workflows(d), failures(d, n), durations(d), spend(d), human(d), trend(d), projects(d), health(d)]
    body.append(notes(d))
    return ('<title>Archon Run Report</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Schibsted+Grotesk:wght@400;500;600;700&display=swap">\n'
            '<style>%s</style>\n<div class="page">%s</div>\n' % (CSS, "\n".join(body)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stats")
    ap.add_argument("out")
    ap.add_argument("--narrative")
    ap.add_argument("--standalone", action="store_true")
    a = ap.parse_args()
    d = json.loads(Path(a.stats).read_text(encoding="utf-8"))
    n = json.loads(Path(a.narrative).read_text(encoding="utf-8")) if a.narrative else None
    html = render(d, n)
    if a.standalone:
        html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">%s</body></html>' % html.replace("<div class=\"page\">", "</head><body><div class=\"page\">", 1)
    Path(a.out).write_text(html, encoding="utf-8")
    print("Wrote %s (%d KB)" % (a.out, len(html) // 1024))


if __name__ == "__main__":
    main()
