---
name: single-cell-analysis
description: Statistical analysis and reporting for single-cell RNA-seq data. Performs pseudobulk differential expression (PyDESeq2), pathway enrichment (gseapy GSEA), and generates publication-ready reports with critical review. Use when you have clean integrated scRNA-seq data and need to identify disease-associated genes, dysregulated pathways, and therapeutic targets.
---

# Single-Cell Analysis & Reporting

## Overview

This skill performs comprehensive statistical analysis on clean, integrated single-cell RNA-seq data. It transforms batch-corrected AnnData into biological insights through rigorous differential expression testing, pathway enrichment, and critical review.

**Pipeline:**
1. **Load & Validate** - Import integrated.h5ad and verify requirements met
2. **Differential Expression** - Pseudobulk PyDESeq2 analysis (donor x condition aggregation)
3. **Pathway Enrichment** - Multi-database GSEA analysis
4. **Critical Review** - Independent quality evaluation and synthesis
5. **Final Report** - Publication-ready analysis with therapeutic target recommendations

**Input:** `processed/integrated.h5ad` (from single-cell-data-prep-qc skill)

**Outputs:**
- DE results tables (`de_*.csv`)
- Pathway enrichment results (`pathway_*.csv`, `gsea_*.csv`)
- Visualization suite (volcano plots, heatmaps, pathway dotplots)
- Three reports (DRAFT, CRITICAL_REVIEW, FINAL)

**Key Features:**
- **MANDATORY** pseudobulk approach (avoids pseudoreplication)
- **MANDATORY** pathway enrichment (multiple databases)
- **MANDATORY** independent critical review
- Publication-quality visualizations throughout
- Therapeutic target prioritization

---

## ⚠️ CRITICAL: Input Requirements

**This skill REQUIRES pre-processed integrated data from the data-prep-qc skill.**

**Required input file:** `workspace/{date}/{run_id}/single_cell_analyst/data/processed/integrated.h5ad`

**Input must contain:**
- ✅ Raw counts in `.X` (integer counts, not normalized)
- ✅ Gene symbols as `var_names` (not integers)
- ✅ `obs['condition']` column (disease/healthy labels)
- ✅ `obs['donor_id']` column (for pseudobulk DE - preferred)
- ✅ Cell type annotations (unified_cell_type or cell_type)
- ✅ Batch-corrected embedding (`X_pca_harmony` for visualizations)

**If you don't have integrated data yet:**
→ First invoke the `single-cell-data-prep-qc` skill to prepare your data

---

## Workspace Management

**Pattern for using existing workspace:**

```python
from src.utils.workspace_manager import WorkspaceManager

# Use the SAME date/run_id from data prep skill
current_date = 'YYYY-MM-DD'  # From data prep output
current_run_id = 'XXXXXXXX'  # From data prep output (8 chars)

wm = WorkspaceManager(
    agent_name='single_cell_analyst',
    date=current_date,
    run_id=current_run_id
)

# Get paths
processed_dir = wm.get_data_path('processed')
figure_dir = wm.get_results_path('figures')
table_dir = wm.get_results_path('tables')
report_dir = wm.get_results_path('reports')
```

**All analysis scripts and results will be added to the existing workspace.**

---

## Visualization Requirements

This skill requires **publication-quality visualizations** for all statistical analyses. All figures should:
- Use `dpi=300` for publication quality
- Include statistical annotations (p-values, FDR)
- Use clear titles, axis labels, and legends
- Save to `workspace/{date}/{run_id}/single_cell_analyst/results/figures/`
- Use informative filenames (e.g., `de_volcano_T_cells.png`, `pathway_dotplot_hallmark.png`)

**Required visualization categories:**
1. **Differential expression** - Volcano plots, MA plots, heatmaps of top DE genes
2. **Pathway enrichment** - Dotplots, bar plots, enrichment networks
3. **Target genes** - Expression plots (violin, UMAP overlays) for key candidates
4. **Cell type-specific analysis** - Per-cell-type DE and pathway summaries

See workflow documentation for specific visualization requirements at each step.

---

## Procedure Quick Reference

This skill contains procedures located in `procedures/`:

**Choose DE approach based on your comparison:**
- **Disease vs Healthy (cross-condition)** - [pseudobulk_de_procedure.md](procedures/pseudobulk_de_procedure.md) - PyDESeq2 donor-level DE (requires matched dataset)
- **Cell type comparisons (within disease)** - [within_donor_meta_analysis.md](procedures/within_donor_meta_analysis.md) - Within-donor DE + meta-analysis (e.g., T cells vs B cells, malignant vs normal)

**Always Required:**
- **Pathway Enrichment** - [pathway_enrichment_procedure.md](procedures/pathway_enrichment_procedure.md) - gseapy GSEA pathway analysis
- **Critical Review** - [review_procedure.md](procedures/review_procedure.md) - 5-category quality evaluation

---

## ⚠️ Forbidden Actions

**You Must NEVER:**
- Use Wilcoxon/t-test on single cells when donor_id exists (pseudoreplication)
- Skip pathway enrichment analysis
- Skip Stage 3 critical review
- Create FINAL_REPORT.md before DRAFT_REPORT.md and CRITICAL_REVIEW.md
- Proceed to next stage without validating current stage complete
- Generate figures without statistical annotations

**See [reference/forbidden_actions.md](reference/forbidden_actions.md) for full details.**

---

## Computational Resources

**See [reference/computational_resources.md](reference/computational_resources.md) for details.**

**Available resources:**
- 650 GB RAM
- Up to 30-minute timeouts (1,800,000 ms)
- Multiple cores for parallel processing

**Typical runtimes:**
- Pseudobulk DE (per cell type): 2-5 minutes
- GSEA enrichment: 5-10 minutes per cell type
- Full analysis pipeline: 45-70 minutes

**No excuses for shortcuts or skipping steps due to computational constraints.**

---

## Two-Stage Workflow

### Stage 1: Statistical Analysis (DE + Pathways)

**Complete workflow:** See [workflows/stage1_statistical_analysis.md](workflows/stage1_statistical_analysis.md)

**Objectives:**
- Load and validate integrated.h5ad
- **MANDATORY Checkpoint 1:** Run pseudobulk differential expression
- **MANDATORY Checkpoint 2:** Run pathway enrichment analysis
- Generate comprehensive DE and pathway visualizations
- Create analysis summary tables

**Key outputs:**
- `results/tables/de_*.csv` - DE results per cell type
- `results/tables/pathway_*.csv` - Enriched pathways
- DE visualizations (volcano plots, heatmaps)
- Pathway visualizations (dotplots, bar charts)

---

### Stage 2: Critical Review & Synthesis

**Complete workflow:** See [workflows/stage2_review_synthesis.md](workflows/stage2_review_synthesis.md)

**⛔ ALL SUB-STAGES ARE MANDATORY ⛔**

**Objectives:**
- Stage 2A: Create analysis_report_DRAFT.md with all findings
- Stage 2B: Independent critical review (adopt Dr. Reviewer persona)
- Stage 2B-Action: Address review feedback (revise or fix)
- Stage 2C: Create FINAL_REPORT.md with therapeutic target recommendations

**Key outputs:**
- `results/reports/analysis_report_DRAFT.md`
- `results/reports/CRITICAL_REVIEW.md`
- `results/reports/FINAL_REPORT.md`

---

## Analysis Completion Checklist

Before declaring analysis complete, verify:

**Files present:**
```bash
# DE results
ls workspace/{date}/{run_id}/single_cell_analyst/results/tables/de_*.csv

# Pathway results
ls workspace/{date}/{run_id}/single_cell_analyst/results/tables/pathway_*.csv
ls workspace/{date}/{run_id}/single_cell_analyst/results/tables/gsea_*.csv

# Figures
ls workspace/{date}/{run_id}/single_cell_analyst/results/figures/de_*.png
ls workspace/{date}/{run_id}/single_cell_analyst/results/figures/pathway_*.png

# Reports (all 3 required)
ls workspace/{date}/{run_id}/single_cell_analyst/results/reports/*.md
```

**Success criteria:**
- ✅ Both stages completed in sequence
- ✅ All mandatory checkpoints completed (pseudobulk DE, pathway enrichment, critical review)
- ✅ All required files exist
- ✅ All visualizations generated with statistical annotations
- ✅ No forbidden actions taken
- ✅ Three reports created (DRAFT, CRITICAL_REVIEW, FINAL)

---

## Workflow Summary

```
START: Load integrated.h5ad from data prep skill
  ↓
STAGE 1: Statistical Analysis
  ├─ Load and validate integrated data
  ├─ Checkpoint 1: ⚠️ MANDATORY Pseudobulk Differential Expression
  │   ├─ Find largest matched dataset (disease + healthy in same study)
  │   ├─ Extract each cell type with adequate samples
  │   ├─ Aggregate by donor x condition (manual pandas groupby)
  │   ├─ Run PyDESeq2 for each cell type (optimized parameters)
  │   ├─ Filter for significance (FDR<0.05, |log2FC|>0.5)
  │   ├─ Generate volcano plots and heatmaps
  │   └─ Link: procedures/pseudobulk_de_procedure.md
  ├─ Checkpoint 2: ⚠️ MANDATORY Pathway Enrichment
  │   ├─ Prepare ranked gene lists from DE results
  │   ├─ Run gseapy GSEA (Hallmark, KEGG, Reactome)
  │   ├─ Filter for significance (FDR<0.05, |NES|>1.5)
  │   ├─ Generate dotplots and bar charts
  │   └─ Link: procedures/pathway_enrichment_procedure.md
  └─ Gate: Verify DE and pathway results exist
  ↓
STAGE 2: Review & Synthesis (3 mandatory sub-stages)
  ├─ 2A: Create DRAFT_REPORT.md with all findings
  ├─ 2B: ⚠️ MANDATORY Critical Review
  │   ├─ Adopt Dr. Reviewer persona
  │   ├─ Evaluate 5 categories (data quality, statistics, biology, reproducibility, interpretation)
  │   ├─ Classify issues (major vs minor)
  │   ├─ Make decision (REJECT / REVISE / APPROVE)
  │   ├─ Create CRITICAL_REVIEW.md
  │   └─ Link: procedures/review_procedure.md
  ├─ 2B-Action: Address review feedback
  │   ├─ If REJECT: Fix issues and re-analyze
  │   ├─ If REVISE: Add caveats to draft
  │   └─ If APPROVE: Proceed to 2C
  ├─ 2C: Create FINAL_REPORT.md
  │   ├─ Incorporate all revisions
  │   ├─ Add therapeutic target recommendations
  │   └─ Include all figure references
  └─ Gate: Verify all 3 reports exist
  ↓
COMPLETE: Therapeutic target identification finished
```

---

## Key Principles

**Follow the workflow sequentially:**
- Complete Stage 1 before Stage 2
- Complete Stage 2A before 2B before 2C
- Complete all mandatory checkpoints
- Generate visualizations at each analysis step

**Use procedures at checkpoints:**
- When workflow references a procedure file, read it completely
- Follow the procedure's instructions exactly
- Return to workflow when procedure is complete

**Validate at gates:**
- Run validation scripts before progressing
- Verify required files exist
- Ensure all visualizations generated
- Do not skip stages or checkpoints

**Generate statistical visualizations:**
- Include significance annotations (p-values, FDR)
- Show effect sizes (log2FC, NES)
- Use color scales appropriately
- Add informative legends

---

## Troubleshooting

**Issue: No donor_id in integrated data**
- Check if alternative sample IDs exist (individual_id, sample_id)
- If truly no donor info: Document limitation and use Wilcoxon as fallback
- Add clear caveat in reports about pseudoreplication risk

**Issue: Pseudobulk DE returns no significant genes**
- Verify pseudobulk was used (not single-cell testing)
- Check sample sizes (need ≥3 donors per condition ideally)
- Consider biological reality: some cell types may not differ
- Document findings even if no differences detected

**Issue: Pathway enrichment returns no results**
- Verify DE gene list is properly ranked
- Check gene symbols match database format
- Try multiple databases if one fails
- Lower stringency (FDR<0.25) for exploratory analysis
- Document if no enrichment found

**Issue: Critical review returns REJECT**
- This is expected for some analyses
- Return to Stage 1 and fix major issues
- Re-run affected analyses
- Re-review before proceeding to final report

---

## Success Criteria

Your statistical analysis is complete when:
- ✅ Both stages finished in sequence
- ✅ Pseudobulk DE performed and results saved
- ✅ Pathway enrichment performed and results saved
- ✅ Comprehensive visualization suite generated
- ✅ Critical review completed
- ✅ All 3 report files created
- ✅ Validation scripts pass
- ✅ Therapeutic targets identified and prioritized

---

## Output Summary

**At completion, your workspace contains:**

**Tables:**
- DE results for all analyzed cell types
- Pathway enrichment results (multiple databases)
- Gene rankings and target prioritization

**Figures:**
- Volcano plots for each cell type
- Heatmaps of top DE genes
- Pathway enrichment dotplots
- Gene expression plots for key targets
- Integration overview (from data prep)

**Reports:**
- DRAFT_REPORT.md - Initial findings
- CRITICAL_REVIEW.md - Quality evaluation
- FINAL_REPORT.md - Publication-ready analysis

**All outputs in:** `workspace/{date}/{run_id}/single_cell_analyst/`

---

**You have the workflow. Execute it completely. Follow every checkpoint. Generate all visualizations. Complete critical review. No shortcuts.**
