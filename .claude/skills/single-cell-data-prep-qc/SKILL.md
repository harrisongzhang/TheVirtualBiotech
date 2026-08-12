---
name: single-cell-data-prep-qc
description: Single-cell RNA-seq data preparation and quality control pipeline. Handles data discovery from CELLxGENE Census, quality filtering, cell type harmonization, and batch correction. Outputs clean, integrated AnnData ready for statistical analysis. Use when you need to prepare scRNA-seq data for analysis or create publication-ready integrated datasets.
---

# Single-Cell Data Preparation & QC

## Overview

This skill prepares single-cell RNA-seq data from CELLxGENE Census for downstream statistical analysis. It handles the full data engineering pipeline from raw data query to batch-corrected, quality-controlled integrated dataset.

**Pipeline:**
1. **Data Discovery** - Query Census, download disease + healthy data, subsample if needed
2. **Quality Control** - Filter low-quality cells, set gene symbols
3. **Harmonization** - Standardize cell type annotations across datasets
4. **Integration** - Batch correction with Harmony, validation, visualization

**Output:** `processed/integrated.h5ad` - Clean, batch-corrected dataset ready for DE/pathway analysis

**Key Features:**
- Enforces ~100K cell limit with two-stage stratified subsampling (donor + cell type)
- Prioritizes donor preservation for robust pseudobulk analysis
- Conservative cell type harmonization
- Harmony batch correction with validation metrics
- **Comprehensive QC visualizations at each step**

---

## ⚠️ CRITICAL: Workspace Initialization

**Data Acquisition:**
- Data is downloaded via **MCP tools** (e.g., `mcp__single_cell__get_anndata`)
- WorkspaceManager can be initialized via **direct bash command** before writing scripts

**Workspace Initialization Pattern:**
```bash
# Recommended: Initialize workspace before any data operations
python -c "from src.utils.workspace_manager import WorkspaceManager; wm = WorkspaceManager(agent_name='single_cell_analyst'); print(f'DATE={wm.date}'); print(f'RUN_ID={wm.run_id}')"
```

**When writing Python scripts:**
❌ **NEVER write to project root**
✅ **ALWAYS write to workspace code directory**: `workspace/{date}/{run_id}/single_cell_analyst/code/scripts/`

**See [reference/workspace_setup.md](reference/workspace_setup.md) for complete pattern.**

**⚠️ CELLxGENE Census Gene Index Fix:** AnnData objects from Census use numeric IDs as `var_names` (e.g., '0', '1', '2'), NOT gene symbols. Gene symbols are in `adata.var['feature_name']`. **Always** run this immediately after any Census download, before any other processing:
```python
adata.var.index = adata.var['feature_name'].astype(str)
adata.var_names_make_unique()
```

---

## Visualization Requirements

This skill requires **publication-quality visualizations** at each major step. All figures should:
- Use `dpi=300` for publication quality
- Include clear titles and axis labels
- Save to `workspace/{date}/{run_id}/single_cell_analyst/results/figures/`
- Use informative filenames (e.g., `qc_violin_plots.png`, `batch_correction_umap.png`)

**Required visualization categories:**
1. **QC metrics** - Distribution plots before/after filtering
2. **Cell type composition** - Bar plots comparing disease vs healthy
3. **Batch correction validation** - Before/after UMAPs with metrics
4. **Data summary** - Cell counts, sample sizes, metadata overview

See workflow documentation for specific visualization requirements at each step.

---

## Procedure Quick Reference

This skill contains 3 procedures located in `procedures/`:

**Mandatory Procedures:**
- **Subsampling** - [subsampling_procedure.md](procedures/subsampling_procedure.md) - Two-stage stratified sampling to ~100K cells (preserves all donors + cell types; when combined >100K)
- **Harmonization** - [harmonization_procedure.md](procedures/harmonization_procedure.md) - Conservative cell type name standardization (when annotations differ)
- **Batch Correction** - [batch_correction_procedure.md](procedures/batch_correction_procedure.md) - Harmony integration with validation metrics (always required)

---

## ⚠️ Forbidden Actions

**You Must NEVER:**
- Skip the subsampling checkpoint when dataset >100K cells
- Proceed with integration before harmonizing cell types (if needed)
- Skip batch correction validation metrics
- Create analysis scripts without gene symbols properly set
- Write files to project root instead of workspace

**See [reference/forbidden_actions.md](reference/forbidden_actions.md) for full details.**

---

## Computational Resources

**See [reference/computational_resources.md](reference/computational_resources.md) for details.**

**Available resources:**
- 650 GB RAM
- Up to 30-minute timeouts (1,800,000 ms)
- Multiple cores for parallel processing

**No excuses for shortcuts or skipping steps due to computational constraints.**

---

## Two-Stage Workflow

### Stage 1: Data Discovery & Size Optimization

**Complete workflow:** See [workflows/stage1_data_discovery.md](workflows/stage1_data_discovery.md)

**Objectives:**
- Initialize WorkspaceManager with date/run_id
- Query CELLxGENE Census for disease + healthy tissue data
- Download and save raw data to workspace
- **CHECKPOINT:** Evaluate size and subsample to ≤100K cells if needed
- Generate initial QC visualizations

**Key outputs:**
- `data/raw/disease_raw_subsampled.h5ad`
- `data/raw/healthy_raw_subsampled.h5ad`
- Initial QC plots

---

### Stage 2: QC, Harmonization & Integration

**Complete workflow:** See [workflows/stage2_qc_integration.md](workflows/stage2_qc_integration.md)

**Objectives:**
- Load subsampled data from Stage 1 (≤100K cells guaranteed)
- Perform QC and filtering
- **Checkpoint 1:** Harmonize cell type annotations if needed
- **Checkpoint 2:** Perform batch correction with Harmony
- Validate integration quality
- Generate comprehensive visualization suite

**Key outputs:**
- `data/processed/integrated.h5ad` - Ready for analysis
- QC, harmonization, integration figures
- Metadata summary JSON

---

## Workflow Completion Checklist

Before declaring data prep complete, verify:

**Files present:**
```bash
# Raw data
ls workspace/{date}/{run_id}/single_cell_analyst/data/raw/*_subsampled.h5ad

# Processed data (MAIN OUTPUT)
ls workspace/{date}/{run_id}/single_cell_analyst/data/processed/integrated.h5ad

# QC figures
ls workspace/{date}/{run_id}/single_cell_analyst/results/figures/qc_*.png

# Integration figures
ls workspace/{date}/{run_id}/single_cell_analyst/results/figures/integration_*.png
```

**Success criteria:**
- ✅ Both stages completed in sequence
- ✅ All mandatory checkpoints completed (subsampling, harmonization, batch correction)
- ✅ Gene symbols properly set (not integers)
- ✅ Batch correction validated with metrics
- ✅ All required visualizations generated
- ✅ integrated.h5ad file exists and is valid

---

## Output Handoff to Statistical Analysis Skill

**Primary output:** `workspace/{date}/{run_id}/single_cell_analyst/data/processed/integrated.h5ad`

**This file contains:**
- Batch-corrected expression data (`X_pca_harmony` for neighbors/UMAP)
- Raw counts in `.X` (preserved for differential expression)
- Quality-filtered cells
- Gene symbols as `var_names` (required for pathway enrichment)
- Harmonized cell type labels in `obs['unified_cell_type']`
- Condition labels in `obs['condition']` (disease/healthy)
- Donor IDs in `obs['donor_id']` (for pseudobulk DE)

**Validation before handoff:**
```python
import scanpy as sc

# Load integrated data
adata = sc.read_h5ad('workspace/{date}/{run_id}/single_cell_analyst/data/processed/integrated.h5ad')

print("\n[Integration Output Validation]")
print(f"  Cells: {adata.n_obs:,}")
print(f"  Genes: {adata.n_vars:,}")
print(f"  Gene symbols set: {not adata.var_names[0].isdigit()}")
print(f"  Has donor_id: {'donor_id' in adata.obs.columns}")
print(f"  Has condition: {'condition' in adata.obs.columns}")
print(f"  Has unified_cell_type: {'unified_cell_type' in adata.obs.columns}")
print(f"  Has Harmony embedding: {'X_pca_harmony' in adata.obsm.keys()}")

# Verify raw counts preserved
import numpy as np
is_integer = np.all(np.equal(np.mod(adata.X.data, 1), 0))
print(f"  Raw counts preserved: {is_integer}")

print("\n✅ Data ready for statistical analysis")
```

**Metadata summary:**
Create `results/reports/data_prep_summary.md` with:
- Data sources and queries used
- Cell counts before/after each QC step
- Harmonization decisions (if applied)
- Batch correction validation metrics
- All figure references

---

## Workflow Summary

```
START: Recognize data prep task
  ↓
STAGE 1: Data Discovery & Size Optimization
  ├─ Initialize workspace (print date/run_id)
  ├─ Query CELLxGENE Census
  ├─ Download data
  ├─ ⚠️ CHECKPOINT: Size check → subsample if >100K cells
  │   └─ Link: procedures/subsampling_procedure.md
  ├─ Generate initial QC visualizations
  └─ Gate: Verify *_subsampled.h5ad files exist
  ↓
STAGE 2: QC, Harmonization & Integration
  ├─ Load subsampled data (≤100K cells guaranteed)
  ├─ QC and filtering with visualizations
  ├─ Checkpoint 1: Harmonization → harmonize if needed
  │   └─ Link: procedures/harmonization_procedure.md
  ├─ Checkpoint 2: Batch correction with Harmony + validation
  │   └─ Link: procedures/batch_correction_procedure.md
  ├─ Generate comprehensive visualization suite
  └─ Gate: Verify integrated.h5ad exists and validated
  ↓
COMPLETE: Clean data ready for statistical analysis
```

---

## Key Principles

**Follow the workflow sequentially:**
- Complete Stage 1 before Stage 2
- Complete all checkpoints in order
- Generate visualizations at each step

**Use procedures at checkpoints:**
- When workflow references a procedure file, read it completely
- Follow the procedure's instructions exactly
- Return to workflow when procedure is complete

**Validate at gates:**
- Run validation checks before progressing
- Verify required files exist
- Ensure visualizations are generated
- Do not skip stages or checkpoints

**Generate publication-quality figures:**
- All plots at dpi=300
- Clear labels and legends
- Informative titles
- Save with descriptive filenames

---

## Troubleshooting

**Issue: Dataset exceeds 100K cells but subsampling skipped**
- Return to Stage 1 subsampling checkpoint
- Apply stratified subsampling procedure
- Regenerate subsampled files

**Issue: Gene symbols still integers after QC**
- Check if `feature_name` column exists in var
- Apply gene symbol mapping in QC script
- Make unique if duplicates exist

**Issue: Batch correction fails validation**
- Check iLISI and ASW_batch metrics
- Ensure batch variable is dataset_id (not condition)
- Try adjusting Harmony parameters or alternative methods

**Issue: Cell type harmonization unclear**
- Review harmonization procedure carefully
- Create decision table for manual adjudication
- Document all harmonization decisions
- When in doubt, be conservative (don't force matches)

---

## Success Criteria

Your data preparation is complete when:
- ✅ Both stages finished in sequence
- ✅ All checkpoints evaluated and procedures followed
- ✅ Subsampling performed if needed
- ✅ Harmonization completed if annotations differed
- ✅ Batch correction applied and validated
- ✅ integrated.h5ad file created with all required components
- ✅ All visualization requirements met
- ✅ Data prep summary report created
- ✅ Validation checks pass

---

## Next Steps

After completing data preparation:
1. Verify all Stage 2 checklist items are checked
2. Run validation script to confirm integrated.h5ad quality
3. Review data_prep_summary.md
4. **Ready to invoke single-cell-statistical-analysis skill** for DE and pathway enrichment

---

**You have the workflow. Execute it completely. Follow every checkpoint. Generate all visualizations. No shortcuts.**
