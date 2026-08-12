"""
Provenance MCP Tools — claims, artifacts and the analysis plan

These are the tools by which agents write into the run's audit record. Three
things matter about their design:

1. **Validation happens on write.** ``record_claims`` resolves every evidence
   pointer against the run's MANIFEST.json and trace.jsonl. A claim citing a
   file that was never written, or a tool call that never happened, is rejected
   and the errors come back to the caller to fix. An evidence link that is never
   checked is worse than no link: it looks like provenance while being a guess.

2. **They operate on the current run, discovered from the working directory.**
   Agents run with cwd inside the run, so no run id has to be threaded through
   prompts (where it would inevitably be mistyped or hallucinated).

3. **They are additive.** Re-filing a claim id replaces that claim; nothing is
   silently dropped.

Tools:
    record_claims     (CSO)         file claim→evidence objects; validated
    register_artifact (specialists) annotate an output with what it shows
    write_plan        (CSO)         persist the analysis DAG before dispatching
    list_artifacts    (any)         see what this run has produced so far
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.utils.claims import ClaimSet, validate_claims       # noqa: E402
from src.utils.plan_runner import validate_plan              # noqa: E402
from src.utils.provenance import build_provenance            # noqa: E402
from src.utils.run_manifest import RunManifest               # noqa: E402


# ── Run discovery ────────────────────────────────────────────────────

def _find_run(start: Optional[str] = None) -> Optional[Path]:
    """Walk up from *start* (or cwd) to the nearest directory with a MANIFEST.

    Agents run with cwd inside the run directory, so this resolves without the
    caller having to know — or invent — a run id.
    """
    p = Path(start or os.environ.get("VBT_RUN_DIR") or os.getcwd()).resolve()
    for cand in (p, *p.parents):
        if (cand / "MANIFEST.json").exists():
            return cand
    return None


def _load(run_dir: Optional[str] = None):
    """(manifest, provenance) for the active run, or (None, None)."""
    d = _find_run(run_dir)
    if d is None:
        return None, None
    try:
        m = RunManifest.load(d)
    except (json.JSONDecodeError, OSError):
        return None, None
    prov = build_provenance(d / "logs" / "trace.jsonl")
    return m, prov


def _no_run() -> Dict[str, Any]:
    return {
        "ok": False,
        "error": "No run directory found. This tool must be called from within "
                 "a run workspace (a directory containing MANIFEST.json).",
    }


# ── Tools ────────────────────────────────────────────────────────────

def record_claims(claims: List[Dict[str, Any]], run_dir: Optional[str] = None) -> Dict[str, Any]:
    """Record claim-evidence objects for the current run.

    Call this before finishing a substantive answer. Every factual assertion in
    your synthesis that rests on a specialist's analysis should be a claim here,
    and each claim must cite evidence that exists in this run.

    Reference a claim from your prose as ``[[claim:C1]]`` — the interface turns
    that into a clickable superscript that opens the evidence panel.

    Args:
        claims: list of claim objects. Each has:
            id          (str)  short unique id, e.g. "C1"
            text        (str)  the assertion, as one self-contained sentence
            agent       (str)  optional; the specialist whose work supports it
            confidence  (str)  "strong" | "moderate" | "weak"
            evidence    (list) at least one entry, each of:
              {"kind": "table"|"figure"|"code"|"artifact",
               "path": "work/<agent>/results/tables/x.csv",
               "note": "which row/column"}          ← resolved against MANIFEST.json
              {"kind": "tool_call", "tool_use_id": "toolu_..."}
                                                     ← resolved against trace.jsonl
              {"kind": "citation", "pmid": "12345678"}
                                                     ← external, shown as "external ref"
        run_dir: optional explicit run directory; normally inferred from cwd.

    Returns:
        On success: {"ok": true, "recorded": N, "claims": [...], "warnings": [...]}
        On failure: {"ok": false, "errors": [...]} — nothing is recorded. Fix the
        cited paths or tool ids and call again. Use list_artifacts() to see what
        this run actually produced.
    """
    m, prov = _load(run_dir)
    if m is None:
        return _no_run()

    result = validate_claims(claims, m, prov, strict=True)
    if not result.ok:
        return {
            "ok": False,
            "errors": result.errors,
            "warnings": result.warnings,
            "hint": "Every artifact path must match a file recorded in this run's "
                    "MANIFEST.json, and every tool_use_id must appear in its trace. "
                    "Call list_artifacts() to see the available evidence.",
        }

    path = m.run_dir / "evidence" / "claims.json"
    cs = ClaimSet.load(path)
    cs.add(result.claims)
    cs.link_into_manifest(m)
    cs.write(path)
    m.write()

    return {
        "ok": True,
        "recorded": len(result.claims),
        "total_claims": len(cs.claims),
        "warnings": result.warnings,
        "claims": [
            {"id": c["id"], "n_evidence": len(c["evidence"]),
             "n_verified": c["n_verified"]}
            for c in result.claims
        ],
    }


def register_artifact(path: str, description: str,
                      run_dir: Optional[str] = None) -> Dict[str, Any]:
    """Describe a file you produced, so a human can tell what it is.

    The run records every file automatically — you do not need this to make an
    artifact appear. What you add here is the one thing automation cannot infer:
    what the file *shows*. Call it for outputs that carry a finding.

    Args:
        path: the file, absolute or relative to the run directory.
        description: one line on what it contains and what it demonstrates,
            e.g. "Mean IL1RL1 expression per lung cell type; mast cells highest".
        run_dir: optional explicit run directory; normally inferred from cwd.

    Returns:
        {"ok": true, "artifact": {...}} or {"ok": false, "error": "..."}
    """
    m, _ = _load(run_dir)
    if m is None:
        return _no_run()

    p = Path(path)
    if not p.is_absolute():
        p = m.run_dir / path
    # Guard: a registered artifact must live inside this run's directory,
    # otherwise an absolute path outside the run could be filed as evidence.
    p = p.resolve()
    run_dir = m.run_dir.resolve()
    if p != run_dir and run_dir not in p.parents:
        return {"ok": False,
                "error": f"Path is outside the run directory: {path}. "
                         "Only files within this run can be registered as evidence."}
    if not p.is_file():
        return {"ok": False,
                "error": f"No such file: {path}. Write the file before registering it."}

    key = m.rel(p)
    entry = m.data["artifacts"].get(key) or m.add_artifact(p)
    if entry is None:
        return {"ok": False, "error": f"Could not register {path}."}
    entry["description"] = description[:500]
    m.write()
    return {"ok": True, "artifact": {
        "path": entry["path"], "kind": entry["kind"],
        "produced_by": entry["produced_by"], "description": entry["description"],
    }}


def write_plan(steps: List[Dict[str, Any]], goal: str = "",
               run_dir: Optional[str] = None) -> Dict[str, Any]:
    """Record the analysis plan before dispatching specialists.

    Reviewer comment R2.5 noted there is no workflow manager sequencing the
    analyses. This makes the sequence explicit and checkable: declare the steps
    and their dependencies up front, and the run records planned order against
    what actually happened.

    The plan is validated on write — a cycle, a dangling dependency, or a
    duplicate id is rejected. You may still adapt as results come in; deviations
    are recorded rather than forbidden.

    Args:
        steps: list of step objects, each:
            id                (str)  short unique id, e.g. "s1"
            agent             (str)  specialist to dispatch, e.g. "genomics-analyst"
            task              (str)  what that specialist is being asked to do
            depends_on        (list) ids of steps that must finish first; [] if none
            expected_outputs  (list) optional; files this step should produce
        goal: one line on what the plan as a whole is meant to establish.
        run_dir: optional explicit run directory; normally inferred from cwd.

    Returns:
        {"ok": true, "n_steps": N, "order": [...]} with a valid execution order,
        or {"ok": false, "errors": [...]}.
    """
    m, _ = _load(run_dir)
    if m is None:
        return _no_run()

    result = validate_plan(steps, goal=goal)
    if not result.ok:
        return {"ok": False, "errors": result.errors, "warnings": result.warnings}

    m.data["plan"] = result.plan
    (m.run_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (m.run_dir / "inputs" / "plan.json").write_text(
        json.dumps(result.plan, indent=2, default=str)
    )
    m.write()
    return {
        "ok": True,
        "n_steps": len(result.plan["steps"]),
        "order": result.order,
        "warnings": result.warnings,
    }


def list_artifacts(agent: Optional[str] = None, kind: Optional[str] = None,
                   run_dir: Optional[str] = None) -> Dict[str, Any]:
    """List what this run has produced so far — the citable evidence.

    Use this before record_claims to get exact paths, rather than guessing at
    filenames (a guessed path is rejected).

    Args:
        agent: optional filter, e.g. "single-cell-analyst".
        kind: optional filter — "figure", "table", "data", "code", "report".
        run_dir: optional explicit run directory; normally inferred from cwd.

    Returns:
        {"ok": true, "run_id": ..., "n": N, "artifacts": [...]}
    """
    m, _ = _load(run_dir)
    if m is None:
        return _no_run()

    rows = []
    for e in m.data["artifacts"].values():
        if agent and e["produced_by"] != agent:
            continue
        if kind and e["kind"] != kind:
            continue
        rows.append({
            "path": e["path"], "kind": e["kind"], "bytes": e["bytes"],
            "produced_by": e["produced_by"],
            "description": e.get("description"),
            "created_by": e.get("created_by"),
            "cited_by": e.get("cited_by", []),
        })
    rows.sort(key=lambda r: (r["produced_by"], r["kind"], r["path"]))
    return {"ok": True, "run_id": m.run_id, "n": len(rows), "artifacts": rows}
