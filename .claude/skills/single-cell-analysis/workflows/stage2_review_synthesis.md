# Stage 3: Critical Review & Synthesis

## Overview

Stage 3 is the quality assurance and finalization phase. **All three sub-stages (3A, 3B, 3C) are MANDATORY with no exceptions.**

**Time allocation:** 20% of total analysis time

**Critical requirement:** Cannot skip or reorder sub-stages. Must complete 3A → 3B → 3B-Action → 3C in sequence.

---

## ⛔ STAGE 3 IS MANDATORY ⛔

**YOU MUST NOT:**
- Skip any sub-stage
- Create FINAL_REPORT.md before DRAFT and CRITICAL_REVIEW.md
- Proceed to 3C without completing 3B-Action

**YOU MUST:**
- Complete all three sub-stages in order
- Follow the review procedure exactly
- Address all review feedback before finalizing

---

## Stage 3 Checklist

Copy this checklist and check off items as you complete them:

```
Stage 3 Progress:
- [ ] Stage 3A: analysis_report_DRAFT.md created
- [ ] Stage 3B: Read review procedure
- [ ] Stage 3B: Adopted Dr. Reviewer persona
- [ ] Stage 3B: Evaluated all 5 categories
- [ ] Stage 3B: CRITICAL_REVIEW.md created with decision
- [ ] Stage 3B-Action: Review feedback addressed based on decision
- [ ] Stage 3C: File existence validated
- [ ] Stage 3C: FINAL_REPORT.md created
```

---

## Stage 3A: Generate Draft Report

**Objective:** Create initial analysis report with all findings.

### Create Draft Report File

**File:** `results/reports/analysis_report_DRAFT.md`

**Required structure:**

```markdown
# [Analysis Title]: Single-Cell RNA-seq Analysis

**Date:** YYYY-MM-DD
**Run ID:** {run_id}
**Status:** DRAFT - Pending Critical Review

---

## Executive Summary

[2-3 sentence overview of key findings]

---

## 1. Data Summary

### 1.1 Dataset Overview
- Data source: CELLxGENE Census
- Disease data: X cells from Y datasets
- Healthy data: X cells from Y datasets
- Total cells analyzed: X

### 1.2 Quality Control Metrics
- Median UMI counts: X (disease), Y (healthy)
- Median genes per cell: X (disease), Y (healthy)
- QC filters applied: min_genes=300, max_genes=9000, max_mt=15%
- Doublet removal: X% disease, Y% healthy
- Subsampling: [if applicable, describe stratified subsampling]

---

## 2. Cell Type Landscape

### 2.1 Cell Type Composition
[Table showing cell types and counts in disease vs healthy]

### 2.2 Key Observations
- Novel or rare populations enriched in disease
- Cell type composition changes
- [Include visualizations: UMAPs, composition plots]

---

## 3. Differential Expression Results

### 3.1 Methods
- **Approach:** Pseudobulk aggregation (donor x condition) using pandas groupby
- **Statistical test:** PyDESeq2 negative binomial model (optimized: n_cpus=8, fit_type='mean', refit_cooks=False)
- **Sample size:** N disease donors vs M healthy donors (from single matched dataset)
- **Significance criteria:** FDR < 0.05, |log2FC| > 0.5

[OR if donor_id missing, document Wilcoxon limitation]

### 3.2 Results by Cell Type

**[Cell Type 1]:**
- Significant genes: X (Y upregulated, Z downregulated)
- Top upregulated: [gene list with log2FC and FDR]
- Top downregulated: [gene list]
- [Include volcano plot, heatmap]

**[Cell Type 2]:**
[Repeat for each analyzed cell type]

---

## 4. Pathway Enrichment Results

### 4.1 Methods
- **Tool:** gseapy GSEA (databases: Hallmark, KEGG, Reactome)
- **Significance criteria:** FDR < 0.05, |NES| > 1.5

### 4.2 Results by Cell Type

**[Cell Type 1]:**
- Top enriched pathways: [list with NES and FDR]
- Biological interpretation: [key pathway themes]

**[Cell Type 2]:**
[Repeat for each cell type]

---

## 5. Therapeutic Target Recommendations

### 5.1 Prioritized Targets
[Table with gene, cell type, expression changes, druggability]

### 5.2 Mechanistic Insights
[Biological interpretation of findings]

---

## 6. Limitations

[Document any limitations:]
- Sample size considerations
- Missing donor metadata (if applicable)
- Cell types not analyzed (if too few cells)
- Technical constraints
- Biological caveats

---

## 7. Data Provenance

**Data source:**
- CELLxGENE Census queries: [specific value_filter used]

**Analysis files:**
- Scripts: `workspace/{date}/{run_id}/single_cell_analyst/code/scripts/`
- Processed data: `workspace/{date}/{run_id}/single_cell_analyst/data/processed/`
- Results: `workspace/{date}/{run_id}/single_cell_analyst/results/`

**Software versions:**
- scanpy: [version]
- PyDESeq2: 0.5.2
- gseapy: [version]
- pandas: [version]
- Harmony: [version]

---

**END OF DRAFT REPORT**
```

### Save Draft Report

```python
with open(report_dir / 'analysis_report_DRAFT.md', 'w') as f:
    f.write(draft_content)

print("✓ DRAFT report created: analysis_report_DRAFT.md")
```

---

## Stage 3B: Independent Critical Review

**⛔ MANDATORY - This step CANNOT be skipped ⛔**

### Prerequisites

Before starting review, verify draft exists:

```python
from pathlib import Path

draft_exists = (report_dir / 'analysis_report_DRAFT.md').exists()

if not draft_exists:
    raise ValueError("❌ DRAFT_REPORT.md not found - complete Stage 3A first")

print("✅ DRAFT report exists - proceeding with critical review")
```

### Adopt Dr. Reviewer Persona

**Switch your mindset:** You are now **Dr. Reviewer**, a senior scientist evaluating this work objectively. Your goal is to identify flaws, overclaimed results, or missing controls.

### Follow Review Procedure

**See [procedures/review_procedure.md](.claude/skills/single-cell-analysis/procedures/review_procedure.md)**

**The procedure will guide you through:**
- 5-category evaluation framework
- Checklist for each category
- Classification of issues (major vs minor)
- Decision criteria (REJECT / REVISE / APPROVE)
- Creating CRITICAL_REVIEW.md with structured format

### Create Critical Review Report

**File:** `results/reports/CRITICAL_REVIEW.md`

**Required format:**

```markdown
# Technical Review: [Analysis Title]

**Reviewer:** Dr. Reviewer
**Date:** YYYY-MM-DD
**Analysis Run ID:** {run_id}

---

## Overall Assessment: [REJECT / REVISE / APPROVE]

---

## Category Evaluation

| Category | Status | Issues |
|----------|--------|--------|
| Data Quality | [✅/⚠️/🔴] | [number] |
| Clustering & Annotation | [status] | [number] |
| Statistical Rigor | [status] | [number] |
| Biological Interpretation | [status] | [number] |
| Reproducibility | [status] | [number] |

---

## Detailed Findings

### 🔴 MAJOR ISSUES (Require Re-Analysis)

[List each major issue with:]
- Problem description
- Location (file:line or report section)
- Impact
- Required action

### ⚠️ MINOR ISSUES (Require Caveats)

[List each minor issue with:]
- Problem description
- Impact
- Suggested action (caveat to add)

### ✅ STRENGTHS

[Positive aspects - reinforce good practices]

---

## Recommendation: [REJECT / REVISE / APPROVE]

**Justification:**
[Explain the decision]

**Next steps:**
[Specific actions required]

---

**END OF CRITICAL REVIEW**
```

### Save Critical Review

```python
with open(report_dir / 'CRITICAL_REVIEW.md', 'w') as f:
    f.write(review_content)

print("✓ CRITICAL_REVIEW.md created")
```

---

## Stage 3B-Action: Address Review Feedback

**Required action depends on review decision.**

### IF REJECT:

**Action:** Return to Stage 2 and fix all major issues.

```
1. Document all major issues from review
2. Return to Stage 2 workflows
3. Fix identified problems:
   - Re-run analyses with corrections
   - Update scripts
   - Regenerate results
4. Update DRAFT_REPORT.md with corrected results
5. Return to Stage 3B (re-review)
```

**Do not proceed to 3C until review approves.**

### IF REVISE:

**Action:** Add caveats to draft report for each minor issue.

```
1. Open analysis_report_DRAFT.md
2. For each minor issue:
   - Add caveat to relevant section
   - Soften language where appropriate
   - Update limitations section
3. Save updated draft
4. Proceed to Stage 3C
```

**Example caveat:**
```markdown
## Limitations

**Sample size**: Differential expression tested with N=4 disease donors vs N=3
healthy donors. While statistically significant results were obtained, larger
sample sizes would increase confidence in findings.
```

### IF APPROVE:

**Action:** Proceed directly to Stage 3C.

```
No changes needed - analysis quality is high.
Proceed to create final report.
```

---

## Stage 3C: Create Final Report

**⛔ MANDATORY GATE: Cannot start until 3A and 3B complete ⛔**

### Validate Prerequisites

**Before writing code for FINAL_REPORT.md, run this validation:**

```python
from pathlib import Path

draft_exists = (report_dir / 'analysis_report_DRAFT.md').exists()
review_exists = (report_dir / 'CRITICAL_REVIEW.md').exists()

print(f"\n[Stage 3C Prerequisites]")
print(f"  DRAFT exists: {draft_exists}")
print(f"  REVIEW exists: {review_exists}")

if not draft_exists:
    raise ValueError("❌ Cannot create FINAL - missing DRAFT (complete Stage 3A)")

if not review_exists:
    raise ValueError("❌ Cannot create FINAL - missing REVIEW (complete Stage 3B)")

print("✅ Prerequisites met - proceeding with FINAL report")
```

### Create Final Report

**File:** `results/reports/FINAL_REPORT.md` (NEW file, not editing draft)

**Content:**
- Copy content from DRAFT_REPORT.md
- Incorporate ALL revisions from Stage 3B-Action
- Update status from "DRAFT" to "FINAL REPORT"
- Ensure all caveats from review are included
- Verify all limitations documented

```python
# Read draft
with open(report_dir / 'analysis_report_DRAFT.md', 'r') as f:
    draft_content = f.read()

# Update status
final_content = draft_content.replace(
    'Status:** DRAFT - Pending Critical Review',
    'Status:** FINAL REPORT'
)

# Add any additional revisions from 3B-Action here

# Save final report
with open(report_dir / 'FINAL_REPORT.md', 'w') as f:
    f.write(final_content)

print("✓ FINAL_REPORT.md created")
```

---

## Stage 3 Completion Validation

Verify all 3 report files exist:

```bash
ls workspace/{date}/{run_id}/single_cell_analyst/results/reports/analysis_report_DRAFT.md
ls workspace/{date}/{run_id}/single_cell_analyst/results/reports/CRITICAL_REVIEW.md
ls workspace/{date}/{run_id}/single_cell_analyst/results/reports/FINAL_REPORT.md
```

**Expected output:**
```
analysis_report_DRAFT.md
CRITICAL_REVIEW.md
FINAL_REPORT.md
```

**If ANY file is missing:**
- Identify which file is missing
- Return to the appropriate sub-stage
- Complete the missing step
- Do not proceed until all 3 files exist

---

## Analysis Complete

Before declaring analysis complete, ensure:

**All 3 report files exist:**
- ✅ analysis_report_DRAFT.md
- ✅ CRITICAL_REVIEW.md
- ✅ FINAL_REPORT.md

**All mandatory steps completed:**
- ✅ Stage 3A: Draft report created
- ✅ Stage 3B: Critical review performed with decision
- ✅ Stage 3B-Action: Review feedback addressed
- ✅ Stage 3C: Final report created

---

## Stage 3 Summary

By the end of Stage 3, you should have:

**Report files (3 required):**
- ✅ analysis_report_DRAFT.md (from Stage 3A)
- ✅ CRITICAL_REVIEW.md (from Stage 3B)
- ✅ FINAL_REPORT.md (from Stage 3C)

**Quality assurance:**
- ✅ Independent review completed
- ✅ All issues addressed (fixed or caveated)
- ✅ Analysis validated for rigor and reproducibility

---

## Analysis Complete

**Once Stage 3 validation passes:**

1. Register the completed analysis session:

```python
wm.register_session(
    task=f'{disease} single-cell target identification',
    inputs={
        'disease': disease,
        'tissue': tissue,
        'disease_cells': n_disease,
        'healthy_cells': n_healthy
    },
    outputs={
        'scripts': len(list(code_dir.glob('*.py'))),
        'figures': len(list(figure_dir.glob('*.png'))),
        'tables': len(list(table_dir.glob('*.csv'))),
        'reports': 3  # DRAFT, REVIEW, FINAL
    },
    status='completed'
)

print("✓ Analysis session registered in metadata.json")
```

2. Summarize key outputs:

```
ANALYSIS COMPLETE
================================================================================
Workspace: workspace/{date}/{run_id}/single_cell_analyst/

Key Outputs:
- Data files: [processed data files]
- Scripts: [number] Python analysis scripts
- Figures: [number] publication-quality figures (dpi=300)
- Tables: [number] CSV result files
- Reports: 3 (DRAFT, CRITICAL_REVIEW, FINAL)

Top Findings:
- [Brief summary of top therapeutic targets]
- [Cell types with strongest disease signatures]
- [Key pathways dysregulated]

Next Steps:
- Review FINAL_REPORT.md for complete analysis
- Share with stakeholders
- Plan validation experiments
```

---

## Common Issues

**Issue: Forgot to create DRAFT before REVIEW**
- You cannot review something that doesn't exist
- Return to Stage 3A
- Create DRAFT_REPORT.md
- Then proceed to 3B

**Issue: Review returns REJECT but want to skip fixes**
- This is not allowed
- REJECT means re-analysis is required
- Return to Stage 2
- Fix all major issues
- Re-review before proceeding

**Issue: Validation script fails at end of Stage 3**
- Check which file is missing
- Return to appropriate sub-stage
- Complete missing step
- Run validation again

---

## Stage 3 Gate (Final)

Before considering analysis complete, verify:

```python
# All 3 reports exist
draft_exists = (report_dir / 'analysis_report_DRAFT.md').exists()
review_exists = (report_dir / 'CRITICAL_REVIEW.md').exists()
final_exists = (report_dir / 'FINAL_REPORT.md').exists()

print(f"\n[Final Validation]")
print(f"  DRAFT exists: {draft_exists}")
print(f"  REVIEW exists: {review_exists}")
print(f"  FINAL exists: {final_exists}")

all_exist = draft_exists and review_exists and final_exists

if not all_exist:
    print("❌ Stage 3 incomplete - missing report files")
    if not draft_exists:
        print("   Missing: DRAFT (complete Stage 3A)")
    if not review_exists:
        print("   Missing: REVIEW (complete Stage 3B)")
    if not final_exists:
        print("   Missing: FINAL (complete Stage 3C)")
    raise ValueError("Cannot complete analysis - Stage 3 incomplete")

print("✅ All 3 report files exist")
print("✅ Stage 3 complete")
print("✅ Analysis finished")
```

---

## Success Criteria

Stage 3 is complete when:
- ✅ analysis_report_DRAFT.md created with all findings
- ✅ CRITICAL_REVIEW.md created with evaluation and decision
- ✅ Review feedback addressed (fixes applied or caveats added)
- ✅ FINAL_REPORT.md created incorporating all revisions
- ✅ Validation script confirms all files exist

---

**After completing Stage 3, your comprehensive single-cell analysis is complete.**
