#!/usr/bin/env python3
"""
run_vbt.py — headless runner, replay driver and verifier for The Virtual Biotech.

The web interface is one front end onto a session; this is the other. It runs the
same CSO, the same specialists, the same MCP servers, and produces the same
self-describing run directory — which is what makes a run scriptable, batchable
and replayable.

Reuses ``CSOSession`` from gradio_cso_app rather than reimplementing it, so the
headless path cannot drift from what the web app actually does. (``gradio_cso_app``
and ``run_casestudy_costing`` already carry near-identical copies of the session
setup; consolidating those is worthwhile but is a separate change, and one that
should not be made without being able to run the app to check it.)

Usage::

    python run_vbt.py run "Is KRAS a good target for pancreatic cancer?"
    python run_vbt.py run -f questions.txt          # one turn per line
    python run_vbt.py replay <RUN_ID>               # re-run a past run's turns
    python run_vbt.py verify <RUN_ID> [--rerun]     # integrity / re-execution
    python run_vbt.py list                          # index of all runs
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _app():
    """Import the app lazily so `verify` and `list` work without Gradio or the SDK."""
    _require_api_key()
    import gradio_cso_app as app
    return app


def _require_api_key(env_file: Path = None) -> None:
    """Fail with a useful message rather than the CLI's 'Not logged in'.

    The key lives in .env and is exported by run.sh. Invoking this script
    directly skips that, and the SDK then reports an authentication prompt that
    gives no hint about the actual cause.

    ``env_file`` is a parameter so the missing-key path can be tested in a
    checkout that does have a .env.
    """
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env_file = Path(__file__).parent / ".env" if env_file is None else Path(env_file)
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                value = line.split("=", 1)[1].strip()
                if value:
                    os.environ["ANTHROPIC_API_KEY"] = value
                    return
    print(f"error: ANTHROPIC_API_KEY is not set, and {env_file} does not define it.\n"
          "       Run through ./run.sh, which loads it, or export it yourself.\n"
          "       (Without it the SDK reports 'Not logged in', which does not say why.)",
          file=sys.stderr)
    sys.exit(2)


# ── Headless run ─────────────────────────────────────────────────────

async def _run_turns(turns: list[str], model_key: str, session_id: str = None,
                     quiet: bool = False):
    """Drive a CSOSession through a list of user turns."""
    import uuid as _uuid
    app = _app()

    session_id = session_id or str(_uuid.uuid4())
    history: list = []

    for i, turn in enumerate(turns, 1):
        if not quiet:
            print(f"\n{'=' * 72}\nTurn {i}/{len(turns)}: {turn[:100]}\n{'=' * 72}")
        last = None
        async for result in app.async_process_message(
            turn, history, session_id, model_key=model_key
        ):
            last = result
        if last:
            history = last[0]
            session_id = last[2]
            if not quiet and history and history[-1].get("role") == "assistant":
                print(history[-1]["content"])

    session = app.session_manager.sessions.get(session_id)
    if session is None or session.run is None:
        print("\n[ERROR] No run directory was created.", file=sys.stderr)
        return None

    try:
        await session.cleanup()
    except Exception as e:
        print(f"[WARNING] cleanup: {e}", file=sys.stderr)

    return session.run.run_dir


def cmd_run(args) -> int:
    if args.file:
        turns = [ln.strip() for ln in Path(args.file).read_text().splitlines()
                 if ln.strip() and not ln.startswith("#")]
    else:
        turns = [q for q in args.query if q.strip()]
    if not turns:
        print("error: no query given", file=sys.stderr)
        return 2

    run_dir = asyncio.run(_run_turns(turns, args.model, quiet=args.quiet))
    if run_dir is None:
        return 1
    _report_run(run_dir)
    return 0


# ── Replay ───────────────────────────────────────────────────────────

def cmd_replay(args) -> int:
    """Re-run a past run's turns into a fresh run directory, then diff.

    This is trajectory-level, not bit-level: the models, prompt versions and MCP
    servers are pinned to what the original recorded, but LLM sampling is
    stochastic, so the two runs are *compared*, never asserted identical. For an
    exact guarantee use `verify --rerun`, which re-executes the analysis code.
    """
    from src.utils.run_manifest import RunManifest

    src_dir = _resolve_run(args.run_id)
    if src_dir is None:
        return 2
    original = RunManifest.load(src_dir)

    query_file = src_dir / "inputs" / "query.txt"
    turns = _parse_turns(query_file)
    if not turns:
        print(f"error: no recorded turns in {query_file}", file=sys.stderr)
        return 2

    cfg = original.data.get("config", {})
    model_key = cfg.get("specialist_model_label", "Sonnet 4.5 (default)")

    print(f"Replaying {original.run_id}")
    print(f"  turns:   {len(turns)}")
    print(f"  model:   {model_key}")
    print(f"  commit:  {cfg.get('git_commit', 'unknown')}")
    current = _current_commit()
    if cfg.get("git_commit") and current and cfg["git_commit"] != current:
        print(f"  WARNING: the code has changed since this run "
              f"({cfg['git_commit'][:8]} → {current[:8]}). The replay exercises "
              f"today's code, not the original's.")
    print()

    new_dir = asyncio.run(_run_turns(turns, model_key, quiet=args.quiet))
    if new_dir is None:
        return 1

    diff = compare_runs(src_dir, new_dir)
    print("\n" + format_diff(diff))
    (Path(new_dir) / "replay_diff.json").write_text(
        json.dumps(diff, indent=2, default=str))
    print(f"\nDiff written to {new_dir}/replay_diff.json")
    return 0


def compare_runs(a_dir, b_dir) -> dict:
    """Compare two runs by artifacts, agents and claims."""
    from src.utils.claims import ClaimSet
    from src.utils.run_manifest import RunManifest

    a, b = RunManifest.load(a_dir), RunManifest.load(b_dir)
    a_names = {Path(k).name for k in a.data.get("artifacts", {})}
    b_names = {Path(k).name for k in b.data.get("artifacts", {})}
    a_ag = {x for x in a.data.get("agents", []) if x != "_cso"}
    b_ag = {x for x in b.data.get("agents", []) if x != "_cso"}
    a_cl = ClaimSet.load(Path(a_dir) / "evidence" / "claims.json")
    b_cl = ClaimSet.load(Path(b_dir) / "evidence" / "claims.json")

    same_hash = sum(
        1 for k, e in a.data.get("artifacts", {}).items()
        if k in b.data.get("artifacts", {})
        and b.data["artifacts"][k].get("sha256") == e.get("sha256")
    )
    return {
        "original": a.run_id, "replay": b.run_id,
        "agents": {
            "original": sorted(a_ag), "replay": sorted(b_ag),
            "both": sorted(a_ag & b_ag),
            "only_original": sorted(a_ag - b_ag), "only_replay": sorted(b_ag - a_ag),
        },
        "artifacts": {
            "n_original": len(a_names), "n_replay": len(b_names),
            "same_name": len(a_names & b_names),
            "identical_bytes": same_hash,
            "only_original": sorted(a_names - b_names)[:40],
            "only_replay": sorted(b_names - a_names)[:40],
        },
        "claims": {"n_original": len(a_cl.claims), "n_replay": len(b_cl.claims)},
    }


def format_diff(d: dict) -> str:
    ag, ar = d["agents"], d["artifacts"]
    L = [
        "Replay comparison", "=" * 72,
        f"  original  {d['original']}",
        f"  replay    {d['replay']}",
        "",
        f"  specialists   {len(ag['both'])} of {len(ag['original'])} matched"
        + (f"; only original: {', '.join(ag['only_original'])}" if ag["only_original"] else "")
        + (f"; only replay: {', '.join(ag['only_replay'])}" if ag["only_replay"] else ""),
        f"  artifacts     {ar['n_original']} → {ar['n_replay']}, "
        f"{ar['same_name']} same filename, {ar['identical_bytes']} byte-identical",
        f"  claims        {d['claims']['n_original']} → {d['claims']['n_replay']}",
        "",
        "  A replay re-runs the same turns against the same pinned models and",
        "  prompts. LLM sampling is stochastic, so differences here are expected",
        "  and are not by themselves a failure. For an exact guarantee, use",
        "  `verify --rerun`, which re-executes the analysis code the agents wrote.",
    ]
    return "\n".join(L)


# ── Verify / list ────────────────────────────────────────────────────

def cmd_verify(args) -> int:
    from src.utils.verify import format_report, verify_run

    run_dir = _resolve_run(args.run_id)
    if run_dir is None:
        return 2
    report = verify_run(run_dir, rerun=args.rerun, python_exe=args.python)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_report(report))
    return 0 if report["ok"] else 1


def cmd_list(args) -> int:
    from src.utils.run_index import update_index

    rows = update_index(_runs_dir())
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print(f"No runs in {_runs_dir()}.")
        return 0
    print(f"{'RUN ID':<46} {'FILES':>5} {'CLAIMS':>6}  QUERY")
    for r in rows[: args.limit]:
        q = (r["query"] or "—").replace("\n", " ")[:60]
        print(f"{r['run_id']:<46} {r['n_artifacts']:>5} {r['n_claims']:>6}  {q}")
    if len(rows) > args.limit:
        print(f"\n… {len(rows) - args.limit} more. Full list: {_runs_dir()}/INDEX.md")
    return 0


# ── Helpers ──────────────────────────────────────────────────────────

def _runs_dir() -> Path:
    import os
    return Path(os.environ.get("VBT_RUNS_DIR", Path(__file__).parent / "runs"))


def _resolve_run(run_id: str) -> Path:
    """Accept a run id, a path, or a unique prefix."""
    p = Path(run_id)
    if (p / "MANIFEST.json").exists():
        return p
    runs = _runs_dir()
    if (runs / run_id / "MANIFEST.json").exists():
        return runs / run_id
    matches = [d for d in sorted(runs.glob(f"{run_id}*"))
               if (d / "MANIFEST.json").exists()] if runs.exists() else []
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"error: {run_id!r} matches {len(matches)} runs:", file=sys.stderr)
        for m in matches[:10]:
            print(f"  {m.name}", file=sys.stderr)
        return None
    print(f"error: no run matching {run_id!r} under {runs}", file=sys.stderr)
    return None


def _parse_turns(query_file: Path) -> list[str]:
    """Read inputs/query.txt back into its list of turns."""
    if not query_file.exists():
        return []
    turns, cur = [], []
    for line in query_file.read_text().splitlines():
        if line.startswith("--- turn ") and line.rstrip().endswith("---"):
            if cur:
                turns.append("\n".join(cur).strip())
                cur = []
        else:
            cur.append(line)
    if cur:
        turns.append("\n".join(cur).strip())
    return [t for t in turns if t]


def _current_commit() -> str:
    import subprocess
    try:
        return subprocess.run(
            ["git", "-C", str(Path(__file__).parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _report_run(run_dir) -> None:
    from src.utils.run_manifest import RunManifest
    m = RunManifest.load(run_dir)
    s = m.summary()
    print(f"\n{'=' * 72}")
    print(f"Run complete: {m.run_id}")
    print(f"  directory   {run_dir}")
    print(f"  artifacts   {s['n_artifacts']} ({s['artifacts_by_kind']})")
    print(f"  specialists {', '.join(s['agents']) or 'none'}")
    print(f"  report      {run_dir}/README.md")
    print(f"  audit       {run_dir}/audit.html")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run_vbt.py",
        description="Headless runner, replay driver and verifier for The Virtual Biotech.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="run one or more turns headlessly")
    p.add_argument("query", nargs="*", help="user turn(s); repeat for a conversation")
    p.add_argument("-f", "--file", help="file with one turn per line")
    p.add_argument("-m", "--model", default="Sonnet 4.5 (default)",
                   help="model label")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("replay", help="re-run a past run's turns and diff the result")
    p.add_argument("run_id")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("verify", help="check artifact hashes and claim evidence")
    p.add_argument("run_id")
    p.add_argument("--rerun", action="store_true",
                   help="also re-execute the analysis scripts and compare outputs")
    p.add_argument("--python", help="interpreter to use for --rerun")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("list", help="list all runs")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
