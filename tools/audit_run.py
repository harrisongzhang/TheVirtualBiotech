#!/usr/bin/env python3
"""
audit_run.py — reconstruct an audit trail for a session that never had one.

Reviewer comment R2.5 was about sessions already on disk: a flat pile of files
with no indication of which agent produced what, what supported which claim, or
how the analysis flowed. Those runs cannot be re-executed to fix that. But they
were traced, and the trace is enough.

This reads any existing session directory — `web_workspace/<uuid>/`,
`casestudy_workspace/<uuid>/`, `cost_tracking_workspace/<name>_<ts>/` — and
rebuilds:

    MANIFEST.json              every file, hashed and attributed to an agent
    evidence/provenance.json   every tool call, attributed to an agent
    evidence/claims.json       claims, when the session recorded any
    README.md                  a human map of the run
    audit.html                 the same, self-contained, openable anywhere

Nothing is modified in the source session unless you pass ``--in-place``; by
default output is written to a separate directory.

Attribution is reconstructed from observed execution, in this order of strength:

    1. tool input      — the agent named the file in a Write/Edit/Bash call
    2. producing code  — an agent-written script contains the write statement,
                         giving both the agent and the exact line
    3. execution window— only one agent was running when the file was written
                         (reported as `ambiguous` when several were, never guessed)

Usage::

    python tools/audit_run.py <session_dir> [-o OUTPUT_DIR] [--in-place]
    python tools/audit_run.py <parent_dir> --all -o audits/
    python tools/audit_run.py <session_dir> --json      # summary only
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.claims import ClaimSet, validate_claims          # noqa: E402
from src.utils.provenance import build_provenance               # noqa: E402
from src.utils.run_manifest import (                            # noqa: E402
    CSO_DIR, RunManifest, classify, new_run_id,
)
from src.utils.run_report import render_audit_html, render_readme   # noqa: E402

#: Bookkeeping files that are not analysis artifacts.
LOG_FILES = {"trace.jsonl", "cost_report.json", "transcript.md"}
SKIP_FILES = LOG_FILES | {"environment_full.yml", "MANIFEST.json", "README.md",
                          "audit.html", "run.sh"}


def find_sessions(parent: Path) -> list[Path]:
    """Session directories under *parent* — those carrying a trace or cost report."""
    out = []
    for d in sorted(p for p in parent.iterdir() if p.is_dir()):
        if (d / "trace.jsonl").exists() or (d / "cost_report.json").exists():
            out.append(d)
        elif (d / "workspace").is_dir() and (d / "cost_report.json").exists():
            out.append(d)   # cost_tracking_workspace layout
    return out


def _session_parts(session: Path) -> tuple[Path, Path]:
    """(directory holding the logs, directory holding the artifacts).

    cost_tracking_workspace keeps logs at the top and artifacts one level down in
    `workspace/`; the web and casestudy layouts keep both together.
    """
    if (session / "workspace").is_dir():
        return session, session / "workspace"
    return session, session


def _load_cost_report(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def audit_session(session: Path, out_dir: Path, in_place: bool = False,
                  copy_artifacts: bool = False) -> dict:
    """Reconstruct the audit trail for one session. Returns a summary dict."""
    session = Path(session).resolve()
    log_dir, art_dir = _session_parts(session)

    cost = _load_cost_report(log_dir / "cost_report.json")
    turns = cost.get("turns", []) or []
    query = (turns[0].get("prompt") if turns else "") or ""

    run_dir = session if in_place else Path(out_dir).resolve() / session.name
    run_dir.mkdir(parents=True, exist_ok=True)

    run_id = cost.get("session_id") or session.name
    m = RunManifest.create(
        run_dir.parent if not in_place else run_dir.parent,
        query=query,
        run_id=run_dir.name,
        config={
            "reconstructed_from": str(session),
            "original_session_id": run_id,
            "source_layout": "cost_tracking" if art_dir != log_dir else "flat_session",
            "note": "Retrofitted by tools/audit_run.py; this run predates the "
                    "run-manifest layout, so artifacts are recorded where they "
                    "were originally written.",
        },
    )
    # RunManifest.create() made a skeleton next to the source; point it at the
    # real directory so the retrofit reports on the session itself.
    m.run_dir = run_dir

    prov = build_provenance(log_dir / "trace.jsonl")

    # Index agent-written scripts so their outputs resolve to the producing line.
    scripts = [p for p in art_dir.rglob("*")
               if p.is_file() and p.suffix.lower() in (".py", ".r", ".sh", ".ipynb")
               and not any(part.startswith(".") for part in p.relative_to(art_dir).parts)]
    prov.index_script_outputs(sorted(scripts))

    # ── Register every artifact with the best attribution available ──
    counts = {"tool-input": 0, "tool-output": 0, "script": 0, "mtime": 0, "unattributed": 0}
    for f in sorted(art_dir.rglob("*")):
        if not f.is_file():
            continue
        rel_parts = f.relative_to(art_dir).parts
        if any(p.startswith(".") for p in rel_parts) or f.name in SKIP_FILES:
            continue

        agent, tuid, created_by, method = None, None, None, "unattributed"

        agent, tuid = prov.attribute_path(f)
        if agent:
            # Distinguish "the agent named this file" from "an MCP server wrote
            # it and reported the path", so the report does not overstate how
            # directly the agent produced it.
            call = prov.calls.get(tuid) or {}
            returned = {Path(p).name for p in call.get("files_returned") or []}
            method = "tool-output" if f.name in returned else "tool-input"
        else:
            hit = prov.attribute_by_script(f)
            if hit and hit.get("agent"):
                agent = hit["agent"]
                created_by = f"{Path(hit['script']).name}:{hit['line']}"
                method = "script"
            else:
                try:
                    agent, conf = prov.attribute_by_mtime(f.stat().st_mtime)
                except OSError:
                    conf = "none"
                if agent:
                    method = "mtime"

        counts[method] = counts.get(method, 0) + 1

        target = f
        if copy_artifacts and not in_place:
            dest = run_dir / "work" / (agent or CSO_DIR) / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            target = dest

        e = m.add_artifact(
            target,
            produced_by=agent or CSO_DIR,
            tool_use_id=tuid,
            created_by=created_by,
            kind=classify(f),
        )
        if e is not None and not agent:
            e["description"] = "attribution: could not be determined from the trace"
        elif e is not None:
            e["description"] = f"attribution: {method}"

    # ── Logs ─────────────────────────────────────────────────────────
    if not in_place:
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        for name in LOG_FILES:
            src = log_dir / name
            if src.exists():
                shutil.copy2(src, run_dir / "logs" / name)
        (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
        (run_dir / "inputs").mkdir(parents=True, exist_ok=True)
        if query:
            (run_dir / "inputs" / "query.txt").write_text(
                "\n\n".join(f"--- turn {t.get('turn', i + 1)} ---\n{t.get('prompt', '')}"
                            for i, t in enumerate(turns))
            )
    else:
        (run_dir / "evidence").mkdir(parents=True, exist_ok=True)

    prov.write(run_dir / "evidence" / "provenance.json")

    # ── Claims, if this session ever recorded any ────────────────────
    claim_set = ClaimSet()
    existing = log_dir / "evidence" / "claims.json"
    if existing.exists():
        raw = json.loads(existing.read_text())
        # Non-strict: a historical run has no manifest to have cited against, so
        # unresolvable pointers are reported as unverified rather than dropped.
        r = validate_claims(raw, m, prov, strict=False)
        claim_set = ClaimSet(r.claims)
        claim_set.link_into_manifest(m)

    # ── Record execution order and close out ─────────────────────────
    m.record_execution([
        {"agent": a["agent_type"], "start": a.get("start"), "end": a.get("end"),
         "duration_s": a.get("duration_s")}
        for a in sorted(prov.agents.values(), key=lambda a: a.get("start") or "")
    ])
    m.finalize(status="reconstructed")
    # After finalize(), which stamps `completed` with the current time — for a
    # retrofit we want the session's own clock, not the audit's.
    if cost:
        m.data["config"]["total_cost_usd"] = cost.get("total_cost_usd")
        m.data["config"]["num_turns"] = cost.get("num_turns")
        m.data["created"] = cost.get("start_time") or m.data["created"]
        m.data["completed"] = cost.get("end_time") or m.data["completed"]
    m.write()

    has_trace = (log_dir / "trace.jsonl").exists()
    notes = [
        "This report was reconstructed after the fact by tools/audit_run.py. "
        "The run predates the manifest layout, so artifacts are listed where they "
        "were originally written — flat, in one directory.",
    ]
    if has_trace:
        notes.append(
            f"Attribution sources: {counts.get('tool-input', 0)} named directly in a tool "
            f"call, {counts.get('tool-output', 0)} written by an MCP server and reported in "
            f"its response, {counts.get('script', 0)} traced to the producing line of an "
            f"agent-written script, {counts.get('mtime', 0)} inferred from the execution "
            f"window, {counts.get('unattributed', 0)} could not be determined."
        )
    else:
        # Be explicit rather than rendering an empty-looking report: without a
        # trace there is genuinely nothing to attribute from, and saying so is
        # the honest result. It is also the argument for the new layout.
        notes.append(
            f"**No execution trace was recorded for this session**, so none of its "
            f"{counts.get('unattributed', 0)} artifacts can be attributed to an agent, "
            f"and the order of the analysis cannot be reconstructed. This is the "
            f"limit of what retrofitting can recover: sessions that ran before "
            f"trace logging existed are not auditable at all. Runs made under the "
            f"new layout record attribution as they execute."
        )
    if not claim_set.claims:
        notes.append("This session recorded no claim-evidence objects — that mechanism "
                     "did not exist when it ran. New runs record them automatically.")

    (run_dir / "README.md").write_text(render_readme(m, prov, claim_set, notes))
    (run_dir / "audit.html").write_text(render_audit_html(m, prov, claim_set, notes))

    return {
        "session": str(session),
        "run_dir": str(run_dir),
        "artifacts": len(m.data["artifacts"]),
        "attribution": counts,
        "provenance": prov.summary(),
        "claims": len(claim_set.claims),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Reconstruct an audit trail for an existing session directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage::")[-1],
    )
    ap.add_argument("session", type=Path,
                    help="a session directory, or a parent directory with --all")
    ap.add_argument("-o", "--output", type=Path, default=Path("audits"),
                    help="where to write reports (default: ./audits)")
    ap.add_argument("--all", action="store_true",
                    help="treat SESSION as a parent and audit every session under it")
    ap.add_argument("--in-place", action="store_true",
                    help="write the report into the session directory itself")
    ap.add_argument("--copy-artifacts", action="store_true",
                    help="also copy artifacts into work/<agent>/ in the output dir")
    ap.add_argument("--limit", type=int, help="with --all, stop after N sessions")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args()

    if not args.session.exists():
        print(f"error: {args.session} does not exist", file=sys.stderr)
        return 2

    sessions = find_sessions(args.session) if args.all else [args.session]
    if args.limit:
        sessions = sessions[: args.limit]
    if not sessions:
        print(f"error: no sessions found under {args.session}", file=sys.stderr)
        return 2

    results = []
    for s in sessions:
        try:
            results.append(audit_session(s, args.output, args.in_place, args.copy_artifacts))
        except Exception as e:                      # keep going across a batch
            print(f"  [FAILED] {s.name}: {type(e).__name__}: {e}", file=sys.stderr)
            results.append({"session": str(s), "error": f"{type(e).__name__}: {e}"})
            continue
        if not args.json:
            r = results[-1]
            p = r["provenance"]
            print(f"  [ok] {Path(r['session']).name}")
            print(f"       {r['artifacts']} artifacts · {p['n_tool_calls']} tool calls · "
                  f"{len(p['specialists'])} specialists · {r['claims']} claims")
            att = r["attribution"]
            if att.get("unattributed"):
                print(f"       {att['unattributed']} artifact(s) could not be attributed")
            print(f"       → {r['run_dir']}/audit.html")

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        ok = [r for r in results if "error" not in r]
        print(f"\n{len(ok)}/{len(results)} session(s) audited.")
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
