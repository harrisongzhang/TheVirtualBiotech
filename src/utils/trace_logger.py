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
from datetime import datetime
from pathlib import Path
from typing import Any


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

    def __init__(self):
        self.events: list[dict[str, Any]] = []
        self._agent_t0: dict[str, float] = {}
        self._tool_t0: dict[str, float] = {}

    def _emit(self, etype: str, **kw) -> None:
        self.events.append({"type": etype, "ts": datetime.now().isoformat(), **kw})

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
        self._emit("tool_end", tool_use_id=tool_use_id, tool_name=tool_name,
                    tool_input=tool_input,
                    tool_response=_truncate(tool_response, 10_000),
                    is_error=is_error, duration_ms=dur, agent=agent)

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
        return [e["text"] for e in (events or self.events)
                if e["type"] == "thinking"]

    def extract_subagent_traces(self, events: list[dict] = None) -> list[dict]:
        """Build per-agent summaries from agent_start/stop pairs."""
        evs = events or self.events
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
        with open(path, "w") as f:
            for ev in self.events:
                f.write(json.dumps(ev, default=str) + "\n")


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
                            tresults.append({
                                "tool_use_id": blk.get("tool_use_id"),
                                "content": _truncate(
                                    blk.get("content", ""), max_tool_output),
                                "is_error": blk.get("is_error", False),
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
