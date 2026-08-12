"""
Provenance — reconstruct "who did what" from an execution trace
The Virtual Biotech

Answers the reviewer's central question — *which artifact supports which claim
from which subagent* — by deriving, for every tool call in a run, the specialist
that made it and the files it wrote.

The important property is that this is reconstructed from **observed execution**
(`logs/trace.jsonl`, written by src.utils.trace_logger), not from any agent's
self-report. An agent cannot misattribute its own work, because it is not asked.

Attribution has two sources, tried in order:

1. ``agent`` recorded on the tool event at hook time. Preferred, but only present
   on runs made after the trace_logger change that carries it.
2. Post-hoc: intersect each ``tool_use_id`` against the ``tool_calls`` embedded in
   the sub-agent conversations attached to ``agent_stop`` events. On a typical run
   this attributes the large majority of tool calls to a named specialist, the
   remainder being the CSO's own (exact figures depend on the session). Source (2)
   is SDK-version independent, which is what lets the same tooling audit sessions
   recorded before any of this existed.

Usage::

    from src.utils.provenance import build_provenance

    prov = build_provenance('logs/trace.jsonl')
    prov.agent_for('toolu_01ABC...')          # 'single-cell-analyst'
    prov.files_written()                      # {relpath: tool_use_id}
    prov.timeline()                           # ordered agent/tool events
    prov.write(run_dir / 'evidence' / 'provenance.json')
"""

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Iterable, Optional

CSO = "_cso"

#: Tools whose inputs name a file directly.
_PATH_INPUT_KEYS = ("file_path", "notebook_path", "path", "filename")

#: A file path appearing in a tool's *response*. The MCP servers write their
#: bulk results to disk via src.utils.output_manager and return the path, so
#: those artifacts are named nowhere in the tool input — only in the output.
_RESPONSE_PATH_RE = re.compile(
    r"[\w./\-]*/?([\w.\-]+\.(?:parquet|csv|tsv|h5ad|json|png|pdf|svg|xlsx|npz))\b"
)

#: A filename literal inside source code — matches the f-string form agents
#: overwhelmingly use, e.g. ``to_csv(f"{workspace}/il33_bulk_expression.csv")``.
_OUTPUT_LITERAL_RE = re.compile(
    r"['\"][^'\"]*?([\w./{}\-]+\.(?:csv|tsv|parquet|png|pdf|svg|jpg|jpeg|"
    r"h5ad|h5|xlsx|json|jsonl|md|txt|npz|pkl))['\"]"
)

#: Bash redirection / common write flags: `> out.csv`, `-o fig.png`, `--output x`.
#: The extension must start with a letter, otherwise numeric comparisons in shell
#: conditions (`awk '$3>0.5'`) get picked up as filenames.
_BASH_WRITE_RE = re.compile(
    r"(?:>>?|(?:-o|--out|--output|--outfile|--output-file)\s+)\s*"
    r"([\w./\-]+\.[A-Za-z][A-Za-z0-9]{0,7})\b"
)


class Provenance:
    """Per-run provenance index built from a trace."""

    def __init__(self, events: list[dict[str, Any]]):
        self.events = events
        self.agents: dict[str, dict[str, Any]] = {}       # agent_id -> info
        self._tool_agent: dict[str, str] = {}             # tool_use_id -> agent_type
        self.calls: dict[str, dict[str, Any]] = {}        # tool_use_id -> record
        self._script_outputs: dict[str, dict[str, Any]] = {}   # basename -> writing script
        self._build()

    # ── Construction ─────────────────────────────────────────────────

    def _build(self) -> None:
        # Pass 1 — sub-agent lifecycle, and the tool_use_ids each one owns.
        for ev in self.events:
            t = ev.get("type")
            if t == "agent_start":
                self.agents[ev.get("agent_id", "")] = {
                    "agent_id": ev.get("agent_id"),
                    "agent_type": ev.get("agent_type") or "unknown",
                    "start": ev.get("ts"),
                    "end": None,
                    "duration_s": None,
                    "cost": None,
                    "n_messages": 0,
                    "delegation_prompt": None,
                }
            elif t == "agent_stop":
                aid = ev.get("agent_id", "")
                info = self.agents.setdefault(aid, {
                    "agent_id": aid,
                    "agent_type": ev.get("agent_type") or "unknown",
                    "start": None,
                })
                conv = ev.get("conversation") or []
                info.update(
                    agent_type=ev.get("agent_type") or info.get("agent_type") or "unknown",
                    end=ev.get("ts"),
                    duration_s=ev.get("duration_s"),
                    cost=ev.get("cost"),
                    n_messages=len(conv),
                    transcript_path=ev.get("transcript_path"),
                )
                info["delegation_prompt"] = _first_user_message(conv)

                # The attribution step: every tool call inside this sub-agent's
                # conversation belongs to this sub-agent.
                for tuid in _tool_use_ids(conv):
                    self._tool_agent[tuid] = info["agent_type"]

        # Pass 2 — tool calls. Anything not claimed by a sub-agent is the CSO's.
        for ev in self.events:
            t = ev.get("type")
            if t not in ("tool_start", "tool_end", "tool_error"):
                continue
            tuid = ev.get("tool_use_id")
            if not tuid:
                continue
            rec = self.calls.setdefault(tuid, {
                "tool_use_id": tuid,
                "tool_name": ev.get("tool_name"),
                "agent": None,
                "started_at": None,
                "ended_at": None,
                "duration_ms": None,
                "is_error": False,
                "error": None,
                "tool_input": None,
                "files_written": [],
                "files_returned": [],
            })
            rec["tool_name"] = rec["tool_name"] or ev.get("tool_name")
            if ev.get("tool_input") is not None:
                rec["tool_input"] = ev["tool_input"]
            # Source (1): explicit agent on the event, when the trace carries it.
            if ev.get("agent") and ev["agent"] != "cso":
                rec["agent"] = ev["agent"]

            if t == "tool_start":
                rec["started_at"] = ev.get("ts")
            elif t == "tool_end":
                rec["ended_at"] = ev.get("ts")
                rec["duration_ms"] = ev.get("duration_ms")
                rec["is_error"] = bool(ev.get("is_error"))
                if not ev.get("is_error"):
                    rec["files_returned"] = extract_returned_paths(
                        rec.get("tool_name") or "", ev.get("tool_response")
                    )
            elif t == "tool_error":
                rec["ended_at"] = ev.get("ts")
                rec["duration_ms"] = ev.get("duration_ms")
                rec["is_error"] = True
                rec["error"] = ev.get("error")

        # Resolve attribution and extract written files.
        for tuid, rec in self.calls.items():
            rec["agent"] = rec["agent"] or self._tool_agent.get(tuid) or CSO
            rec["files_written"] = extract_written_paths(
                rec.get("tool_name") or "", rec.get("tool_input")
            )

    # ── Queries ──────────────────────────────────────────────────────

    def agent_for(self, tool_use_id: str) -> str:
        """Agent that made a given tool call; CSO if unclaimed by any sub-agent."""
        rec = self.calls.get(tool_use_id)
        return (rec or {}).get("agent") or self._tool_agent.get(tool_use_id) or CSO

    def has_tool_call(self, tool_use_id: str) -> bool:
        """Used by claims validation — an evidence pointer to a tool call that
        never happened must not be accepted."""
        return tool_use_id in self.calls

    def specialist_types(self) -> list[str]:
        return sorted({a["agent_type"] for a in self.agents.values() if a.get("agent_type")})

    def files_written(self, include_returned: bool = True) -> dict[str, str]:
        """{path -> tool_use_id}, last writer wins.

        Covers both files the agent named in a tool input and files an MCP server
        wrote on its behalf and named in the response.
        """
        out: dict[str, str] = {}
        for tuid, rec in sorted(self.calls.items(), key=lambda kv: kv[1].get("started_at") or ""):
            for p in rec["files_written"]:
                out[p] = tuid
            if include_returned:
                for p in rec.get("files_returned") or []:
                    out[p] = tuid
        return out

    def attribute_path(self, path, run_dir=None) -> tuple[Optional[str], Optional[str]]:
        """Best (agent, tool_use_id) for a written file.

        Matches on the full path first, then on basename — agents write a mix of
        absolute and relative paths, and Bash redirections are usually relative.
        """
        target = str(path)
        written = self.files_written()
        if target in written:
            tuid = written[target]
            return self.agent_for(tuid), tuid

        base = os.path.basename(target)
        for p, tuid in written.items():
            if os.path.basename(p) == base:
                return self.agent_for(tuid), tuid
        return None, None

    def index_script_outputs(self, script_paths: Iterable) -> None:
        """Index which agent-written script writes which output file.

        Agents here characteristically write a Python script and then run it, so
        the script's own outputs never appear in any tool input. But the scripts
        name them — typically as ``f"{workspace}/il33_bulk_expression.csv"`` — so
        a basename scan of the code recovers both the producing agent *and* the
        exact line that wrote the file. That line is the ``code`` evidence row an
        auditor most wants: it goes straight from a claim to the statement that
        produced its data.

        Call once, after code artifacts are known, before ``attribute_path``.
        """
        for sp in script_paths:
            sp = Path(sp)
            if sp.suffix.lower() not in (".py", ".r", ".sh", ".ipynb"):
                continue
            agent, _ = self.attribute_path(sp)
            try:
                text = sp.read_text(errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for m in _OUTPUT_LITERAL_RE.finditer(line):
                    base = os.path.basename(m.group(1))
                    # First writer wins: scripts are indexed in creation order, so
                    # `..._v2.py` does not displace the original author.
                    self._script_outputs.setdefault(base, {
                        "script": str(sp),
                        "line": lineno,
                        "agent": agent,
                        "statement": line.strip()[:200],
                    })

    def attribute_by_script(self, path) -> Optional[dict[str, Any]]:
        """The script (and line) that wrote a given output file, if known."""
        return self._script_outputs.get(os.path.basename(str(path)))

    def agents_active_at(self, ts: float) -> list[str]:
        """Agent types whose execution window contains the given epoch timestamp."""
        active = []
        for a in self.agents.values():
            start, end = _parse_ts(a.get("start")), _parse_ts(a.get("end"))
            if start is None:
                continue
            if start <= ts <= (end if end is not None else float("inf")):
                active.append(a.get("agent_type") or "unknown")
        return sorted(set(active))

    def attribute_by_mtime(self, mtime: float) -> tuple[Optional[str], str]:
        """Attribute a file to the agent running when it was written.

        This is the dominant case in this codebase: an agent writes a Python
        script and then runs it, and the script's own outputs (CSVs, figures)
        never appear in any tool input. Their mtime, however, falls inside that
        agent's execution window.

        Returns ``(agent, confidence)`` where confidence is:
          ``exact``     — one agent was running, so attribution is unambiguous;
          ``ambiguous`` — several ran concurrently; we return None rather than guess;
          ``none``      — no agent was running (the CSO, or written outside any span).

        Reporting ambiguity honestly matters: the CSO dispatches specialists in
        parallel, and a confident-looking wrong attribution is worse for an
        auditor than an acknowledged gap.
        """
        active = self.agents_active_at(mtime)
        if len(active) == 1:
            return active[0], "exact"
        if len(active) > 1:
            return None, "ambiguous"
        return None, "none"

    def timeline(self) -> list[dict[str, Any]]:
        """Ordered agent dispatches and tool calls — 'how the analysis flowed'."""
        rows: list[dict[str, Any]] = []
        for a in self.agents.values():
            if a.get("start"):
                rows.append({"ts": a["start"], "event": "agent_start",
                             "agent": a["agent_type"], "detail": ""})
            if a.get("end"):
                rows.append({"ts": a["end"], "event": "agent_stop",
                             "agent": a["agent_type"],
                             "detail": f"{a.get('duration_s')}s, {a.get('n_messages')} messages"})
        for rec in self.calls.values():
            if rec.get("started_at"):
                rows.append({
                    "ts": rec["started_at"],
                    "event": "tool_error" if rec["is_error"] else "tool",
                    "agent": rec["agent"],
                    "detail": rec.get("tool_name") or "",
                })
        rows.sort(key=lambda r: r["ts"] or "")
        return rows

    def tool_counts(self) -> dict[str, dict[str, int]]:
        """{agent: {tool_name: count}} — a quick read on what each agent actually did."""
        out: dict[str, dict[str, int]] = {}
        for rec in self.calls.values():
            out.setdefault(rec["agent"], {})
            name = rec.get("tool_name") or "?"
            out[rec["agent"]][name] = out[rec["agent"]].get(name, 0) + 1
        return out

    def summary(self) -> dict[str, Any]:
        attributed = sum(1 for r in self.calls.values() if r["agent"] != CSO)
        return {
            "n_subagents": len(self.agents),
            "specialists": self.specialist_types(),
            "n_tool_calls": len(self.calls),
            "n_attributed_to_specialist": attributed,
            "n_cso_tool_calls": len(self.calls) - attributed,
            "n_tool_errors": sum(1 for r in self.calls.values() if r["is_error"]),
            "n_files_written": len(self.files_written()),
        }

    # ── Serialisation ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "agents": list(self.agents.values()),
            "tool_calls": [
                self.calls[k] for k in sorted(
                    self.calls, key=lambda k: self.calls[k].get("started_at") or ""
                )
            ],
            "files_written": self.files_written(),
        }

    def write(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        return path


# ── Helpers ──────────────────────────────────────────────────────────

def _parse_ts(ts: Any) -> Optional[float]:
    """ISO timestamp (as written by TraceLogger) → epoch seconds."""
    if not ts:
        return None
    try:
        from datetime import datetime as _dt
        return _dt.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return None


def _first_user_message(conversation: Iterable[dict]) -> Optional[str]:
    """The CSO's delegation prompt — the first user turn in a sub-agent's transcript."""
    for m in conversation or []:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                return c
    return None


def _tool_use_ids(conversation: Iterable[dict]) -> list[str]:
    """Every tool_use_id appearing in a parsed sub-agent conversation."""
    ids = []
    for m in conversation or []:
        if not isinstance(m, dict):
            continue
        for tc in m.get("tool_calls") or []:
            if isinstance(tc, dict) and tc.get("id"):
                ids.append(tc["id"])
    return ids


def _coerce_input(tool_input: Any) -> dict:
    """Trace events store tool_input as a dict, or as its repr() after truncation.

    Older traces were written with `str(dict)`, so fall back to ast.literal_eval
    rather than dropping the event.
    """
    if isinstance(tool_input, dict):
        return tool_input
    if isinstance(tool_input, str):
        s = tool_input.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            import ast
            v = ast.literal_eval(s)
            return v if isinstance(v, dict) else {}
        except (ValueError, SyntaxError):
            return {}
    return {}


def extract_written_paths(tool_name: str, tool_input: Any) -> list[str]:
    """Files a tool call wrote, from its inputs.

    Exact for Write/Edit/NotebookEdit (the path is a named argument);
    heuristic for Bash, where we read redirections and `-o/--output` flags.
    The end-of-run `RunManifest.scan()` sweep is what catches whatever this misses,
    so a false negative costs attribution detail, never the artifact itself.
    """
    inp = _coerce_input(tool_input)
    if not inp:
        return []

    name = (tool_name or "").split("__")[-1]
    paths: list[str] = []

    if name in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        for k in _PATH_INPUT_KEYS:
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                paths.append(v.strip())

    elif name == "Bash":
        cmd = inp.get("command") or ""
        if isinstance(cmd, str):
            paths.extend(m.group(1) for m in _BASH_WRITE_RE.finditer(cmd))
            # `python script.py` — the script itself is a code artifact worth linking.
            try:
                toks = shlex.split(cmd)
            except ValueError:
                toks = cmd.split()
            for i, tok in enumerate(toks[:-1]):
                if tok in ("python", "python3") and toks[i + 1].endswith(".py"):
                    paths.append(toks[i + 1])

    # Dedupe, order-preserving.
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def extract_returned_paths(tool_name: str, tool_response: Any) -> list[str]:
    """Files named in a tool's *response*.

    The MCP servers persist bulk results through ``OutputManager`` and return the
    path rather than the payload, so those artifacts appear in no tool input at
    all. Without this, every MCP-written ``.parquet`` in a run is unattributable —
    which was the single largest gap when auditing real sessions.

    Restricted to ``mcp__*`` tools: a general Bash or Read response mentions
    plenty of paths it did not create, and a wrong attribution is worse than none.
    """
    if not (tool_name or "").startswith("mcp__"):
        return []
    s = tool_response if isinstance(tool_response, str) else str(tool_response or "")
    if not s:
        return []
    seen, out = set(), []
    for m in _RESPONSE_PATH_RE.finditer(s):
        full = m.group(0)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def read_trace(trace_path) -> list[dict[str, Any]]:
    """Read a trace.jsonl, skipping malformed lines rather than failing the audit."""
    events = []
    p = Path(trace_path)
    if not p.exists():
        return events
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def build_provenance(trace_path) -> Provenance:
    """Build the provenance index for a run from its trace.jsonl."""
    return Provenance(read_trace(trace_path))
