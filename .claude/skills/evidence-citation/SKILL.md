---
name: evidence-citation
description: How to report findings so they can be cited — writing evidence to files before asserting it, choosing where outputs belong, describing what each artifact shows, and returning findings with machine-checkable evidence pointers. Use when you are a specialist producing analysis for the CSO, before writing your final response, or when deciding whether a result needs to be saved.
---

# Evidence Citation

## Why this exists

Your findings become claims in the CSO's answer to the user. Each claim is
clickable: a reader opens it and sees the table, figure, code line or tool call
behind it. The CSO can only build that from what you hand it, and **it must not
invent citations** — the system rejects evidence pointers that do not resolve.

So a finding you report without evidence is one the CSO has to present as
unsupported. Everything below is about making sure that does not happen to work
you actually did.

## 1. Write it down before you assert it

A number that exists only in your prose cannot be cited.

Before stating a result, save the thing that shows it:

| You want to say | Save |
|---|---|
| "highest in mast cells" | the per-cell-type table |
| "L2G score 0.75" | the credible-set table you read it from |
| "expression is bimodal" | the plot |
| "no black-box warning" | the query result |

This is not bookkeeping. If the only record of a result is a sentence, nobody can
check it, and in six months neither can you.

## 2. Put it in the right place

You have your own directory. Use the layout:

```
work/<your-agent-name>/
├── code/scripts/         analysis scripts you write
├── data/raw/             data as pulled from a tool or database
├── data/processed/       data after your QC / transformation
└── results/
    ├── figures/          plots (.png, .pdf)
    ├── tables/           result tables (.csv, .tsv, .parquet)
    └── reports/          your written findings (.md)
```

Absolute paths, always. Do not write into another specialist's directory or into
the run root.

**Name for content, not sequence.** `il33_celltype_expression.csv`, not
`analysis2.csv`. `gwas_credible_sets_chr9.parquet`, not `cs_2fd0.parquet`. Every
file you leave behind is something a human has to interpret without you there.

## 3. Say what each output shows

The system records every file you write automatically — you do not need to do
anything for an artifact to exist. What it cannot infer is what the file
*demonstrates*. For each output that carries a finding:

```python
register_artifact(
  path="results/tables/il33_celltype_expression.csv",
  description="Mean IL1RL1 expression per lung cell type across 12 donors; "
              "mast cells highest at 2.4 CPM")
```

One line. What is in it, and what it shows.

## 4. Return findings with evidence attached

Structure your response to the CSO so each substantive finding carries its
evidence:

```
FINDING: IL1RL1 is most highly expressed in lung mast cells (mean 2.4 CPM,
         3.1x the next-highest cell type).
EVIDENCE: results/tables/il33_celltype_expression.csv (row: mast cell)
          results/figures/il33_celltype.png
CONFIDENCE: strong — direct measurement, n=12 donors, consistent across 3 datasets

FINDING: Expression in cardiac tissue is low but non-zero.
EVIDENCE: results/tables/il33_critical_organs.csv (rows: cardiac_*)
CONFIDENCE: moderate — only 2 donors with cardiac tissue; wide CI
```

Paths relative to your own directory are fine — the CSO resolves them.

If a specific tool call is the evidence (an MCP query whose result you are
reporting directly rather than a file you wrote), give its `tool_use_id`.

## 5. Report what you could not establish

If a query failed, returned nothing, or the evidence is too thin to support a
conclusion — that is your result. Say it:

```
NOT ESTABLISHED: Cardiac expression could not be assessed. The Census query for
heart tissue returned 0 cells after QC filtering. Retried with relaxed filters
(min_genes 200 → 100), still 0. This gap should be closed before a cardiac
safety claim is made either way.
```

An acknowledged gap is usable — the CSO can scope its answer around it, or send
someone back for it. A confident claim with nothing behind it is worse than
silence, because it looks the same as a supported one until someone checks.

## Checklist before you respond

- [ ] Every number I state exists in a file I wrote
- [ ] Files are under my own `work/<agent>/` tree, in the right subdirectory
- [ ] Filenames say what the files contain
- [ ] `register_artifact` called for each output carrying a finding
- [ ] Each finding in my response has an EVIDENCE line
- [ ] Confidence stated honestly, with the reason
- [ ] Anything I could not establish is stated, not omitted

## Anti-patterns

**Reporting a summary statistic without saving the underlying table.** The
summary is the claim; the table is the evidence. Both are needed.

**`results_final_v3.csv`.** If there are three versions, either two are dead —
delete them — or they differ in a way the filename should say.

**Burying the finding in a long narrative.** The CSO extracts claims from what
you return. A clearly delimited FINDING/EVIDENCE/CONFIDENCE block survives that
extraction; a paragraph where the result is in the fourth sentence may not.

**Overstating confidence to sound useful.** `moderate` with an honest reason is
more useful than `strong` that a reviewer later has to walk back.
