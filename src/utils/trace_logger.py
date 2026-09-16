"""
Execution Trace Logger
The Virtual Biotech

Fine-grained execution traces for reviewer-grade audit trails:
sub-agent conversations, reasoning traces, tool calls with I/O, timing.

Outputs:
  - trace.jsonl  — one event per line, machine-readable
  - Structured dicts for embedding in cost_report.json
"""

import json
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils.run_storage import write_text_atomic
from src.utils.tool_errors import tool_result_error


def _truncate(obj: Any, max_chars: int) -> str:
    """Truncate to *max_chars* with a suffix noting total length."""
    s = str(obj) if not isinstance(obj, str) else obj
    if len(s) > max_chars:
        return s[:max_chars] + f"... [{len(s):,} chars total]"
    return s


class TraceLogger:
    """Accumulates structured execution events for agent traces.

    Usage::

        trace = TraceLogger()
        # ... register SDK hooks that call trace.agent_start/stop, etc. ...
        trace.write_jsonl(path)
    """

    def __init__(self, path: Path = None):
        self.events: list[dict[str, Any]] = []
        self._agent_t0: dict[str, float] = {}
        self._tool_t0: dict[str, float] = {}
        self._path = None
        self._lock = threading.RLock()
        if path is not None:
            self.bind(path)

    def bind(self, path: Path) -> None:
        """Persist events as they arrive, so in-flight claims can cite them."""
        with self._lock:
            self._path = Path(path)
            self.write_jsonl(self._path)

    def _emit(self, etype: str, **kw) -> None:
        event = {"type": etype, "ts": datetime.now().isoformat(), **kw}
        with self._lock:
            self.events.append(event)
            if self._path is not None:
                with self._path.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(event, default=str) + '\n')

    # ── Agent lifecycle ──────────────────────────────────────────────

    def agent_start(self, agent_id: str, agent_type: str):
        self._agent_t0[agent_id] = time.monotonic()
        self._emit("agent_start", agent_id=agent_id, agent_type=agent_type)

    def agent_stop(self, agent_id: str, agent_type: str,
                   transcript_path: str = None, conversation: list = None,
                   cost: dict = None):
        t0 = self._agent_t0.pop(agent_id, None)
        dur = round(time.monotonic() - t0, 2) if t0 is not None else None
        self._emit("agent_stop", agent_id=agent_id, agent_type=agent_type,
                    duration_s=dur, transcript_path=transcript_path,
                    conversation=conversation, cost=cost)

    # ── Reasoning ────────────────────────────────────────────────────

    def thinking(self, text: str, agent: str = "cso"):
        self._emit("thinking", agent=agent, text=text)

    # ── Tool calls ───────────────────────────────────────────────────

    def tool_start(self, tool_use_id: str, tool_name: str, tool_input: dict,
                   agent: str = None):
        self._tool_t0[tool_use_id] = time.monotonic()
        self._emit("tool_start", tool_use_id=tool_use_id,
                    tool_name=tool_name, tool_input=tool_input, agent=agent)

    def tool_end(self, tool_use_id: str, tool_name: str, tool_input: dict,
                 tool_response: Any, is_error: bool = False, agent: str = None):
        t0 = self._tool_t0.pop(tool_use_id, None)
        dur = round((time.monotonic() - t0) * 1000, 1) if t0 is not None else None
        error = tool_result_error(tool_response)
        self._emit("tool_end", tool_use_id=tool_use_id, tool_name=tool_name,
                    tool_input=tool_input,
                    tool_response=_truncate(tool_response, 10_000),
                    is_error=bool(is_error or error), error=error,
                    duration_ms=dur, agent=agent)

    def tool_error(self, tool_use_id: str, tool_name: str, tool_input: dict,
                   error: str, agent: str = None):
        t0 = self._tool_t0.pop(tool_use_id, None)
        dur = round((time.monotonic() - t0) * 1000, 1) if t0 is not None else None
        self._emit("tool_error", tool_use_id=tool_use_id, tool_name=tool_name,
                    tool_input=tool_input, error=error, duration_ms=dur, agent=agent)

    # ── Queries ──────────────────────────────────────────────────────

    def events_since(self, idx: int) -> list[dict]:
        """Return events from *idx* to now (for per-turn slicing)."""
        return self.events[idx:]

    def extract_thinking(self, events: list[dict] = None) -> list[str]:
        """Extract thinking/reasoning texts from events."""
        return [e["text"] for e in (self.events if events is None else events)
                if e["type"] == "thinking"]

    def extract_subagent_traces(self, events: list[dict] = None) -> list[dict]:
        """Build per-agent summaries from agent_start/stop pairs."""
        evs = self.events if events is None else events
        pending: dict[str, dict] = {}
        traces: list[dict] = []
        for ev in evs:
            if ev["type"] == "agent_start":
                pending[ev["agent_id"]] = {
                    "agent_type": ev["agent_type"],
                    "agent_id": ev["agent_id"],
                    "start_time": ev["ts"],
                }
            elif ev["type"] == "agent_stop":
                info = pending.pop(ev["agent_id"], {})
                info.update(
                    agent_type=ev["agent_type"],
                    agent_id=ev["agent_id"],
                    duration_s=ev.get("duration_s"),
                    end_time=ev["ts"],
                    conversation=ev.get("conversation", []),
                    cost=ev.get("cost"),
                )
                # Extract the CSO's delegation prompt (first user message)
                conv = info["conversation"]
                first_user = next(
                    (m.get("content", "") for m in conv
                     if isinstance(m, dict) and m.get("role") == "user"),
                    None,
                )
                if first_user:
                    info["delegation_prompt"] = first_user
                traces.append(info)
        return traces

    # ── Serialisation ────────────────────────────────────────────────

    def write_jsonl(self, path: Path) -> None:
        """Write all events as newline-delimited JSON."""
        with self._lock:
            write_text_atomic(path, ''.join(json.dumps(ev, default=str) + '\n'
                                           for ev in self.events))


def agents_in_events(events: list[dict]) -> list[str]:
    """Ordered agent identities from lifecycle hooks and both dispatch formats."""
    agents = []
    for event in events:
        agent = event.get('agent_type') or event.get('agent')
        if event.get('tool_name') in ('Task', 'Agent'):
            data = event.get('tool_input') or {}
            agent = data.get('subagent_type') or data.get('agent_type') or agent
        if agent and agent not in ('cso', '_cso', 'unknown') and agent not in agents:
            agents.append(agent)
    return agents


def tool_failures(events: list[dict]) -> list[dict]:
    """All observed operational failures, including attempts later recovered."""
    failures = {}
    for event in events:
        if event.get('type') != 'tool_error' and not event.get('is_error'):
            continue
        key = event.get('tool_use_id') or str(len(failures))
        failures[key] = {
            'tool_use_id': event.get('tool_use_id'),
            'tool_name': event.get('tool_name', 'unknown'),
            'error': str(event.get('error') or event.get('tool_response') or 'Tool failed')[:2000],
        }
    return list(failures.values())


def unresolved_tool_failures(events: list[dict]) -> list[dict]:
    """Failures without a later successful call of the same tool and arguments.

    A successful retry restores that query's availability without deleting its
    failed attempt from the trace. Different inputs cannot resolve one another:
    successful retrieval for another target does not establish the first result.
    """
    pending = {}
    started = {}
    for event in events:
        kind = event.get('type')
        identifier = event.get('tool_use_id')
        if kind == 'tool_start':
            started[identifier] = event
            continue
        if kind not in ('tool_end', 'tool_error'):
            continue
        initial = started.get(identifier, {})
        name = event.get('tool_name') or initial.get('tool_name', 'unknown')
        inputs = event.get('tool_input')
        if inputs is None:
            inputs = initial.get('tool_input') or {}
        key = (name, json.dumps(inputs, sort_keys=True, default=str))
        if kind == 'tool_error' or event.get('is_error'):
            pending[key] = {
                'tool_use_id': identifier, 'tool_name': name,
                'error': str(event.get('error') or event.get('tool_response') or 'Tool failed')[:2000],
            }
        else:
            pending.pop(key, None)
    return list(pending.values())


# ── Per-agent cost from transcript usage data ────────────────────────

# Current Claude API pricing (update as pricing changes)
_PRICING = {
    "claude-sonnet-4-5-20250929": {
        "input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000,
        "cache_write": 3.75 / 1_000_000, "cache_read": 0.30 / 1_000_000,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.0 / 1_000_000, "output": 5.0 / 1_000_000,
        "cache_write": 1.25 / 1_000_000, "cache_read": 0.10 / 1_000_000,
    },
}
# Fallback: use Sonnet pricing for unknown models
_DEFAULT_PRICING = _PRICING["claude-sonnet-4-5-20250929"]


def compute_agent_cost(transcript_path: str) -> dict[str, Any] | None:
    """Compute exact cost for a sub-agent from its transcript usage data.

    Returns dict with model, tokens breakdown, and cost_usd — or None if
    the transcript is missing or has no usage data.
    """
    path = Path(transcript_path)
    if not path.exists() or path.stat().st_size == 0:
        return None

    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    model = None
    msg_count = 0

    try:
        with open(path) as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = entry.get("message", {})
                if msg.get("role") != "assistant":
                    continue
                usage = msg.get("usage")
                if not usage:
                    continue
                model = model or msg.get("model")
                totals["input"] += usage.get("input_tokens", 0)
                totals["output"] += usage.get("output_tokens", 0)
                totals["cache_read"] += usage.get("cache_read_input_tokens", 0)
                totals["cache_write"] += usage.get("cache_creation_input_tokens", 0)
                msg_count += 1
    except Exception:
        return None

    if msg_count == 0:
        return None

    p = _PRICING.get(model, _DEFAULT_PRICING)
    cost = (totals["input"] * p["input"]
            + totals["output"] * p["output"]
            + totals["cache_read"] * p["cache_read"]
            + totals["cache_write"] * p["cache_write"])

    return {
        "model": model,
        "messages": msg_count,
        "tokens": totals,
        "cost_usd": round(cost, 6),
    }


# ── Transcript parser ────────────────────────────────────────────────

def parse_agent_transcript(transcript_path: str,
                           max_tool_output: int = 3000) -> list[dict]:
    """Parse a Claude SDK JSONL transcript into a structured conversation.

    Each item has ``role`` plus optional ``content``, ``thinking``,
    ``tool_calls``, ``tool_results``.
    """
    path = Path(transcript_path)
    if not path.exists() or path.stat().st_size == 0:
        return []

    conversation: list[dict] = []
    try:
        with open(path) as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = entry.get("message", {})
                role = msg.get("role")
                if not role:
                    continue

                record: dict[str, Any] = {"role": role}
                content = msg.get("content", "")

                if isinstance(content, str):
                    if content:
                        record["content"] = content
                elif isinstance(content, list):
                    texts: list[str] = []
                    thinks: list[str] = []
                    tcalls: list[dict] = []
                    tresults: list[dict] = []
                    for blk in content:
                        if not isinstance(blk, dict):
                            continue
                        bt = blk.get("type", "")
                        if bt == "text":
                            texts.append(blk.get("text", ""))
                        elif bt == "thinking":
                            thinks.append(blk.get("thinking", ""))
                        elif bt == "tool_use":
                            tcalls.append({
                                "id": blk.get("id"),
                                "name": blk.get("name"),
                                "input": blk.get("input"),
                            })
                        elif bt == "tool_result":
                            raw_result = blk.get("content", "")
                            # Determine semantics before rendering/truncation:
                            # repr(list/dict) is no longer a parseable JSON
                            # envelope, and large results may lose the error.
                            error = tool_result_error(raw_result)
                            tresults.append({
                                "tool_use_id": blk.get("tool_use_id"),
                                "content": _truncate(
                                    raw_result, max_tool_output),
                                "is_error": bool(blk.get("is_error", False) or error),
                                "error": error,
                            })
                    if texts:
                        record["content"] = "\n".join(texts)
                    if thinks:
                        record["thinking"] = "\n---\n".join(thinks)
                    if tcalls:
                        record["tool_calls"] = tcalls
                    if tresults:
                        record["tool_results"] = tresults

                # Only keep records that have something beyond just the role
                if len(record) > 1:
                    conversation.append(record)
    except Exception:
        pass  # never crash the caller on transcript parse errors

    return conversation
