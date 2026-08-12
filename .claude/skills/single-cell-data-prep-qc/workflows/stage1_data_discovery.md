# Stage 1: Data Discovery & Acquisition

## Overview

Stage 1 focuses on identifying the right data sources, querying CELLxGENE Census, and downloading raw data for analysis. This stage sets the foundation for all downstream analyses.

**Time allocation:** 20% of total analysis time

**For Web Interface:** Your workspace is automatically configured. Use relative paths for all files.

---

## Stage 1 Checklist

Copy this checklist and check off items as you complete them:

```
Stage 1 Progress:
- [ ] Step 1.1: Set up workspace directories
- [ ] Step 1.2: Explore available data in CELLxGENE Census
- [ ] Step 1.3: Query and count cells for disease data
- [ ] Step 1.4: Query and count cells for healthy data
- [ ] Step 1.5: Download disease data
- [ ] Step 1.6: Download healthy data
- [ ] Step 1.7: Save raw data to workspace
- [ ] Step 1.8: CHECKPOINT - Size check and subsample if needed
- [ ] Step 1.9: Perform initial quality assessment
```

---

## Step 1.1: Set Up Workspace Directories

**For Web Interface:** Your `cwd` is already set to your session workspace. Just create subdirectories for organization.

### Create Directory Structure

```python
from pathlib import Path

# Create standard workspace directories
Path('data/raw').mkdir(parents=True, exist_ok=True)
Path('data/processed').mkdir(parents=True, exist_ok=True)
Path('code/scripts').mkdir(parents=True, exist_ok=True)
Path('results/figures').mkdir(parents=True, exist_ok=True)
Path('results/tables').mkdir(parents=True, exist_ok=True)
Path('results/reports').mkdir(parents=True, exist_ok=True)

print('✓ Workspace directories created')
print('  - data/raw/')
print('  - data/processed/')
print('  - code/scripts/')
print('  - results/figures/')
print('  - results/tables/')
print('  - results/reports/')
```

**That's it!** All file operations from now on use relative paths:
- Save data: `adata.write_h5ad('data/raw/dataset.h5ad')`
- Save results: `results.to_csv('results/tables/de_genes.csv')`
- Save figures: `plt.savefig('results/figures/umap.png', dpi=300')`

**Script File Locations:**

When writing Python scripts, write them to the code directory using relative paths:

```python
Write(file_path='code/scripts/01_data_acquisition.py', content=...)
Write(file_path='code/scripts/02_qc_preprocessing.py', content=...)
```

---

## Step 1.2: Explore CELLxGENE Census

**Use Single Cell MCP tools to explore available data.**

### List Available Tissues

```python
# Use MCP tool to see what tissues are available
# mcp__single_cell__list_metadata_values(column_name='tissue')
```

### List Available Diseases for Your Tissue

```python
# Example for breast tissue:
# mcp__single_cell__list_metadata_values(
#     column_name='disease',
#     value_filter="tissue == 'breast'"
# )
```

### List Available Cell Types

```python
# mcp__single_cell__list_metadata_values(
#     column_name='cell_type',
#     value_filter="tissue == 'breast'"
# )
```

---

## Step 1.3: Count Disease Cells

**Before downloading, estimate dataset size.**

```python
# Example for breast cancer:
# disease_count = mcp__single_cell__count_cells(
#     value_filter="tissue == 'breast' and disease in ['invasive ductal breast carcinoma', 'breast cancer', ...]"
# )
```

**Query strategy:**
- For broad disease queries (e.g., "breast cancer"): Use OR query to capture all subtypes
- For specific queries (e.g., "triple-negative"): Query only that specific subtype
- Always match query specificity to user request

---

## Step 1.4: Count Healthy Cells

```python
# healthy_count = mcp__single_cell__count_cells(
#     value_filter="tissue == 'breast' and disease == 'normal'"
# )
```

**Verify adequate healthy controls exist.**

---

## Step 1.5: Download Disease Data

**Use MCP tool `mcp__single_cell__get_anndata` to retrieve data.**

**⚠️ CRITICAL: Always retrieve ALL genes (full transcriptome), never filter genes at download.**

```python
# Use the raw_dir path from Step 1.1
# mcp__single_cell__get_anndata(
#     output_path=f"workspace/{current_date}/{current_run_id}/single_cell_analyst/data/raw/disease_raw.h5ad",
#     value_filter="tissue == 'breast' and disease in ['invasive ductal breast carcinoma', ...]",
#     layer='raw'
# )
```

**Why retrieve all genes:**
- QC requires full transcriptome (mitochondrial %, total counts)
- Normalization requires all genes
- HVG selection needs complete gene set
- Batch correction requires genome-wide signal
- Cell type identification needs all marker genes

**You can filter to specific genes AFTER QC/integration for visualization.**

---

## Step 1.6: Download Healthy Data

**Use MCP tool `mcp__single_cell__get_anndata` to retrieve healthy tissue data.**

```python
# Use the raw_dir path from Step 1.1
# mcp__single_cell__get_anndata(
#     output_path=f"workspace/{current_date}/{current_run_id}/single_cell_analyst/data/raw/healthy_raw.h5ad",
#     value_filter="tissue == 'breast' and disease == 'normal'",
#     layer='raw'
# )
```

---

## Step 1.7: Save Data to Workspace

**Data should be saved during download (output_path parameter).**

**Verify files are in workspace:**

```python
import os

disease_file = raw_dir / 'disease_raw.h5ad'
healthy_file = raw_dir / 'healthy_raw.h5ad'

print(f"\n[Data Download Verification]")
print(f"  Disease file: {disease_file.exists()} ({os.path.getsize(disease_file) / 1e9:.2f} GB)")
print(f"  Healthy file: {healthy_file.exists()} ({os.path.getsize(healthy_file) / 1e9:.2f} GB)")
```

---

## Step 1.8: ⚠️ CHECKPOINT - Dataset Size Check and Subsampling

**CRITICAL: This checkpoint must be evaluated BEFORE any QC or processing.**

### Load Downloaded Data

```python
import scanpy as sc
import numpy as np

# Load raw downloaded data
adata_disease = sc.read_h5ad(raw_dir / 'disease_raw.h5ad')
adata_healthy = sc.read_h5ad(raw_dir / 'healthy_raw.h5ad')

print(f"\n[Downloaded Data Size]")
print(f"  Disease: {adata_disease.n_obs:,} cells, {adata_disease.n_vars:,} genes")
print(f"  Healthy: {adata_healthy.n_obs:,} cells, {adata_healthy.n_vars:,} genes")
print(f"  Total: {adata_disease.n_obs + adata_healthy.n_obs:,} cells")
```

### Evaluate Size Threshold

```python
# MANDATORY SIZE CHECK
n_combined = adata_disease.n_obs + adata_healthy.n_obs

if n_combined > 100000:
    print(f"\n⚠️ CHECKPOINT TRIGGERED: {n_combined:,} cells exceeds 100K limit")
    print("   ACTION REQUIRED: Follow subsampling procedure")
    print("   Target: 50,000 cells per source (100K total)")
```

### IF total > 100K: Apply Subsampling (MANDATORY)

**See [procedures/subsampling_procedure.md](../procedures/subsampling_procedure.md) for complete procedure.**

**Implementation using utility function:**

```python
from src.utils.sc_preprocessing import subsample_datasets_for_integration

# Set random seed for reproducibility
np.random.seed(42)

target_per_source = 50000

# Two-stage stratified subsampling (preserves donors + cell types)
adata_disease, adata_healthy = subsample_datasets_for_integration(
    adata_disease=adata_disease,
    adata_healthy=adata_healthy,
    target_per_source=target_per_source,
    donor_col='donor_id',
    celltype_col='cell_type',
    random_state=42,
    verbose=True
)

print(f"\n✓ Subsampling complete")
print(f"  Disease: {adata_disease.n_obs:,} cells, {adata_disease.obs['donor_id'].nunique()} donors")
print(f"  Healthy: {adata_healthy.n_obs:,} cells, {adata_healthy.obs['donor_id'].nunique()} donors")
print(f"  Total: {adata_disease.n_obs + adata_healthy.n_obs:,} cells")

# CRITICAL: Remove unnecessary data to reduce file size
print("\n[Optimizing file size...]")
# Remove extra layers (keep only .X with raw counts)
adata_disease.layers = {}
adata_healthy.layers = {}

# Remove embeddings (will be computed fresh after QC)
adata_disease.obsm = {}
adata_disease.varm = {}
adata_healthy.obsm = {}
adata_healthy.varm = {}

# Save subsampled data
adata_disease.write_h5ad(raw_dir / 'disease_raw_subsampled.h5ad')
adata_healthy.write_h5ad(raw_dir / 'healthy_raw_subsampled.h5ad')

print(f"✓ Subsampled data saved")
```

### IF total ≤ 100K: No Subsampling Needed

```python
else:
    print(f"\n✓ No subsampling needed: {n_combined:,} cells within 100K limit")
    print("   Proceeding with full dataset")

    # Still optimize file size by removing unnecessary data
    adata_disease.layers = {}
    adata_healthy.layers = {}
    adata_disease.obsm = {}
    adata_disease.varm = {}
    adata_healthy.obsm = {}
    adata_healthy.varm = {}

    # Save optimized versions with same naming convention
    adata_disease.write_h5ad(raw_dir / 'disease_raw_subsampled.h5ad')
    adata_healthy.write_h5ad(raw_dir / 'healthy_raw_subsampled.h5ad')
    print("✓ Data optimized and saved")
```

**Key points:**
- Subsampling is **two-stage stratified** (donor-level balancing + cell-type preservation within donors)
- Preserves all donors for robust pseudobulk analysis (critical!)
- Prevents single-donor dominance while maintaining biological proportions
- Random seed = 42 for reproducibility
- Unnecessary layers and embeddings removed to keep files ~200-500MB
- Uses utility function: `src/utils/sc_preprocessing.subsample_datasets_for_integration()`
- **ALL subsequent stages use the `*_subsampled.h5ad` files**

---

## Step 1.9: Initial Quality Assessment

**Load the (potentially subsampled) data and inspect:**

```python
# Load the size-optimized data
adata_disease = sc.read_h5ad(raw_dir / 'disease_raw_subsampled.h5ad')
adata_healthy = sc.read_h5ad(raw_dir / 'healthy_raw_subsampled.h5ad')

print(f"\n[Working Dataset]")
print(f"  Disease: {adata_disease.n_obs:,} cells, {adata_disease.n_vars:,} genes")
print(f"  Healthy: {adata_healthy.n_obs:,} cells, {adata_healthy.n_vars:,} genes")
print(f"  Total: {adata_disease.n_obs + adata_healthy.n_obs:,} cells")

# Check metadata
print(f"\n[Metadata Available]")
print(f"  Disease obs columns: {adata_disease.obs.columns.tolist()}")
print(f"  Healthy obs columns: {adata_healthy.obs.columns.tolist()}")

# Check for donor_id (important for pseudobulk DE later)
has_donor_disease = 'donor_id' in adata_disease.obs.columns
has_donor_healthy = 'donor_id' in adata_healthy.obs.columns
print(f"\n[Donor Metadata]")
print(f"  Disease has donor_id: {has_donor_disease}")
print(f"  Healthy has donor_id: {has_donor_healthy}")

if has_donor_disease and has_donor_healthy:
    print(f"  ✅ Pseudobulk DE will be possible in Stage 2")
else:
    print(f"  ⚠️ Pseudobulk may not be possible - check for alternative sample IDs")

# Check cell type annotations
print(f"\n[Cell Types]")
print(f"  Disease types: {adata_disease.obs['cell_type'].nunique()}")
print(f"  Healthy types: {adata_healthy.obs['cell_type'].nunique()}")
```

---

## Stage 1 Outputs (Required)

By the end of Stage 1, you must have:

**Files:**
- ✅ `workspace/{date}/{run_id}/single_cell_analyst/data/raw/disease_raw.h5ad` (original download)
- ✅ `workspace/{date}/{run_id}/single_cell_analyst/data/raw/healthy_raw.h5ad` (original download)
- ✅ `workspace/{date}/{run_id}/single_cell_analyst/data/raw/disease_raw_subsampled.h5ad` (size-optimized, ≤50K cells)
- ✅ `workspace/{date}/{run_id}/single_cell_analyst/data/raw/healthy_raw_subsampled.h5ad` (size-optimized, ≤50K cells)

**Values communicated:**
- ✅ current_date printed and communicated
- ✅ current_run_id printed and communicated

**Assessment:**
- ✅ Cell counts reported (before and after subsampling if applicable)
- ✅ Subsampling performed if total > 100K cells
- ✅ Donor metadata status confirmed
- ✅ Cell type annotations verified

**Critical:** All Stage 2 analyses must use the `*_subsampled.h5ad` files, NOT the original `*_raw.h5ad` files.

---

## Stage 1 Completion Gate

Verify required files exist:

```bash
# Original downloads
ls workspace/{date}/{run_id}/single_cell_analyst/data/raw/disease_raw.h5ad
ls workspace/{date}/{run_id}/single_cell_analyst/data/raw/healthy_raw.h5ad

# Size-optimized versions (REQUIRED for Stage 2)
ls workspace/{date}/{run_id}/single_cell_analyst/data/raw/disease_raw_subsampled.h5ad
ls workspace/{date}/{run_id}/single_cell_analyst/data/raw/healthy_raw_subsampled.h5ad
```

**If files are missing:**
- Check MCP download completed successfully
- Verify output_path was correct
- Verify subsampling checkpoint was executed
- Re-run download and subsampling if needed

**If all files exist:**
- ✅ Stage 1 complete
- ✅ Dataset size is ≤100K cells total
- ✅ Ready to proceed to Stage 2

---

## Next Steps

**After completing Stage 1:**
1. Review the Stage 1 checklist - ensure all items are checked
2. Verify raw data files exist in workspace
3. Confirm date and run_id values are documented
4. Proceed to Stage 2: [workflows/stage2_analysis.md](stage2_analysis.md)

---

**Do not proceed to Stage 2 until all Stage 1 requirements are met.**
