"""
Run verification — is this run directory still what it says it is?
The Virtual Biotech

Reviewer comment R2.5 asked to be able to replay analyses and reproduce them.
That splits into two guarantees which are worth keeping apart, because conflating
them would overclaim:

**Artifact integrity.** Every artifact is re-hashed against MANIFEST.json. This
answers "has anything changed since the run?"

**Evidence coverage.** Filed claims and their local evidence pointers are checked,
along with the claim references in the final report. A research run with no
claims has incomplete auditing even when its files are unchanged. These checks
establish a recorded evidence trail; they do not verify scientific conclusions.

**Re-execution (exact, opt-in).** The agent-written analysis scripts under
``work/*/code/`` are re-run and their outputs re-hashed. The *code* an agent wrote
is ordinary deterministic Python — if it produced `il33_expression.csv` once it
should produce a byte-identical file again. This is off by default because those
scripts hit networks and large datasets and can take a long time.

What is deliberately **not** claimed: re-running the agents themselves is not
bit-reproducible. LLM sampling is stochastic. ``run_vbt.py --replay`` re-runs the
same turns against the same pinned models and prompts and diffs the result — that
is a comparison, not a reproduction, and it is reported as such.

Usage::

    from src.utils.verify import verify_run
    report = verify_run(run_dir)                 # integrity only
    report = verify_run(run_dir, rerun=True)     # also re-execute the scripts
    report['ok']
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from src.utils.claims import find_claim_refs, validate_claims
from src.utils.provenance import build_provenance
from src.utils.run_manifest import CSO_DIR, RunManifest, sha256_file


def assess_evidence_coverage(manifest, claim_set=None, provenance=None,
                             final_text=None, *, finalizing=False) -> dict[str, Any]:
    """Check recorded evidence pointers and final-report references.

    New runners set ``config.audit_required`` when scientific analysis starts.
    For older runs, analytical artifacts or specialist execution supply that
    signal. Greeting and administrative sessions do not need scientific claims.
    Claim references are always checked, including in non-research sessions.
    """
    run_dir = manifest.run_dir
    config = manifest.data.get("config") or {}
    if provenance is None:
        provenance = build_provenance(run_dir / "logs" / "trace.jsonl")
    explicit = config.get("audit_required")
    if isinstance(explicit, bool):
        required = explicit
    else:
        administrative = {CSO_DIR, "cso", "chief-of-staff", "scientific-reviewer"}
        agents = set(provenance.specialist_types())
        agents.update(e.get("agent") for e in manifest.data.get("execution", []))
        required = bool(agents - administrative - {None, "", "unknown"}) or any(
            a.get("kind") in {"code", "data", "table", "figure", "report"}
            for a in manifest.data.get("artifacts", {}).values()
        )

    out: dict[str, Any] = {
        "required": required, "total": 0, "unresolvable": 0,
        "without_verified_evidence": 0, "reference_count": 0,
        "dangling_references": [], "problems": [],
    }
    problems = out["problems"]
    if manifest.data.get("status") == "in_progress" and not finalizing:
        problems.append({"kind": "unfinished_run", "detail":
                         "A research turn is still in progress or its audit has not finished saving."})
    claims_path = run_dir / "evidence" / "claims.json"
    if claim_set is None:
        raw = []
        if claims_path.exists():
            try:
                raw = json.loads(claims_path.read_text())
                raw = raw.get("claims", []) if isinstance(raw, dict) else raw
                if not isinstance(raw, list):
                    raise ValueError("claims must be a list")
            except (OSError, ValueError) as exc:
                problems.append({"kind": "invalid_claims", "detail":
                                 f"evidence/claims.json cannot be read: {exc}"})
                raw = []
    else:
        raw = claim_set.claims
    out["total"] = len(raw)
    result = validate_claims(raw, manifest, provenance, strict=True)
    out["unresolvable"] = len(result.errors)
    out["without_verified_evidence"] = sum(
        c["n_verified"] == 0 for c in result.claims
    )
    problems.extend({"kind": "claim_unresolvable", "detail": e}
                    for e in result.errors)
    if required and not raw:
        problems.append({"kind": "no_claims", "detail":
                         "Scientific analysis was recorded, but no claims were filed; "
                         "the evidence audit is incomplete."})

    final_path = run_dir / "report" / "FINAL_REPORT.md"
    out["final_report_present"] = final_text is not None or final_path.is_file()
    if final_text is None:
        try:
            final_text = final_path.read_text() if final_path.is_file() else ""
        except (OSError, UnicodeError) as exc:
            final_text = ""
            problems.append({"kind": "unreadable_final_report", "detail":
                             f"report/FINAL_REPORT.md cannot be read: {exc}"})
    refs = find_claim_refs(final_text)
    out["reference_count"] = len(refs)
    filed_ids = {c["id"] for c in result.claims}
    dangling = sorted(set(refs) - filed_ids)
    out["dangling_references"] = dangling
    if dangling:
        problems.append({"kind": "dangling_claim_references", "detail":
                         "The final report references claims that were not validly filed: "
                         + ", ".join(dangling)})
    if required and explicit is True and not final_text.strip():
        problems.append({"kind": "missing_final_report", "detail":
                         "The research run has no recorded final response."})
    elif required and final_text.strip() and not refs:
        problems.append({"kind": "missing_claim_references", "detail":
                         "The final report contains no claim references, so its "
                         "conclusions are not linked to the recorded evidence."})

    # A prior turn's citations do not cover new research in a follow-up. New
    # sessions persist which turns performed research; older reports continue
    # to use the aggregate check above when this metadata is unavailable.
    recorded_turns = config.get("research_turns") or []
    if not isinstance(recorded_turns, list) or not all(type(n) is int and n > 0 for n in recorded_turns):
        problems.append({"kind": "invalid_turn_record", "detail":
                         "The research-turn inventory must contain positive integer turn numbers."})
        recorded_turns = []
    research_turns = set(recorded_turns)
    has_turn_metadata = "research_turns" in config
    turns = []
    turn_report = run_dir / "logs" / "cost_report.json"
    if turn_report.is_file():
        try:
            turn_data = json.loads(turn_report.read_text())
            turns = turn_data.get("turns", []) if isinstance(turn_data, dict) else []
            if not isinstance(turns, list) or not all(isinstance(t, dict) for t in turns):
                raise ValueError("turns must be a list of turn records")
            has_turn_metadata = has_turn_metadata or any("audit_required" in t for t in turns)
        except (OSError, ValueError) as exc:
            if has_turn_metadata:
                problems.append({"kind": "invalid_turn_record", "detail":
                                 f"Per-turn evidence coverage cannot be read: {exc}"})
            turns = []
    if has_turn_metadata:
        seen = set()
        for turn in turns:
            number = turn.get("turn")
            if (type(number) is not int or number < 1 or number in seen
                    or not isinstance(turn.get("response", ""), str)
                    or ("audit_required" in turn and type(turn["audit_required"]) is not bool)):
                problems.append({"kind": "invalid_turn_record", "detail":
                                 "A saved turn has an invalid or duplicate number, response, or audit requirement."})
                continue
            seen.add(number)
            if not (turn.get("audit_required") or number in research_turns):
                continue
            research_turns.add(number)
            turn_refs = set(find_claim_refs(turn.get("response", "")))
            if not turn_refs.intersection(filed_ids):
                problems.append({"kind": "missing_turn_claim_references", "turn": number,
                                 "detail": f"Research turn {number} has no valid claim references; "
                                           "an earlier turn's citations do not cover its findings."})
            unresolved = sorted(turn_refs - filed_ids)
            if unresolved:
                problems.append({"kind": "dangling_turn_claim_references", "turn": number,
                                 "detail": f"Turn {number} references claims that are not validly filed: "
                                           + ", ".join(unresolved)})
        for number in research_turns - seen:
            problems.append({"kind": "missing_turn_record", "turn": number,
                             "detail": f"Research turn {number} has no saved response record."})
    out["research_turns"] = sorted(research_turns)

    for error in config.get("audit_errors") or []:
        problems.append({"kind": "audit_capture_error", "detail": str(error)})
    interrupted_turns = config.get("interrupted_turns") or []
    for turn in interrupted_turns:
        problems.append({"kind": "interrupted_turn", "turn": turn,
                         "detail": f"Turn {turn} was interrupted; its response or evidence record may be incomplete."})
    if manifest.data.get("status") == "interrupted" and not interrupted_turns:
        problems.append({"kind": "interrupted_turn",
                         "detail": "The research run was interrupted; its response or evidence record may be incomplete."})
    for error in config.get("data_source_errors") or []:
        detail = (f"{error.get('tool_name', 'Data source')}: {error.get('error', 'unavailable')}"
                  if isinstance(error, dict) else str(error))
        problems.append({"kind": "data_source_unavailable", "detail": detail})

    out["ok"] = not problems
    out["status"] = ("incomplete" if problems else
                     "complete" if required or raw or refs else "not_required")
    return out


def verify_integrity(run_dir) -> dict[str, Any]:
    """Check artifact integrity and evidence coverage independently."""
    run_dir = Path(run_dir)
    out: dict[str, Any] = {
        "run_dir": str(run_dir), "checks": {}, "problems": [], "ok": False,
        "status": "failed", "integrity": {"ok": False, "status": "failed"},
        "evidence": {"ok": False, "status": "unavailable"},
    }

    if not (run_dir / "MANIFEST.json").exists():
        out["ok"] = False
        out["problems"].append({
            "kind": "no_manifest",
            "detail": f"{run_dir} has no MANIFEST.json — not an auditable run.",
        })
        return out

    try:
        m = RunManifest.load(run_dir)
    except (OSError, ValueError) as exc:
        out["problems"].append({"kind": "invalid_manifest", "detail": str(exc)})
        return out
    out["run_id"] = m.run_id
    out["query"] = m.data.get("query", "")

    # 1. Artifact hashes.
    problems = m.verify()
    out["checks"]["artifacts"] = {
        "total": len(m.data.get("artifacts", {})),
        "failed": len(problems),
    }
    for p in problems:
        out["problems"].append({
            "kind": p["problem"], "path": p["path"],
            "detail": (f"{p['path']} is missing" if p["problem"] == "missing"
                       else f"{p['path']} has changed since the run "
                            f"(expected {p.get('expected', '')[:12]}…, "
                            f"found {p.get('actual', '')[:12]}…)"),
        })

    # 2. The run should describe itself.
    for f in ("README.md", "MANIFEST.json"):
        if not (run_dir / f).exists():
            out["problems"].append({
                "kind": "missing_report",
                "detail": f"{f} is absent — regenerate with ./run.sh audit.",
            })

    out["integrity"] = {"ok": not out["problems"],
                        "status": "failed" if out["problems"] else "passed"}

    # 3. Unchanged files do not establish that scientific claims were recorded.
    coverage = assess_evidence_coverage(m)
    out["evidence"] = coverage
    out["checks"]["claims"] = {
        k: coverage[k] for k in ("total", "unresolvable", "without_verified_evidence")
    }
    out["problems"].extend(coverage["problems"])
    out["ok"] = out["integrity"]["ok"] and coverage["ok"]
    out["status"] = ("failed" if not out["integrity"]["ok"] else
                     "incomplete" if not coverage["ok"] else "passed")
    return out


def rerun_scripts(run_dir, python_exe: Optional[str] = None,
                  timeout: int = 3600) -> dict[str, Any]:
    """Re-execute the agent-written analysis scripts and compare their outputs.

    Each script runs in a scratch copy of its agent's directory, so a failed or
    misbehaving re-run cannot damage the original run. Outputs are compared by
    hash against the manifest.

    Scripts are run in manifest order per agent, which reflects the order they
    were written — the usual case is a numbered sequence (`01_…`, `02_…`) where
    that is also the dependency order.
    """
    run_dir = Path(run_dir)
    m = RunManifest.load(run_dir)
    python_exe = python_exe or sys.executable

    scripts = [
        (key, e) for key, e in sorted(m.data.get("artifacts", {}).items())
        if e.get("kind") == "code" and key.endswith(".py")
    ]
    out: dict[str, Any] = {
        "python": python_exe, "scripts": [], "ok": True,
        "n_scripts": len(scripts),
    }
    if not scripts:
        out["note"] = "This run contains no Python analysis scripts to re-execute."
        return out

    for key, _ in scripts:
        src = run_dir / key
        if not src.exists():
            out["scripts"].append({"script": key, "status": "missing"})
            out["ok"] = False
            continue

        # Scratch copy of the owning agent's tree: the script's relative paths
        # keep working, and nothing it writes touches the original run.
        agent_root = run_dir / Path(key).parts[0] / Path(key).parts[1] \
            if len(Path(key).parts) > 2 else run_dir
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            try:
                shutil.copytree(agent_root, work)
            except OSError as e:
                out["scripts"].append({"script": key, "status": "copy_failed",
                                       "detail": str(e)})
                out["ok"] = False
                continue

            rel = Path(key).relative_to(agent_root.relative_to(run_dir)) \
                if agent_root != run_dir else Path(key)
            target = work / rel
            env = dict(os.environ, workspace=str(work), VBT_VERIFY="1")
            try:
                proc = subprocess.run(
                    [python_exe, str(target)], cwd=str(work), env=env,
                    capture_output=True, text=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                out["scripts"].append({"script": key, "status": "timeout",
                                       "detail": f"exceeded {timeout}s"})
                out["ok"] = False
                continue
            except OSError as e:
                out["scripts"].append({"script": key, "status": "error",
                                       "detail": str(e)})
                out["ok"] = False
                continue

            if proc.returncode != 0:
                out["scripts"].append({
                    "script": key, "status": "failed",
                    "returncode": proc.returncode,
                    "stderr": (proc.stderr or "")[-2000:],
                })
                out["ok"] = False
                continue

            # Compare whatever it reproduced against the recorded hashes.
            matched, differed, absent = [], [], []
            for akey, aent in m.data["artifacts"].items():
                if aent.get("kind") == "code":
                    continue
                name = Path(akey).name
                cands = list(work.rglob(name))
                if not cands:
                    continue
                actual = sha256_file(cands[0])
                (matched if actual == aent.get("sha256") else differed).append(akey)
            out["scripts"].append({
                "script": key, "status": "ok",
                "outputs_matched": matched, "outputs_differed": differed,
            })
            if differed:
                out["ok"] = False

    return out


def verify_run(run_dir, rerun: bool = False,
               python_exe: Optional[str] = None) -> dict[str, Any]:
    """Full verification. Integrity always; re-execution only when asked."""
    report = verify_integrity(run_dir)
    if rerun and "run_id" in report:
        report["rerun"] = rerun_scripts(run_dir, python_exe=python_exe)
        if not report["rerun"]["ok"]:
            report["ok"] = False
            report["status"] = "failed"
            for s in report["rerun"]["scripts"]:
                if s.get("status") != "ok":
                    report["problems"].append({
                        "kind": f"rerun_{s['status']}", "path": s["script"],
                        "detail": s.get("detail") or s.get("stderr", "")[:300],
                    })
                elif s.get("outputs_differed"):
                    report["problems"].append({
                        "kind": "rerun_output_differs", "path": s["script"],
                        "detail": "re-execution produced different bytes for: "
                                  + ", ".join(s["outputs_differed"][:5]),
                    })
    return report


def format_report(report: dict[str, Any]) -> str:
    """Human-readable verification summary for the terminal."""
    L = []
    L.append(f"Run:    {report.get('run_id', report['run_dir'])}")
    if report.get("query"):
        L.append(f"Query:  {report['query'][:70]}")
    a = report["checks"].get("artifacts")
    if a:
        L.append(f"Files:  {a['total']} recorded, {a['failed']} failed hash check")
    c = report["checks"].get("claims")
    if c is not None:
        L.append(f"Claims: {c['total']} filed, {c.get('unresolvable', 0)} with "
                 f"unresolvable evidence, "
                 f"{c.get('without_verified_evidence', 0)} with none verified")
    integrity = report.get("integrity", {})
    coverage = report.get("evidence", {})
    if integrity:
        L.append(f"Artifact integrity: {integrity['status'].upper()}")
    if coverage:
        status = coverage["status"].replace("_", " ").upper()
        L.append(f"Evidence coverage:  {status}")
    if "rerun" in report:
        r = report["rerun"]
        okc = sum(1 for s in r["scripts"] if s.get("status") == "ok"
                  and not s.get("outputs_differed"))
        L.append(f"Rerun:  {okc}/{r['n_scripts']} scripts reproduced their outputs")
        if r.get("note"):
            L.append(f"        {r['note']}")

    if report["ok"]:
        L.append("")
        if coverage.get("status") == "not_required":
            L.append("PASS — recorded files are unchanged; no scientific analysis "
                     "requiring claims was recorded.")
        else:
            L.append("PASS — recorded files, claim pointers and report references "
                     "passed structural checks.")
    else:
        L.append("")
        status = "INCOMPLETE" if report.get("status") == "incomplete" else "FAIL"
        L.append(f"{status} — {len(report['problems'])} problem(s):")
        for p in report["problems"][:25]:
            L.append(f"  [{p['kind']}] {p.get('detail', p.get('path', ''))}")
        if len(report["problems"]) > 25:
            L.append(f"  … and {len(report['problems']) - 25} more")
    L.append("These checks do not verify scientific correctness or external citations.")
    return "\n".join(L)
