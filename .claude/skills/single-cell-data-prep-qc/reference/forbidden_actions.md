# Forbidden Actions - Single-Cell Analysis

## Overview

This document lists actions that are **absolutely forbidden** during single-cell RNA-seq analysis. Violating these rules compromises statistical validity, scientific rigor, or workflow completeness.

---

## ❌ Statistical Analysis Violations

### 1. Using Single-Cell DE Methods When Donor ID Exists

**FORBIDDEN:**
```python
# ❌ DO NOT DO THIS if donor_id exists:
sc.tl.rank_genes_groups(
    adata,
    groupby='condition',
    method='wilcoxon'  # Treats cells as independent - WRONG
)
```

**Why forbidden:**
- Cells from the same donor are correlated, not independent
- This is **pseudoreplication** - inflates significance
- P-values are artificially low and unreliable
- Reviewers will reject this analysis

**Required approach:**
- Aggregate cells by donor (pseudobulk)
- Test at donor level using PyDESeq2
- Follow: `procedures/pseudobulk_de_procedure.md`

---

### 2. Using Raw P-Values Without FDR Correction

**FORBIDDEN:**
```python
# ❌ DO NOT filter by raw p-values:
sig_genes = de_results[de_results['pvals'] < 0.05]
```

**Required:**
```python
# ✅ ALWAYS use FDR-adjusted p-values:
sig_genes = de_results[de_results['padj'] < 0.05]
```

**Why:** Testing thousands of genes requires multiple testing correction.

---

### 3. Not Reporting Effect Sizes

**FORBIDDEN:**
- Reporting only p-values without log2 fold changes
- Claiming significance without magnitude

**Required:**
- Report both padj AND log2FoldChange
- Use |log2FC| > 0.5 as significance threshold
- Show effect sizes in all tables and figures

---

## ❌ Workflow Violations

### 4. Skipping Pathway Enrichment

**FORBIDDEN:**
- Claiming "time constraints" to skip pathway enrichment
- Treating pathway analysis as optional
- Submitting final report without pathway results

**Required:**
- Pathway enrichment is MANDATORY after DE analysis
- Follow: `procedures/pathway_enrichment_procedure.md`
- Results must be saved before Stage 3

---

### 5. Skipping Stage 3 Critical Review

**FORBIDDEN:**
- Creating FINAL_REPORT.md before DRAFT_REPORT.md
- Creating FINAL_REPORT.md before CRITICAL_REVIEW.md
- Skipping the review procedure
- Proceeding without addressing review feedback

**Required:**
- Stage 3 has 3 MANDATORY sub-stages (3A, 3B, 3C)
- Must complete in order: DRAFT → REVIEW → Address feedback → FINAL
- Follow: `procedures/review_procedure.md` for Stage 3B
- All 3 report files must exist before analysis is complete

---

### 6. Skipping Stages or Reordering

**FORBIDDEN:**
- Jumping from Stage 1 directly to Stage 3
- Starting Stage 3 before completing Stage 2
- Skipping checkpoints in Stage 2

**Required:**
- Complete Stage 1 → Stage 2 → Stage 3 in order
- Complete all checkpoints in each stage
- Validate gates before progressing

---

## ❌ Data Quality Violations

### 7. Inadequate Quality Filtering

**FORBIDDEN:**
```python
# ❌ Too permissive:
sc.pp.filter_cells(adata, min_genes=100)  # Too low
adata = adata[adata.obs['pct_counts_mt'] < 30]  # Too high
```

**Required:**
```python
# ✅ Standard thresholds:
sc.pp.filter_cells(adata, min_genes=300)  # Minimum
adata = adata[adata.obs['n_genes_by_counts'] < 9000]  # Remove outliers
adata = adata[adata.obs['pct_counts_mt'] < 15]  # Mitochondrial
sc.pp.filter_genes(adata, min_cells=50)  # Gene filtering
```

---

### 8. Not Validating Batch Correction

**FORBIDDEN:**
- Running Harmony without visual validation
- Not checking if batches actually mixed
- Ignoring residual batch effects on UMAP

**Required:**
- Generate before/after PCA plots colored by batch
- Generate UMAP colored by batch
- Verify batch mixing visually
- Calculate quantitative metrics (iLISI, ASW_batch) if possible

---

## ❌ Reproducibility Violations

### 10. Not Setting Random Seeds

**FORBIDDEN:**
```python
# ❌ No random seed:
sc.pp.subsample(adata, n_obs=100000)  # Not reproducible
```

**Required:**
```python
# ✅ Always set random seeds:
import numpy as np
np.random.seed(42)
sc.pp.subsample(adata, n_obs=100000, random_state=42)
```

---

### 11. Not Documenting Software Versions

**FORBIDDEN:**
- Final report without software versions
- Missing package versions in methods

**Required:**
Include in methods section:
```markdown
**Software**: scanpy vX.X, PyDESeq2 v0.5.2, gseapy vX.X, pandas vX.X,
Harmony vX.X, Python 3.12
```

---

## ❌ Workspace Violations

### 12. Using Absolute Paths (Web Interface)

**FORBIDDEN:**
```python
# ❌ Using absolute paths to write files
Write(file_path='/path/to/TheVirtualBiotech/workspace/2025-12-14/abc123/script.py', content=...)
base_dir = Path('/path/to/TheVirtualBiotech/workspace/...')

# ❌ Using WorkspaceManager (not needed in web interface)
from src.utils.workspace_manager import WorkspaceManager
wm = WorkspaceManager(agent_name='single_cell_analyst')
```

**Required:**
```python
# ✅ Using relative paths (your cwd is already set to session workspace)
from pathlib import Path

# Create subdirectories
Path('code/scripts').mkdir(parents=True, exist_ok=True)
Path('data/raw').mkdir(parents=True, exist_ok=True)
Path('data/processed').mkdir(parents=True, exist_ok=True)
Path('results/figures').mkdir(parents=True, exist_ok=True)
Path('results/tables').mkdir(parents=True, exist_ok=True)

# Write scripts with relative paths
Write(file_path='code/scripts/01_qc_analysis.py', content=...)
Write(file_path='code/scripts/02_integration.py', content=...)

# In generated scripts, use relative paths
adata.write_h5ad('data/raw/dataset.h5ad')
results.to_csv('results/tables/de_results.csv')
plt.savefig('results/figures/umap.png', dpi=300)
```

**Why forbidden:**
- Web interface automatically sets your `cwd` to session-specific workspace
- Absolute paths bypass workspace isolation (security risk)
- Files won't appear in web UI download section
- Creates cross-session access vulnerabilities
- WorkspaceManager is for CLI usage, not web interface

**Remember:** In the web interface, ALL file operations use relative paths. Your current directory is already correctly set.

---

## ❌ Gene Retrieval Violations

### 15. Filtering Genes at Download

**FORBIDDEN:**
```python
# ❌ DO NOT filter genes when downloading from Census:
get_anndata(..., gene_filter=['CD4', 'CD8'])  # Wrong!
```

**Required:**
- ALWAYS download complete transcriptome (ALL genes)
- QC, normalization, HVG selection require full gene set
- Filter to specific genes AFTER integration for visualization only

---

## ❌ Interpretation Violations

### 16. Making Causal Claims

**FORBIDDEN:**
- "Gene X causes disease Y" (from correlative data)
- "Pathway A drives phenotype B" (without functional validation)

**Required:**
- "Gene X is associated with disease Y"
- "Pathway A is activated in phenotype B"
- Use appropriate language: "correlates", "associated", "suggests"

---

### 17. Overinterpreting Weak Signals

**FORBIDDEN:**
- Claiming significance based on p-value alone without effect size
- Highlighting genes with log2FC <0.5 as "differentially expressed"

**Required:**
- Use thresholds: padj<0.05 AND |log2FC|>0.5
- Report both p-value and effect size
- Prioritize strong signals (|log2FC| >1.0 are most robust)

---

## Consequences of Violations

**If forbidden actions are detected:**
- Analysis will be flagged during critical review (Stage 3B)
- Review will return REJECT decision
- Must return to Stage 2 and fix issues
- Cannot proceed to FINAL_REPORT.md

**Example violations from test run:**
- ❌ Used Wilcoxon when donor_id existed (pseudoreplication)
- ❌ Skipped pathway enrichment (incomplete analysis)
- ❌ Skipped Stage 3B review (no quality control)

**Result:** Analysis had to be re-done.

---

## Summary

**Remember:**
- These are not suggestions - they are requirements
- Violating these rules produces invalid or incomplete analyses
- Follow the procedures at each checkpoint to avoid violations
- When in doubt, check the relevant procedure file

**If you encounter a situation not covered here, err on the side of rigor and completeness.**
