---
name: run-organization
description: How to organise a session run so a human can audit it — directory layout, artifact naming, recording the analysis plan, filing claim-evidence objects, and the end-of-run checklist. Use when orchestrating specialists (the CSO role), whenever you are about to dispatch work or synthesise findings, or when you need to know where an artifact belongs.
---

# Run Organisation

## Why this exists

A reviewer ran the system, got a pile of files, and could not tell how they were
organised, which artifact supported which claim from which specialist, or how the
analysis flowed together. They also could not replay it.

That is a fair description of what an unmanaged run produces. This skill is the
standard that prevents it. The rule behind every instruction here:

> **Every run must be understandable by someone who was not there, opening the
> directory cold, six months later.**

## The run directory

Every session gets one directory. You are working inside it now.

```
runs/<RUN_ID>/
├── MANIFEST.json      every artifact: hash, producing agent, producing tool call
├── README.md          generated map of the run — do not hand-edit
├── audit.html         self-contained report for someone with no environment
├── inputs/
│   ├── query.txt      the user's turns, verbatim
│   ├── plan.json      the analysis DAG you declared      ← you write this
│   └── config.json    models, prompt hashes, MCP servers, git commit
├── work/<agent>/      one subtree per specialist — never flat
│   ├── code/scripts/
│   ├── data/{raw,processed}/
│   └── results/{figures,tables,reports}/
├── evidence/
│   ├── claims.json    claim → evidence                    ← you write this
│   └── provenance.json  tool call → agent, derived from the trace
├── logs/              trace.jsonl, cost_report.json, transcript.md
└── report/FINAL_REPORT.md
```

`MANIFEST.json`, `provenance.json`, `README.md` and `audit.html` are generated.
Your two responsibilities are **`plan.json`** and **`claims.json`**.

## 1. Declare the plan before dispatching

Before dispatching two or more specialists, call `mcp__provenance__write_plan`.

```python
write_plan(
  goal="Assess the safety risk of targeting IL-33 in asthma",
  steps=[
    {"id": "s1", "agent": "single-cell-analyst",
     "task": "IL33/IL1RL1 expression across lung and critical-organ cell types",
     "depends_on": [], "expected_outputs": ["il33_celltype_expression.csv"]},
    {"id": "s2", "agent": "fda-safety-officer",
     "task": "Clinical precedent AEs read against the expression profile",
     "depends_on": ["s1"]},
  ])
```

- `depends_on: []` → can start immediately. Steps that do not depend on each
  other are dispatched in parallel.
- Use `depends_on` **only for real data dependencies**. Over-declaring
  serialises work that could have run concurrently and makes the run slower for
  no auditing benefit.
- The plan is validated on write: cycles, unknown ids and duplicates are
  rejected.
- **Deviating is allowed and expected.** If a specialist's findings change what
  should happen next, do that — deviations are recorded, not forbidden. If the
  change is substantial, call `write_plan` again with the revised plan.
- Skip the plan for single-specialist queries and clarification exchanges.

## 2. Keep artifacts in the right place

Each specialist writes only under `work/<its-own-name>/`. Its prompt tells it so,
and the system records anything written elsewhere and attributes it anyway — but a
file in the wrong place is still a file the next reader has to puzzle over.

When you delegate, if a specialist needs an earlier one's output, **give it the
path**:

```
Load the expression table from
work/single-cell-analyst/results/tables/il33_celltype_expression.csv
```

Do not tell a specialist to "check the workspace" and hope. Name the file.

### Naming

Name for content, never for sequence or status.

| Good | Bad | Why |
|---|---|---|
| `il33_celltype_expression.csv` | `analysis2.csv` | says what is in it |
| `gwas_credible_sets_chr9.parquet` | `cs_2fd0.parquet` | readable six months later |
| `safety_ae_summary.md` | `results_final_v3.md` | "final v3" tells a reader nothing |

If you find yourself appending `_v2`, the first file was either superseded (say
so in its description) or the two differ in a way the name should state.

## 3. File claim-evidence objects

Every substantive factual assertion in your synthesis becomes a claim with the
evidence behind it. See the `evidence-citation` skill for the specialist-side
contract that produces citable artifacts in the first place.

**Sequence:**

1. `mcp__provenance__list_artifacts` — get the exact paths. Do not guess a
   filename; a path that does not exist is rejected.
2. Write your synthesis with inline anchors:
   `...highly expressed in lung mast cells[[claim:C1]]...`
3. `mcp__provenance__record_claims` with the claim objects.

```python
record_claims(claims=[
  {"id": "C1",
   "text": "IL1RL1 is most highly expressed in lung mast cells (mean 2.4 CPM)",
   "agent": "single-cell-analyst",
   "confidence": "strong",
   "evidence": [
     {"kind": "table",
      "path": "work/single-cell-analyst/results/tables/il33_celltype_expression.csv",
      "note": "row: mast cell"},
     {"kind": "figure",
      "path": "work/single-cell-analyst/results/figures/il33_celltype.png"}]}])
```

**Rules that matter:**

- Evidence is validated on write. `ok: false` means a path or tool id is wrong —
  fix it and call again.
- **Never resolve a rejection by deleting the evidence.** A claim you cannot
  support is a finding: state it in prose as unsupported or uncertain. Filing an
  unsupported claim as though it were supported is the specific failure this
  whole mechanism exists to prevent.
- `confidence`: `strong` = direct measurement; `moderate` = inference;
  `weak` = suggestive or indirect.
- Every `[[claim:Cn]]` anchor must correspond to a filed claim. A dangling anchor
  renders as a visibly broken marker and is reported as a defect in the README.

## 4. End-of-run checklist

Before your final response:

- [ ] `write_plan` called, if two or more specialists ran
- [ ] Every substantive assertion carries a `[[claim:Cn]]` anchor
- [ ] `record_claims` returned `ok: true` for all of them
- [ ] `list_artifacts` shows no unexplained files — anything a specialist produced
      that carries a finding should have a description
- [ ] Anything you could not establish is stated as a gap, not omitted

## Anti-patterns

**Filing a claim with weak evidence to clear the checklist.** The confidence
field exists so you can be honest. `weak` with a real artifact beats `strong`
with a stretched one.

**Citing a specialist's prose.** If a specialist asserted a number but wrote no
file, there is nothing to cite. Report it as an unsupported statement, or send
the specialist back to produce the artifact.

**One claim covering a whole paragraph.** One claim = one checkable assertion. If
the text spans three findings from two specialists, that is three claims.

**Silently dropping a failed analysis.** A specialist that timed out or returned
nothing is part of the run's story. Say so, and adjust your confidence.
