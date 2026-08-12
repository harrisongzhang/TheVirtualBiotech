"""
Run verification — is this run directory still what it says it is?
The Virtual Biotech

Reviewer comment R2.5 asked to be able to replay analyses and reproduce them.
That splits into two guarantees which are worth keeping apart, because conflating
them would overclaim:

**Integrity (exact, always available).** Every artifact is re-hashed against
MANIFEST.json, and every claim's evidence pointer is re-resolved. This answers
"has anything changed since the run, and does the evidence still hold up?" It is
deterministic and needs nothing but the directory.

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

from src.utils.claims import ClaimSet, validate_claims
from src.utils.provenance import build_provenance
from src.utils.run_manifest import RunManifest, sha256_file


def verify_integrity(run_dir) -> dict[str, Any]:
    """Re-hash every artifact and re-resolve every claim's evidence."""
    run_dir = Path(run_dir)
    out: dict[str, Any] = {
        "run_dir": str(run_dir), "checks": {}, "problems": [], "ok": True,
    }

    if not (run_dir / "MANIFEST.json").exists():
        out["ok"] = False
        out["problems"].append({
            "kind": "no_manifest",
            "detail": f"{run_dir} has no MANIFEST.json — not an auditable run.",
        })
        return out

    m = RunManifest.load(run_dir)
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

    # 2. Claim evidence still resolves.
    claims_path = run_dir / "evidence" / "claims.json"
    if claims_path.exists():
        prov = build_provenance(run_dir / "logs" / "trace.jsonl")
        cs = ClaimSet.load(claims_path)
        res = validate_claims(cs.claims, m, prov, strict=True)
        out["checks"]["claims"] = {
            "total": len(cs.claims),
            "unresolvable": len(res.errors),
            "without_verified_evidence":
                len(cs.stats()["claims_without_verified_evidence"]),
        }
        for e in res.errors:
            out["problems"].append({"kind": "claim_unresolvable", "detail": e})
    else:
        out["checks"]["claims"] = {"total": 0}

    # 3. The run should describe itself.
    for f in ("README.md", "MANIFEST.json"):
        if not (run_dir / f).exists():
            out["problems"].append({
                "kind": "missing_report",
                "detail": f"{f} is absent — regenerate with ./run.sh audit.",
            })

    out["ok"] = not out["problems"]
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
    if rerun:
        report["rerun"] = rerun_scripts(run_dir, python_exe=python_exe)
        if not report["rerun"]["ok"]:
            report["ok"] = False
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
    if c and c.get("total"):
        L.append(f"Claims: {c['total']} filed, {c.get('unresolvable', 0)} with "
                 f"unresolvable evidence, "
                 f"{c.get('without_verified_evidence', 0)} with none verified")
    if "rerun" in report:
        r = report["rerun"]
        okc = sum(1 for s in r["scripts"] if s.get("status") == "ok"
                  and not s.get("outputs_differed"))
        L.append(f"Rerun:  {okc}/{r['n_scripts']} scripts reproduced their outputs")
        if r.get("note"):
            L.append(f"        {r['note']}")

    if report["ok"]:
        L.append("")
        L.append("PASS — every artifact matches its recorded hash and every claim "
                 "resolves.")
    else:
        L.append("")
        L.append(f"FAIL — {len(report['problems'])} problem(s):")
        for p in report["problems"][:25]:
            L.append(f"  [{p['kind']}] {p.get('detail', p.get('path', ''))}")
        if len(report["problems"]) > 25:
            L.append(f"  … and {len(report['problems']) - 25} more")
    return "\n".join(L)
