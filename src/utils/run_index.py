"""
Run index — a browsable list of every run
The Virtual Biotech

Reviewer comment R2.5 noted it was hard to tell how a run's outputs were
organised. Half of that is the layout inside one run (src/utils/run_manifest.py);
the other half is being able to see the runs at all. This builds
``runs/INDEX.md`` and a JSON sidecar listing every run with its query, date,
cost, agents, artifact count and claim count — so "which run produced that
figure?" is answerable without opening directories one at a time.

Usage::

    from src.utils.run_index import update_index, load_index
    update_index(RUNS_DIR)                 # rebuild runs/INDEX.md + INDEX.json
    rows = load_index(RUNS_DIR)            # for the Past Runs tab
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def scan_runs(runs_root) -> list[dict[str, Any]]:
    """Summarise every run under *runs_root*, newest first.

    Reads only MANIFEST.json and claims.json — never the artifacts themselves —
    so this stays fast even when runs hold large datasets.
    """
    runs_root = Path(runs_root)
    rows: list[dict[str, Any]] = []
    if not runs_root.exists():
        return rows

    for run_dir in sorted(runs_root.iterdir(), reverse=True):
        mpath = run_dir / "MANIFEST.json"
        if not run_dir.is_dir() or not mpath.exists():
            continue
        try:
            m = json.loads(mpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        arts = m.get("artifacts", {}) or {}
        cfg = m.get("config", {}) or {}

        n_claims, n_verified = 0, 0
        cpath = run_dir / "evidence" / "claims.json"
        if cpath.exists():
            try:
                cd = json.loads(cpath.read_text())
                stats = cd.get("stats") or {}
                n_claims = stats.get("n_claims", len(cd.get("claims", [])))
                n_verified = stats.get("n_verified_evidence", 0)
            except (json.JSONDecodeError, OSError):
                pass

        rows.append({
            "run_id": m.get("run_id", run_dir.name),
            "path": str(run_dir),
            "query": m.get("query") or "",
            "status": m.get("status", "unknown"),
            "created": m.get("created"),
            "completed": m.get("completed"),
            "agents": [a for a in (m.get("agents") or []) if a != "_cso"],
            "n_artifacts": len(arts),
            "total_bytes": sum(e.get("bytes", 0) for e in arts.values()),
            "n_claims": n_claims,
            "n_verified_evidence": n_verified,
            "cost_usd": cfg.get("total_cost_usd"),
            "specialist_model": cfg.get("specialist_model"),
            "has_report": (run_dir / "README.md").exists(),
            "has_audit": (run_dir / "audit.html").exists(),
        })

    rows.sort(key=lambda r: r.get("created") or "", reverse=True)
    return rows


def render_index_md(rows: list[dict[str, Any]]) -> str:
    L = ["# Runs", ""]
    if not rows:
        L += ["*No runs recorded yet.*", ""]
        return "\n".join(L)

    total_cost = sum(r["cost_usd"] or 0 for r in rows)
    L.append(f"{len(rows)} runs · {sum(r['n_artifacts'] for r in rows)} artifacts · "
             f"{sum(r['n_claims'] for r in rows)} claims"
             + (f" · ${total_cost:.2f}" if total_cost else ""))
    L += ["", "| Run | Query | Started | Specialists | Artifacts | Claims | Cost |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        q = (r["query"] or "—").replace("|", "\\|").replace("\n", " ")
        if len(q) > 70:
            q = q[:70] + "…"
        started = (r.get("created") or "")[:16].replace("T", " ")
        cost = f"${r['cost_usd']:.2f}" if r.get("cost_usd") else "—"
        # The link must be the run's actual directory name, not the run_id
        # recorded inside its MANIFEST.json — those differ whenever a run
        # directory has been copied or renamed (e.g. examples/ snapshots),
        # and linking on the manifest field alone silently 404s.
        dirname = Path(r["path"]).name
        L.append(
            f"| [`{r['run_id']}`]({dirname}/README.md) | {q} | {started} "
            f"| {len(r['agents'])} | {r['n_artifacts']} | {r['n_claims']} | {cost} |"
        )
    L += ["", f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
              f"by `src/utils/run_index.py`.*"]
    return "\n".join(L)


def update_index(runs_root) -> list[dict[str, Any]]:
    """Rebuild ``INDEX.md`` and ``INDEX.json`` under *runs_root*."""
    runs_root = Path(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    rows = scan_runs(runs_root)
    (runs_root / "INDEX.md").write_text(render_index_md(rows))
    (runs_root / "INDEX.json").write_text(json.dumps(rows, indent=2, default=str))
    return rows


def load_index(runs_root, rebuild_if_missing: bool = True) -> list[dict[str, Any]]:
    """Read the cached index, rebuilding it when absent."""
    p = Path(runs_root) / "INDEX.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return update_index(runs_root) if rebuild_if_missing else []


def render_index_html(rows: list[dict[str, Any]], limit: int = 50) -> str:
    """Compact table for the Past Runs tab in the Gradio UI."""
    import html as _h

    if not rows:
        return ('<p style="color:#898781;font-style:italic">No runs recorded yet. '
                'Send a query to create one.</p>')

    out = ['<table class="runs-table"><thead><tr><th>Run</th><th>Query</th>'
           '<th>Started</th><th>Agents</th><th>Files</th><th>Claims</th>'
           '<th>Cost</th><th>Report</th></tr></thead><tbody>']
    for r in rows[:limit]:
        q = (r["query"] or "—").replace("\n", " ")
        if len(q) > 80:
            q = q[:80] + "…"
        cost = f"${r['cost_usd']:.2f}" if r.get("cost_usd") else "—"
        claims = (f'{r["n_claims"]}' if r["n_claims"]
                  else '<span style="color:#898781">—</span>')
        # Gradio serves anything under an allowed_paths root at /file=<abs path>;
        # RUNS_DIR is one, so this needs no server route of its own. audit.html
        # is self-contained (no external assets), so this link is also what a
        # reviewer can download/save independent of the running app.
        if r.get("has_audit"):
            audit_url = "/file=" + str(Path(r["path"]) / "audit.html")
            report_link = (f'<a href="{_h.escape(audit_url)}" target="_blank" '
                           f'rel="noopener">📄 audit.html</a>')
        else:
            report_link = '<span style="color:#898781">—</span>'
        out.append(
            f'<tr><td><code title="{_h.escape(r["path"])}">'
            f'{_h.escape(r["run_id"][:28])}</code></td>'
            f'<td>{_h.escape(q)}</td>'
            f'<td>{_h.escape((r.get("created") or "")[:16].replace("T", " "))}</td>'
            f'<td>{len(r["agents"])}</td><td>{r["n_artifacts"]}</td>'
            f'<td>{claims}</td><td>{cost}</td><td>{report_link}</td></tr>'
        )
    out.append("</tbody></table>")
    if len(rows) > limit:
        out.append(f'<p style="color:#898781;font-size:12px">Showing the {limit} most '
                   f'recent of {len(rows)} runs; the full list is in '
                   f'<code>runs/INDEX.md</code>.</p>')
    return "".join(out)
