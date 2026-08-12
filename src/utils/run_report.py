"""
Run reports — the human-readable face of the audit spine
The Virtual Biotech

Renders a run's MANIFEST/provenance/claims into two artifacts:

  * ``README.md``  — a map of the run, dropped in the run directory.
  * ``audit.html`` — one self-contained file (no external assets, no CDN) that
    can be attached to an email and opened by a reviewer with no environment.

Both answer the three questions from reviewer comment R2.5, in this order:

  1. *How is this organised?*        → artifacts grouped by producing agent
  2. *What supports which claim?*    → claim → evidence, and artifact → cited_by
  3. *How did the analysis flow?*    → delegation timeline and dispatch order

Design note: agent identity in the timeline is carried by a direct row label, not
by colour. The palette is validated (see the dataviz palette reference) but colour
is reinforcement only, so the chart still reads in greyscale, in print, and for
colour-vision-deficient readers.

Usage::

    from src.utils.run_report import render_readme, render_audit_html

    (run_dir / 'README.md').write_text(render_readme(manifest, prov, claims))
    (run_dir / 'audit.html').write_text(render_audit_html(manifest, prov, claims))
"""

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.utils.claims import EVIDENCE_STATUS_LABELS, evidence_status
from src.utils.plan_runner import reconcile, render_plan_md
from src.utils.run_manifest import CSO_DIR

# ── Palette (validated; see dataviz references/palette.md) ───────────

#: Categorical slots in fixed order — assigned to agents by first appearance,
#: never cycled. Beyond this many agents, later ones fall back to muted ink.
_SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
_SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]

KIND_ORDER = ["report", "figure", "table", "data", "code", "log", "other"]

KIND_LABEL = {
    "report": "Reports", "figure": "Figures", "table": "Tables",
    "data": "Data", "code": "Code", "log": "Logs", "other": "Other",
}


def _agent_colors(agents: list[str]) -> dict[str, tuple[str, str]]:
    """{agent: (light, dark)} assigned in fixed order of first appearance."""
    out = {}
    for i, a in enumerate(agents):
        if i < len(_SERIES_LIGHT):
            out[a] = (_SERIES_LIGHT[i], _SERIES_DARK[i])
        else:
            out[a] = ("#898781", "#898781")   # muted; row label carries identity
    return out


# ── Small helpers ────────────────────────────────────────────────────

def _human_bytes(n: int) -> str:
    step = 1024.0
    val = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step:
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} PB"


def _parse(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return None


def _short_time(ts) -> str:
    d = _parse(ts)
    return d.strftime("%H:%M:%S") if d else ""


def _agent_label(agent: str) -> str:
    if agent == CSO_DIR:
        return "CSO (orchestrator)"
    return (agent or "unknown").replace("-", " ").title()


def _collect(manifest, provenance, claim_set):
    """Shared derived values so README and HTML never disagree."""
    by_agent = manifest.artifacts_by_agent()
    order = [a for a in manifest.data.get("agents", []) if a in by_agent]
    order += [a for a in sorted(by_agent) if a not in order and a != CSO_DIR]
    if CSO_DIR in by_agent and CSO_DIR not in order:
        order.append(CSO_DIR)
    if provenance is not None:
        for a in provenance.specialist_types():
            if a not in order:
                order.append(a)
    return by_agent, order


# ── Markdown ─────────────────────────────────────────────────────────

def render_readme(manifest, provenance=None, claim_set=None,
                  notes: Optional[list[str]] = None) -> str:
    """A human map of the run, written to ``README.md`` in the run directory."""
    d = manifest.data
    s = manifest.summary()
    by_agent, order = _collect(manifest, provenance, claim_set)
    L: list[str] = []

    L.append(f"# Run `{d['run_id']}`")
    L.append("")
    if d.get("query"):
        L.append(f"> {d['query']}")
        L.append("")

    started, finished = _parse(d.get("created")), _parse(d.get("completed"))
    dur = f"{(finished - started).total_seconds() / 60:.1f} min" if started and finished else "—"
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| **Status** | {d.get('status', 'unknown')} |")
    L.append(f"| **Started** | {d.get('created', '—')} |")
    L.append(f"| **Duration** | {dur} |")
    L.append(f"| **Specialists** | {len([a for a in order if a != CSO_DIR])} |")
    L.append(f"| **Artifacts** | {s['n_artifacts']} ({_human_bytes(s['total_bytes'])}) |")
    if provenance is not None:
        ps = provenance.summary()
        L.append(f"| **Tool calls** | {ps['n_tool_calls']} "
                 f"({ps['n_attributed_to_specialist']} by specialists, "
                 f"{ps['n_cso_tool_calls']} by the CSO) |")
        if ps["n_tool_errors"]:
            L.append(f"| **Tool errors** | {ps['n_tool_errors']} |")
    if claim_set is not None and claim_set.claims:
        cs = claim_set.stats()
        L.append(f"| **Claims** | {cs['n_claims']} "
                 f"({cs['n_verified_evidence']}/{cs['n_evidence']} evidence links verified) |")
    L.append("")

    if notes:
        L.append("## Notes on this report")
        L.append("")
        for n in notes:
            L.append(f"- {n}")
        L.append("")

    misplaced = d.get("misplaced_files") or []
    if misplaced:
        L.append("> **Misplaced files.** These were written into directories the run "
                 "harness owns, where analysis output does not belong. They are not "
                 "recorded as artifacts and nothing can cite them; whichever agent "
                 "produced them should have written under `work/<agent>/`.")
        L.append("")
        for p in misplaced[:20]:
            L.append(f"> - `{p}`")
        if len(misplaced) > 20:
            L.append(f"> - … and {len(misplaced) - 20} more")
        L.append("")

    # ── The declared plan, and whether the run followed it ───────
    plan = d.get("plan")
    if plan:
        rep = reconcile(plan, d.get("execution"), manifest)
        L.append(render_plan_md(plan, rep))

    # ── 1. How the analysis flowed ───────────────────────────────
    if provenance is not None and provenance.agents:
        L.append("## How the analysis flowed")
        L.append("")
        L.append("The CSO dispatched these specialists, in this order:")
        L.append("")
        L.append("| # | Specialist | Started | Duration | Messages | Cost |")
        L.append("|---|---|---|---|---|---|")
        agents = sorted(provenance.agents.values(), key=lambda a: a.get("start") or "")
        for i, a in enumerate(agents, 1):
            cost = (a.get("cost") or {}).get("cost_usd")
            L.append(
                f"| {i} | `{a['agent_type']}` | {_short_time(a.get('start'))} "
                f"| {a.get('duration_s') or '—'}s | {a.get('n_messages') or 0} "
                f"| {'$%.3f' % cost if cost else '—'} |"
            )
        L.append("")
        L.append("<details><summary>Delegation prompts (what the CSO asked each specialist)</summary>")
        L.append("")
        for a in agents:
            p = (a.get("delegation_prompt") or "").strip()
            if not p:
                continue
            L.append(f"**`{a['agent_type']}`**")
            L.append("")
            L.append("```")
            L.append(p[:1500] + ("\n… [truncated]" if len(p) > 1500 else ""))
            L.append("```")
            L.append("")
        L.append("</details>")
        L.append("")

    # ── 2. What each agent produced ──────────────────────────────
    L.append("## What each agent produced")
    L.append("")
    if not by_agent:
        L.append("*No artifacts recorded.*")
        L.append("")
    for agent in order:
        arts = by_agent.get(agent, [])
        if not arts:
            continue
        L.append(f"### {_agent_label(agent)}  ·  {len(arts)} artifacts")
        L.append("")
        L.append("| File | Kind | Size | Produced by | Cited by |")
        L.append("|---|---|---|---|---|")
        for e in arts:
            origin = e.get("created_by") or (
                f"`{e['tool_use_id'][:14]}…`" if e.get("tool_use_id") else "—"
            )
            cited = ", ".join(e.get("cited_by") or []) or "—"
            L.append(f"| `{e['path']}` | {e['kind']} | {_human_bytes(e['bytes'])} "
                     f"| {origin} | {cited} |")
        L.append("")

    # ── 3. Claims and their evidence ─────────────────────────────
    if claim_set is not None and claim_set.claims:
        L.append("## Claims and their evidence")
        L.append("")
        for c in claim_set.claims:
            mark = "✓" if c["n_verified"] else "!"
            L.append(f"### [{mark}] `{c['id']}` — {c['text']}")
            L.append("")
            L.append(f"*{_agent_label(c.get('agent') or 'unattributed')} · "
                     f"confidence: {c['confidence']}*")
            L.append("")
            for ev in c["evidence"]:
                L.append(f"- {_evidence_line_md(ev)}")
            L.append("")
        bad = claim_set.stats()["claims_without_verified_evidence"]
        if bad:
            L.append(f"> **{len(bad)} claim(s) have no independently verified evidence:** "
                     f"{', '.join(bad)}")
            L.append("")

    # ── 4. Reproducing this run ──────────────────────────────────
    L.append("## Reproducing this run")
    L.append("")
    L.append("```bash")
    L.append(f"./run.sh verify {d['run_id']}   # re-run the analysis code, check every hash")
    L.append(f"./run.sh replay {d['run_id']}   # re-run the same turns into a new run dir")
    L.append("```")
    L.append("")
    L.append("`verify` is exact: it re-executes the analysis scripts under `work/*/code/` and "
             "fails on any byte difference. `replay` re-runs the agents against the same pinned "
             "models and prompts — LLM sampling means it is comparable, not bit-identical.")
    L.append("")

    cfg = d.get("config") or {}
    if cfg:
        L.append("<details><summary>Pinned configuration</summary>")
        L.append("")
        L.append("```json")
        L.append(json.dumps(cfg, indent=2, default=str)[:4000])
        L.append("```")
        L.append("")
        L.append("</details>")
        L.append("")

    L.append("## Directory layout")
    L.append("")
    L.append("```")
    L.append("MANIFEST.json   every artifact: hash, producing agent, producing tool call")
    L.append("inputs/         the query, the analysis plan, the pinned config")
    L.append("work/<agent>/   per-agent outputs — code/, data/, results/")
    L.append("evidence/       claims.json (claim → evidence), provenance.json (tool → agent)")
    L.append("logs/           trace.jsonl, cost_report.json, transcript.md")
    L.append("report/         the CSO's final synthesis")
    L.append("```")
    L.append("")
    L.append(f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
             f"by `src/utils/run_report.py`.*")
    return "\n".join(L)


def _evidence_line_md(ev: dict) -> str:
    mark = EVIDENCE_STATUS_LABELS[evidence_status(ev)]
    k = ev["kind"]
    if k == "tool_call":
        body = f"**tool call** `{ev.get('tool_name') or ev['tool_use_id']}`"
        if ev.get("agent"):
            body += f" by `{ev['agent']}`"
    elif k == "citation":
        ref = ev.get("pmid") or ev.get("doi") or ev.get("url")
        body = f"**citation** {ref}"
    else:
        body = f"**{k}** `{ev.get('path')}`"
        if ev.get("line"):
            body += f" (line {ev['line']})"
    if ev.get("note"):
        body += f" — {ev['note']}"
    return f"{body}  ·  _{mark}_"


# ── HTML ─────────────────────────────────────────────────────────────

def render_audit_html(manifest, provenance=None, claim_set=None,
                      notes: Optional[list[str]] = None) -> str:
    """One self-contained HTML file. No external requests; opens anywhere."""
    d = manifest.data
    s = manifest.summary()
    by_agent, order = _collect(manifest, provenance, claim_set)
    colors = _agent_colors([a for a in order if a != CSO_DIR])
    colors[CSO_DIR] = ("#898781", "#898781")

    ps = provenance.summary() if provenance is not None else {}
    cs = claim_set.stats() if (claim_set and claim_set.claims) else {}

    started, finished = _parse(d.get("created")), _parse(d.get("completed"))
    dur = f"{(finished - started).total_seconds() / 60:.1f} min" if started and finished else "—"

    tiles = [
        ("Specialists", str(len([a for a in order if a != CSO_DIR])), ""),
        ("Artifacts", str(s["n_artifacts"]), _human_bytes(s["total_bytes"])),
    ]
    if ps:
        tiles.append(("Tool calls", str(ps["n_tool_calls"]),
                      f"{ps['n_attributed_to_specialist']} by specialists"))
        if ps.get("n_tool_errors"):
            tiles.append(("Tool errors", str(ps["n_tool_errors"]), "recovered in-run"))
    if cs:
        tiles.append(("Claims", str(cs["n_claims"]),
                      f"{cs['n_verified_evidence']}/{cs['n_evidence']} links verified"))
    tiles.append(("Duration", dur, ""))

    P: list[str] = []
    P.append(_HTML_HEAD.replace("__TITLE__", html.escape(f"Audit · {d['run_id']}")))

    # Sections are numbered as they are emitted; a run with no claims must not
    # leave a gap where section 3 would have been.
    _n = [0]

    def sec(title: str) -> str:
        _n[0] += 1
        return f'<section><h2>{_n[0]} · {html.escape(title)}</h2>' 

    # Header
    P.append('<header class="hdr">')
    P.append('<div class="eyebrow">Virtual Biotech · run audit</div>')
    P.append(f'<h1>{html.escape(d.get("query") or d["run_id"])}</h1>')
    P.append(f'<div class="sub"><code>{html.escape(d["run_id"])}</code> · '
             f'{html.escape(str(d.get("created") or ""))} · '
             f'status <strong>{html.escape(str(d.get("status") or "unknown"))}</strong></div>')
    P.append("</header>")

    # Stat tiles
    P.append('<div class="tiles">')
    for label, value, sub in tiles:
        P.append('<div class="tile">'
                 f'<div class="tile-l">{html.escape(label)}</div>'
                 f'<div class="tile-v">{html.escape(value)}</div>'
                 f'<div class="tile-s">{html.escape(sub)}</div></div>')
    P.append("</div>")

    if notes:
        P.append('<div class="note"><strong>About this report.</strong><ul>')
        for n in notes:
            P.append(f"<li>{html.escape(n)}</li>")
        P.append("</ul></div>")

    misplaced = d.get("misplaced_files") or []
    if misplaced:
        P.append('<div class="note warnbox"><strong>Misplaced files.</strong> '
                 'Written into directories the run harness owns, where analysis '
                 'output does not belong. They are not recorded as artifacts and '
                 'nothing can cite them.<ul>')
        for p in misplaced[:20]:
            P.append(f"<li><code>{html.escape(p)}</code></li>")
        if len(misplaced) > 20:
            P.append(f"<li>… and {len(misplaced) - 20} more</li>")
        P.append("</ul></div>")

    # The declared plan, and whether the run followed it
    plan = d.get("plan")
    if plan:
        rep = reconcile(plan, d.get("execution"), manifest)
        P.append(sec("The analysis plan"))
        P.append('<p class="lede">The sequence the CSO declared <em>before</em> dispatching '
                 'any specialist, and how the run compared against it.</p>')
        if plan.get("goal"):
            P.append(f'<p><strong>Goal:</strong> {html.escape(plan["goal"])}</p>')
        P.append('<div class="xscroll"><table><thead><tr><th>Step</th><th>Specialist</th>'
                 '<th>Depends on</th><th>Task</th></tr></thead><tbody>')
        for s in plan.get("steps", []):
            P.append(
                f'<tr><td><code>{html.escape(s["id"])}</code></td>'
                f'<td><code>{html.escape(s["agent"])}</code></td>'
                f'<td>{html.escape(", ".join(s["depends_on"]) or "—")}</td>'
                f'<td class="prompt">{html.escape(s.get("task") or "")}</td></tr>'
            )
        P.append("</tbody></table></div>")

        dev = rep["deviations"]
        box = "note" if not dev else "note warnbox"
        P.append(f'<div class="{box}"><strong>Planned vs actual.</strong> '
                 f'{html.escape(rep["summary"])}')
        if dev:
            P.append("<ul>")
            for v in dev:
                P.append(f'<li><em>{html.escape(v["kind"])}</em> — '
                         f'{html.escape(v["detail"])}</li>')
            P.append("</ul><p>Deviations are expected when a specialist\'s findings change "
                     "what should happen next. They are recorded so the actual sequence is "
                     "auditable, not to flag an error.</p>")
        P.append("</div>")
        P.append("</section>")

    # 1. Flow
    if provenance is not None and provenance.agents:
        P.append(sec("How the analysis flowed"))
        P.append('<p class="lede">Each bar is one specialist\'s execution span, in dispatch '
                 'order. Overlapping bars ran concurrently.</p>')
        P.append(_timeline_svg(provenance, colors))
        P.append(_delegation_table(provenance, colors))
        P.append("</section>")

    # 2. Artifacts by agent
    P.append(sec("What each agent produced"))
    P.append('<p class="lede">Every file this run wrote, grouped by the agent that produced '
             'it and hashed at the time of writing.</p>')
    if not by_agent:
        P.append('<p class="empty">No artifacts recorded for this run.</p>')
    for agent in order:
        arts = by_agent.get(agent, [])
        if not arts:
            continue
        lc, dc = colors.get(agent, ("#898781", "#898781"))
        P.append(f'<div class="agent" style="--ac:{lc};--acd:{dc}">')
        P.append(f'<h3><span class="swatch"></span>{html.escape(_agent_label(agent))}'
                 f'<span class="count">{len(arts)} artifacts</span></h3>')
        for kind in KIND_ORDER:
            group = [e for e in arts if e["kind"] == kind]
            if not group:
                continue
            P.append(f'<div class="kind">{html.escape(KIND_LABEL[kind])}</div>')
            P.append('<div class="xscroll"><table><thead><tr><th>File</th><th>Size</th>'
                     '<th>Produced by</th><th>SHA-256</th><th>Cited by</th></tr></thead><tbody>')
            for e in group:
                origin = html.escape(str(e.get("created_by") or "")) or (
                    f'<code class="dim">{html.escape((e.get("tool_use_id") or "—")[:16])}</code>'
                )
                cited = " ".join(
                    f'<a class="cref" href="#claim-{html.escape(c)}">{html.escape(c)}</a>'
                    for c in (e.get("cited_by") or [])
                ) or '<span class="dim">—</span>'
                P.append(
                    f'<tr><td><code>{html.escape(e["path"])}</code></td>'
                    f'<td class="num">{_human_bytes(e["bytes"])}</td>'
                    f'<td>{origin}</td>'
                    f'<td><code class="dim" title="{html.escape(e.get("sha256") or "")}">'
                    f'{html.escape((e.get("sha256") or "")[:12])}…</code></td>'
                    f'<td>{cited}</td></tr>'
                )
            P.append("</tbody></table></div>")
        P.append("</div>")
    P.append("</section>")

    # 3. Claims
    if claim_set is not None and claim_set.claims:
        P.append(sec("Claims and their evidence"))
        P.append('<p class="lede">Each claim from the final synthesis, with the artifacts and '
                 'tool calls behind it. <strong>Verified</strong> means the pointer was resolved '
                 'against this run\'s manifest or trace — not that the science was checked.</p>')
        for c in claim_set.claims:
            ok = c["n_verified"] > 0
            P.append(f'<div class="claim {"ok" if ok else "warn"}" id="claim-{html.escape(c["id"])}">')
            P.append(f'<div class="claim-h"><span class="cid">{html.escape(c["id"])}</span>'
                     f'<span class="ctext">{html.escape(c["text"])}</span></div>')
            P.append(f'<div class="claim-m">{html.escape(_agent_label(c.get("agent") or "unattributed"))}'
                     f' · confidence {html.escape(c["confidence"])}</div>')
            P.append('<ul class="ev">')
            for ev in c["evidence"]:
                P.append(f"<li>{_evidence_line_html(ev)}</li>")
            P.append("</ul></div>")
        bad = cs.get("claims_without_verified_evidence") or []
        if bad:
            P.append(f'<div class="note warnbox"><strong>{len(bad)} claim(s) carry no '
                     f'independently verified evidence:</strong> {html.escape(", ".join(bad))}. '
                     f'Treat these as unsupported until the underlying artifact is located.</div>')
        P.append("</section>")

    # 4. Provenance table
    if provenance is not None and provenance.calls:
        P.append(sec("Full provenance"))
        P.append('<p class="lede">Every tool call in the run, attributed to the agent that made '
                 'it. Reconstructed from the execution trace, not from any agent\'s self-report.</p>')
        P.append(_tool_table(provenance, colors))
        P.append("</section>")

    # 5. Reproducing
    P.append(sec("Reproducing this run"))
    P.append(f'<pre class="cmd">./run.sh verify {html.escape(d["run_id"])}'
             '   <span class="dim"># re-run the analysis code, check every hash</span>\n'
             f'./run.sh replay {html.escape(d["run_id"])}'
             '   <span class="dim"># re-run the same turns into a new run dir</span></pre>')
    P.append('<p class="lede"><code>verify</code> is exact — it re-executes the analysis scripts '
             'under <code>work/*/code/</code> and fails on any byte difference. '
             '<code>replay</code> re-runs the agents against the same pinned models and prompts; '
             'because LLM sampling is stochastic the result is comparable, not bit-identical. '
             'We state the difference rather than implying end-to-end determinism.</p>')
    cfg = d.get("config") or {}
    if cfg:
        P.append('<details><summary>Pinned configuration</summary>'
                 f'<pre>{html.escape(json.dumps(cfg, indent=2, default=str)[:6000])}</pre></details>')
    P.append("</section>")

    P.append(f'<footer>Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} · '
             'src/utils/run_report.py · self-contained, no external assets</footer>')
    P.append("</div></body>")
    return "\n".join(P)


def _evidence_line_html(ev: dict) -> str:
    status = evidence_status(ev)
    badge = (f'<span class="vb {status}">'
             f'{html.escape(EVIDENCE_STATUS_LABELS[status])}</span>')
    k = ev["kind"]
    if k == "tool_call":
        body = (f'<span class="ek">tool call</span> '
                f'<code>{html.escape(str(ev.get("tool_name") or ev.get("tool_use_id")))}</code>')
        if ev.get("agent"):
            body += f' <span class="dim">by {html.escape(ev["agent"])}</span>'
        if ev.get("ts"):
            body += f' <span class="dim">{html.escape(_short_time(ev["ts"]))}</span>'
    elif k == "citation":
        ref = ev.get("pmid") or ev.get("doi") or ev.get("url")
        body = f'<span class="ek">citation</span> {html.escape(str(ref))}'
    else:
        body = f'<span class="ek">{html.escape(k)}</span> <code>{html.escape(str(ev.get("path")))}</code>'
        if ev.get("line"):
            body += f' <span class="dim">line {html.escape(str(ev["line"]))}</span>'
    if ev.get("note"):
        body += f' <span class="note-inline">— {html.escape(str(ev["note"]))}</span>'
    return f"{body} {badge}"


def _timeline_svg(provenance, colors) -> str:
    """Horizontal spans, one row per specialist, each directly labelled.

    Direct labels are what let colour stay decorative: the chart is readable in
    greyscale and satisfies the relief rule for the light-mode palette.
    """
    agents = sorted(provenance.agents.values(), key=lambda a: a.get("start") or "")
    spans = []
    for a in agents:
        st, en = _parse(a.get("start")), _parse(a.get("end"))
        if st:
            spans.append((a["agent_type"], st, en or st))
    if not spans:
        return ""

    t0 = min(s[1] for s in spans)
    t1 = max(s[2] for s in spans)
    total = max((t1 - t0).total_seconds(), 1.0)

    row_h, gap, pad_l, pad_t, width = 30, 8, 210, 26, 900
    plot_w = width - pad_l - 24
    height = pad_t + len(spans) * (row_h + gap) + 26

    out = [f'<svg class="tl" viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="Specialist execution timeline">']

    # Recessive gridlines with time ticks.
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        x = pad_l + plot_w * frac
        out.append(f'<line class="grid" x1="{x:.1f}" y1="{pad_t - 8}" '
                   f'x2="{x:.1f}" y2="{height - 24}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{height - 8}" '
                   f'text-anchor="middle">+{total * frac / 60:.1f}m</text>')

    for i, (name, st, en) in enumerate(spans):
        y = pad_t + i * (row_h + gap)
        x = pad_l + plot_w * ((st - t0).total_seconds() / total)
        w = max(plot_w * ((en - st).total_seconds() / total), 3)
        lc, dc = colors.get(name, ("#898781", "#898781"))
        secs = (en - st).total_seconds()
        out.append(f'<text class="rowlab" x="{pad_l - 12}" y="{y + row_h / 2 + 4}" '
                   f'text-anchor="end">{html.escape(name)}</text>')
        out.append(f'<rect class="bar" x="{x:.1f}" y="{y}" width="{w:.1f}" height="{row_h}" '
                   f'rx="4" style="--bar:{lc};--bard:{dc}"><title>{html.escape(name)}: '
                   f'{secs:.0f}s</title></rect>')
        lx, anchor, cls = (x + w + 8, "start", "durlab")
        if lx > pad_l + plot_w - 40:
            lx, anchor, cls = (x + w - 8, "end", "durlab inbar")
        out.append(f'<text class="{cls}" x="{lx:.1f}" y="{y + row_h / 2 + 4}" '
                   f'text-anchor="{anchor}">{secs:.0f}s</text>')

    out.append("</svg>")
    return "".join(out)


def _delegation_table(provenance, colors) -> str:
    agents = sorted(provenance.agents.values(), key=lambda a: a.get("start") or "")
    counts = provenance.tool_counts()
    rows = ['<div class="xscroll"><table class="deleg">'
            '<thead><tr><th>#</th><th>Specialist</th><th>Started</th>'
            '<th>Duration</th><th>Tool calls</th><th>Cost</th>'
            '<th>Task given by the CSO</th></tr></thead><tbody>']
    for i, a in enumerate(agents, 1):
        cost = (a.get("cost") or {}).get("cost_usd")
        prompt = (a.get("delegation_prompt") or "").strip().replace("\n", " ")
        short = prompt[:150] + ("…" if len(prompt) > 150 else "")
        lc, dc = colors.get(a["agent_type"], ("#898781", "#898781"))
        rows.append(
            f'<tr><td class="num">{i}</td>'
            f'<td><span class="swatch sm" style="--ac:{lc};--acd:{dc}"></span>'
            f'<code>{html.escape(a["agent_type"])}</code></td>'
            f'<td class="num">{_short_time(a.get("start"))}</td>'
            f'<td class="num">{html.escape(str(a.get("duration_s") or "—"))}s</td>'
            f'<td class="num">{sum(counts.get(a["agent_type"], {}).values())}</td>'
            f'<td class="num">{"$%.3f" % cost if cost else "—"}</td>'
            f'<td class="prompt" title="{html.escape(prompt[:1200])}">{html.escape(short)}</td></tr>'
        )
    rows.append("</tbody></table></div>")
    return "".join(rows)


def _tool_table(provenance, colors, limit: int = 400) -> str:
    calls = sorted(provenance.calls.values(), key=lambda r: r.get("started_at") or "")
    shown, hidden = calls[:limit], max(0, len(calls) - limit)
    rows = ['<div class="scroll"><table class="tools"><thead><tr><th>Time</th><th>Agent</th>'
            '<th>Tool</th><th>ms</th><th>Wrote</th></tr></thead><tbody>']
    for r in shown:
        lc, dc = colors.get(r["agent"], ("#898781", "#898781"))
        wrote = ", ".join(Path(p).name for p in r.get("files_written") or []) or ""
        cls = ' class="err"' if r.get("is_error") else ""
        err_badge = " <span class='vb no'>error</span>" if r.get("is_error") else ""
        rows.append(
            f'<tr{cls}><td class="num">{_short_time(r.get("started_at"))}</td>'
            f'<td><span class="swatch sm" style="--ac:{lc};--acd:{dc}"></span>'
            f'<code>{html.escape(r["agent"])}</code></td>'
            f'<td><code>{html.escape(str(r.get("tool_name") or ""))}</code>'
            f'{err_badge}</td>'
            f'<td class="num">{html.escape(str(r.get("duration_ms") or "—"))}</td>'
            f'<td><code class="dim">{html.escape(wrote)}</code></td></tr>'
        )
    rows.append("</tbody></table></div>")
    if hidden:
        # Never truncate silently — a hidden row is a hidden gap in the audit.
        rows.append(f'<p class="dim">Showing the first {limit} of {len(calls)} tool calls; '
                    f'{hidden} more are in <code>evidence/provenance.json</code>.</p>')
    return "".join(rows)


_HTML_HEAD = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{color-scheme:light dark;
    --surface:#fcfcfb; --plane:#f9f9f7; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --rule:#c3c2b7; --border:rgba(11,11,11,.10);
    --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;}
  @media (prefers-color-scheme:dark){:root:not([data-theme=light]){
    --surface:#1a1a19; --plane:#0d0d0d; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --rule:#383835; --border:rgba(255,255,255,.10);}}
  :root[data-theme=dark]{--surface:#1a1a19; --plane:#0d0d0d; --ink:#fff; --ink2:#c3c2b7;
    --grid:#2c2c2a; --rule:#383835; --border:rgba(255,255,255,.10);}
  *{box-sizing:border-box}
  body{margin:0;background:var(--plane);color:var(--ink);
    font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;}
  .wrap{max-width:1080px;margin:0 auto;padding:32px 20px 72px}
  code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em}
  .dim{color:var(--muted)}
  .hdr{border-bottom:1px solid var(--rule);padding-bottom:20px;margin-bottom:24px}
  .eyebrow{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
  h1{font-size:26px;line-height:1.25;margin:8px 0 6px;font-weight:650}
  .sub{color:var(--ink2);font-size:13px}
  h2{font-size:19px;margin:40px 0 4px;font-weight:620}
  h3{font-size:15px;margin:22px 0 8px;font-weight:600;display:flex;align-items:center;gap:8px}
  .lede{color:var(--ink2);font-size:13.5px;margin:6px 0 16px;max-width:74ch}
  section{border-top:1px solid var(--grid);margin-top:8px}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0}
  .tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
  .tile-l{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
  .tile-v{font-size:26px;font-weight:640;margin:2px 0 1px}
  .tile-s{font-size:11.5px;color:var(--ink2)}
  .note{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--muted);
    border-radius:8px;padding:12px 16px;margin:18px 0;font-size:13.5px;color:var(--ink2)}
  .note ul{margin:6px 0 0;padding-left:18px}
  .warnbox{border-left-color:var(--warn)}
  .empty{color:var(--muted);font-style:italic}
  table{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0 16px}
  th{text-align:left;font-weight:600;color:var(--ink2);font-size:11px;text-transform:uppercase;
    letter-spacing:.06em;border-bottom:1px solid var(--rule);padding:6px 8px}
  td{padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
  td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  tr.err td{background:color-mix(in srgb,var(--crit) 7%,transparent)}
  /* Wide content scrolls inside its own box; the page body never scrolls sideways. */
  .scroll{overflow-x:auto;max-height:520px;overflow-y:auto;border:1px solid var(--border);
    border-radius:8px}
  .scroll table{margin:0}
  .scroll th{position:sticky;top:0;background:var(--surface);z-index:1}
  .xscroll{overflow-x:auto;max-width:100%}
  .xscroll table{min-width:560px}
  td code{overflow-wrap:anywhere}
  .prompt{color:var(--ink2);min-width:22ch;max-width:40ch;overflow-wrap:anywhere}
  .agent{margin:18px 0 26px}
  .swatch{width:10px;height:10px;border-radius:3px;background:var(--ac);flex:none;display:inline-block}
  .swatch.sm{width:8px;height:8px;margin-right:6px;vertical-align:baseline}
  @media (prefers-color-scheme:dark){:root:not([data-theme=light]) .swatch{background:var(--acd)}}
  :root[data-theme=dark] .swatch{background:var(--acd)}
  .count{margin-left:auto;font-weight:400;font-size:12px;color:var(--muted)}
  .kind{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
    margin:12px 0 2px}
  .tl{width:100%;height:auto;background:var(--surface);border:1px solid var(--border);
    border-radius:10px;padding:6px 0;margin:8px 0 18px}
  .tl .grid{stroke:var(--grid);stroke-width:1}
  .tl .tick,.tl .rowlab,.tl .durlab{font:11px system-ui,sans-serif;fill:var(--muted)}
  .tl .rowlab{fill:var(--ink2);font-size:11.5px}
  .tl .bar{fill:var(--bar);stroke:var(--surface);stroke-width:2}
  @media (prefers-color-scheme:dark){:root:not([data-theme=light]) .tl .bar{fill:var(--bard)}}
  :root[data-theme=dark] .tl .bar{fill:var(--bard)}
  .durlab.inbar{fill:var(--surface);font-weight:600}
  .claim{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--good);
    border-radius:8px;padding:12px 16px;margin:10px 0}
  .claim.warn{border-left-color:var(--warn)}
  .claim-h{display:flex;gap:10px;align-items:baseline}
  .cid{font:600 11px ui-monospace,monospace;color:var(--muted);flex:none}
  .ctext{font-weight:550}
  .claim-m{font-size:12px;color:var(--muted);margin:3px 0 8px}
  ul.ev{margin:0;padding-left:18px;font-size:13px}
  ul.ev li{margin:3px 0}
  .ek{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
    margin-right:4px}
  .vb{font-size:10.5px;padding:1px 6px;border-radius:20px;margin-left:6px;white-space:nowrap}
  .vb.ok,.vb.verified{color:var(--good);
    border:1px solid color-mix(in srgb,var(--good) 45%,transparent)}
  /* An external citation is neutral, not a fault — only `unresolved` is red. */
  .vb.external{color:var(--muted);border:1px solid color-mix(in srgb,var(--muted) 45%,transparent)}
  .vb.no,.vb.unresolved{color:var(--crit);
    border:1px solid color-mix(in srgb,var(--crit) 45%,transparent)}
  .note-inline{color:var(--ink2)}
  .cref{color:inherit;text-decoration:none;border-bottom:1px dotted var(--muted);
    font:600 11px ui-monospace,monospace}
  pre{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px;
    overflow-x:auto;font-size:12.5px;margin:8px 0}
  pre.cmd{font-size:13px}
  details{margin:10px 0}
  summary{cursor:pointer;font-size:13px;color:var(--ink2);padding:4px 0}
  footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--grid);
    font-size:12px;color:var(--muted)}
</style>
<body><div class="wrap">"""
