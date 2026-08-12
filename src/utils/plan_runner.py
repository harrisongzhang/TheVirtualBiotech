"""
Plan validation and planned-vs-actual reconciliation
The Virtual Biotech

Reviewer comment R2.5: *"There is no overall workflow manager to sequence the
analyses correctly."*

This is the answer, and it is deliberately plan-then-verify rather than a hard
scheduler. The CSO declares a DAG of steps before dispatching; the DAG is
validated on write (cycles, dangling dependencies, duplicate ids are rejected);
and after the run, the order actually observed is reconciled against the plan
with any deviation reported.

The alternative — a scheduler that owns dispatch and blocks steps until their
dependencies produce declared outputs — would be more rigid but would remove the
adaptive routing that is the system's whole point: a specialist's findings
routinely change which specialist should run next. Recording the plan, and
recording where reality diverged from it, makes the sequence auditable without
pretending the workflow is static. If stricter enforcement is wanted later,
``execution_order`` and ``reconcile`` are the hooks to build it on.

Usage::

    from src.utils.plan_runner import validate_plan, reconcile

    result = validate_plan(steps, goal="Assess IL-33 safety")
    if result.ok:
        manifest.data['plan'] = result.plan

    report = reconcile(manifest.data['plan'], manifest.data['execution'])
    report['deviations']    # steps skipped, reordered, or run unplanned
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

STEP_ID_MAX = 32


@dataclass
class PlanResult:
    plan: Optional[dict[str, Any]] = None
    order: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_plan(steps: Any, goal: str = "") -> PlanResult:
    """Validate an analysis DAG and compute a valid execution order.

    Rejects: missing/duplicate ids, references to steps that do not exist, and
    dependency cycles. Returns a topologically sorted order, so the caller knows
    the plan is executable — not merely well-formed.
    """
    res = PlanResult()

    if isinstance(steps, dict):
        goal = goal or steps.get("goal", "")
        steps = steps.get("steps", [])
    if not isinstance(steps, list) or not steps:
        res.errors.append("plan must be a non-empty list of steps")
        return res

    clean: list[dict[str, Any]] = []
    seen: set[str] = set()

    for i, raw in enumerate(steps):
        where = f"step[{i}]"
        if not isinstance(raw, dict):
            res.errors.append(f"{where}: not an object")
            continue

        sid = str(raw.get("id") or "").strip()
        if not sid:
            res.errors.append(f"{where}: missing 'id'")
            continue
        if len(sid) > STEP_ID_MAX:
            res.errors.append(f"{where}: id too long (max {STEP_ID_MAX})")
            continue
        if sid in seen:
            res.errors.append(f"step {sid}: duplicate id")
            continue
        seen.add(sid)

        agent = str(raw.get("agent") or "").strip()
        if not agent:
            res.errors.append(f"step {sid}: missing 'agent'")
            continue
        task = str(raw.get("task") or "").strip()
        if not task:
            res.warnings.append(f"step {sid}: no 'task' description")

        deps = raw.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list):
            res.errors.append(f"step {sid}: 'depends_on' must be a list")
            continue

        outs = raw.get("expected_outputs") or []
        if isinstance(outs, str):
            outs = [outs]

        clean.append({
            "id": sid,
            "agent": agent,
            "task": task,
            "depends_on": [str(d) for d in deps],
            "expected_outputs": [str(o) for o in outs],
        })

    if res.errors:
        return res

    ids = {s["id"] for s in clean}
    for s in clean:
        for d in s["depends_on"]:
            if d not in ids:
                res.errors.append(f"step {s['id']}: depends on unknown step {d!r}")
            elif d == s["id"]:
                res.errors.append(f"step {s['id']}: depends on itself")
    if res.errors:
        return res

    order = _topo_sort(clean)
    if order is None:
        res.errors.append(
            "plan contains a dependency cycle — no execution order exists. "
            "Check depends_on: every chain must terminate."
        )
        return res

    res.order = order
    res.plan = {
        "goal": goal,
        "created": datetime.now().isoformat(),
        "steps": clean,
        "valid_order": order,
        "parallel_groups": _parallel_groups(clean, order),
    }
    return res


def _topo_sort(steps: list[dict]) -> Optional[list[str]]:
    """Kahn's algorithm. Returns None when a cycle exists."""
    indeg = {s["id"]: 0 for s in steps}
    adj: dict[str, list[str]] = {s["id"]: [] for s in steps}
    for s in steps:
        for d in s["depends_on"]:
            adj[d].append(s["id"])
            indeg[s["id"]] += 1

    # Preserve declared order among ready steps, so the plan reads as written.
    ready = [s["id"] for s in steps if indeg[s["id"]] == 0]
    out: list[str] = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    return out if len(out) == len(steps) else None


def _parallel_groups(steps: list[dict], order: list[str]) -> list[list[str]]:
    """Steps grouped into waves that may run concurrently.

    Makes the intended concurrency explicit in the plan, so a reader can see
    which specialists were meant to run together rather than inferring it.
    """
    by_id = {s["id"]: s for s in steps}
    depth: dict[str, int] = {}
    for sid in order:
        deps = by_id[sid]["depends_on"]
        depth[sid] = 1 + max((depth[d] for d in deps), default=-1)
    groups: dict[int, list[str]] = {}
    for sid, d in depth.items():
        groups.setdefault(d, []).append(sid)
    return [groups[k] for k in sorted(groups)]


def reconcile(plan: Optional[dict], execution: Optional[list[dict]],
              manifest=None) -> dict[str, Any]:
    """Compare the declared plan against what actually ran.

    Deviation is reported, not treated as failure: the CSO is expected to adapt
    when a specialist's findings change what should happen next. What matters for
    an audit is that the divergence is visible rather than silent.

    Returns a dict with ``planned``, ``actual``, ``deviations`` and ``summary``.
    """
    out: dict[str, Any] = {
        "planned": [], "actual": [], "deviations": [], "summary": "",
    }
    if not plan:
        out["summary"] = "No plan was recorded for this run."
        return out

    steps = plan.get("steps", [])
    planned_agents = [s["agent"] for s in steps]
    actual_agents = [e.get("agent") for e in (execution or []) if e.get("agent")]
    out["planned"] = planned_agents
    out["actual"] = actual_agents

    planned_set, actual_set = set(planned_agents), set(actual_agents)

    for s in steps:
        if s["agent"] not in actual_set:
            out["deviations"].append({
                "kind": "not_run", "step": s["id"], "agent": s["agent"],
                "detail": f"Planned step {s['id']} ({s['agent']}) never ran.",
            })

    for a in actual_agents:
        if a not in planned_set:
            out["deviations"].append({
                "kind": "unplanned", "agent": a,
                "detail": f"{a} ran but was not in the plan.",
            })
            planned_set.add(a)   # report each unplanned agent once

    # Dependency order, checked against first-dispatch time.
    first = {}
    for i, a in enumerate(actual_agents):
        first.setdefault(a, i)
    by_id = {s["id"]: s for s in steps}
    for s in steps:
        for d in s["depends_on"]:
            dep = by_id.get(d)
            if not dep:
                continue
            if s["agent"] in first and dep["agent"] in first:
                if first[s["agent"]] < first[dep["agent"]]:
                    out["deviations"].append({
                        "kind": "out_of_order", "step": s["id"],
                        "detail": (f"{s['agent']} (step {s['id']}) started before "
                                   f"{dep['agent']} (step {d}), which it depends on."),
                    })

    # Declared outputs that never materialised.
    if manifest is not None:
        known = set(manifest.data.get("artifacts", {}))
        known_names = {Path(k).name for k in known}
        for s in steps:
            for o in s.get("expected_outputs") or []:
                if o not in known and Path(o).name not in known_names:
                    out["deviations"].append({
                        "kind": "missing_output", "step": s["id"],
                        "detail": f"Step {s['id']} declared output {o!r}, "
                                  f"which this run never produced.",
                    })

    n = len(out["deviations"])
    out["summary"] = (
        f"Plan followed exactly: {len(steps)} steps, no deviations."
        if n == 0 else
        f"{len(steps)} steps planned, {len(actual_agents)} dispatches observed, "
        f"{n} deviation(s) from the plan."
    )
    return out


def render_plan_md(plan: Optional[dict], report: Optional[dict] = None) -> str:
    """Markdown section describing the plan and any deviations, for the README."""
    if not plan:
        return ""
    L = ["## The analysis plan", ""]
    if plan.get("goal"):
        L += [f"**Goal:** {plan['goal']}", ""]
    L += ["| Step | Specialist | Depends on | Task |", "|---|---|---|---|"]
    for s in plan.get("steps", []):
        deps = ", ".join(s["depends_on"]) or "—"
        task = (s.get("task") or "").replace("|", "\\|").replace("\n", " ")
        if len(task) > 90:
            task = task[:90] + "…"
        L.append(f"| `{s['id']}` | `{s['agent']}` | {deps} | {task} |")
    L.append("")

    groups = plan.get("parallel_groups") or []
    if len(groups) > 1:
        L.append("Intended waves (steps in a wave may run concurrently): "
                 + " → ".join("[" + ", ".join(g) + "]" for g in groups))
        L.append("")

    if report:
        L += ["**Planned vs actual:** " + report["summary"], ""]
        if report["deviations"]:
            for d in report["deviations"]:
                L.append(f"- *{d['kind']}* — {d['detail']}")
            L.append("")
            L.append("> Deviations are expected when a specialist's findings change "
                     "what should happen next; they are recorded so the actual "
                     "sequence is auditable, not to flag an error.")
            L.append("")
    return "\n".join(L)
