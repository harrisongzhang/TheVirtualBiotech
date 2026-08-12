
# Batch Correction with Harmony Integration

## Contents
- [When to Use](#when-to-use)
- [Critical Concept: Batch Variable Definition](#critical-concept-batch-variable-definition)
- [Workflow Overview](#workflow-overview)
- [Step-by-Step Implementation](#step-by-step-implementation)
  - [Step 1: Define Batch Variable (BEFORE Concatenation)](#step-1-define-batch-variable-before-concatenation)
  - [Step 2: Concatenate Datasets](#step-2-concatenate-datasets)
  - [Step 3: Preserve Raw Counts & Normalization](#step-3-preserve-raw-counts--normalization)
  - [Step 4: Run Harmony Batch Correction](#step-4-run-harmony-batch-correction)
  - [Step 5: Validate Batch Correction (MANDATORY)](#step-5-validate-batch-correction-mandatory)
- [Quantitative Validation Metrics](#quantitative-validation-metrics)
- [Metric Interpretation](#metric-interpretation)
- [Troubleshooting Poor Batch Mixing](#troubleshooting-poor-batch-mixing)
- [Alternative Methods](#alternative-methods)
- [Validation Checklist](#validation-checklist)
- [Expected Results](#expected-results)
- [Documentation Template](#documentation-template)

## When to Use

Use this skill when integrating multiple single-cell RNA-seq datasets that have technical batch effects from different:
- Sequencing runs
- Laboratories
- Protocols
- Sample processing dates

## Critical Concept: Batch Variable Definition

**CRITICAL**: CELLxGENE Census aggregates multiple studies. Each study is a **technical batch** that requires correction.

- **Batch variable = `dataset_id`** (NOT 'Healthy'/'Disease')
- **Condition variable = 'condition'** (Healthy vs Disease - biological signal to preserve)
- Harmony corrects technical variation (dataset_id) while preserving biological variation (condition)

## Workflow Overview

1. **Define batch variable using dataset_id** BEFORE concatenation
2. **Concatenate datasets** with preserved metadata
3. **Normalize and select highly variable genes**
4. **Run Harmony batch correction** on PCA space
5. **Validate correction** with quantitative metrics (iLISI, ASW_batch)

## Step-by-Step Implementation

### Step 1: Define Batch Variable (BEFORE Concatenation)

```python
import scanpy as sc
import numpy as np

# CRITICAL: Define batch using dataset_id BEFORE merging
# Each Census dataset_id = 1 technical batch

# Healthy dataset
if 'dataset_id' in adata_healthy.obs.columns:
    adata_healthy.obs['batch'] = adata_healthy.obs['dataset_id'].astype(str)
    print(f"Healthy batches: {adata_healthy.obs['batch'].nunique()} datasets")
else:
    # Fallback if dataset_id missing
    adata_healthy.obs['batch'] = 'Healthy_Census'

# Disease dataset
if 'dataset_id' in adata_disease.obs.columns:
    adata_disease.obs['batch'] = adata_disease.obs['dataset_id'].astype(str)
    print(f"Disease batches: {adata_disease.obs['batch'].nunique()} datasets")
else:
    adata_disease.obs['batch'] = 'Disease_Census'

# Add condition labels (biological variable - NOT for batch correction)
adata_healthy.obs['condition'] = 'Healthy'
adata_disease.obs['condition'] = 'Disease'

# Harmonize cell type column names
adata_healthy.obs['unified_cell_type'] = adata_healthy.obs['cell_type']
adata_disease.obs['unified_cell_type'] = adata_disease.obs['cell_type']
```

### Step 2: Concatenate Datasets

```python
# Concatenate WITHOUT batch_categories parameter
# DO NOT use batch_categories - it overwrites dataset_id batches!
adata_combined = adata_healthy.concatenate(adata_disease)

print(f"\nCombined dataset:")
print(f"  Total cells: {adata_combined.n_obs:,}")
print(f"  Total batches (dataset_ids): {adata_combined.obs['batch'].nunique()}")
print(f"  Batch distribution:\n{adata_combined.obs['batch'].value_counts()}")

# VERIFY cell types and batches preserved
print(f"\nCell types: {adata_combined.obs['unified_cell_type'].nunique()} unique types")
print(f"Conditions: {adata_combined.obs['condition'].value_counts().to_dict()}")

# Validation checks
if adata_combined.obs['unified_cell_type'].isna().all():
    raise ValueError("Cell types lost during concatenation! Debug before continuing.")

expected_batches = (adata_healthy.obs['dataset_id'].nunique() +
                   adata_disease.obs['dataset_id'].nunique())
actual_batches = adata_combined.obs['batch'].nunique()
if actual_batches != expected_batches:
    raise ValueError(f"Batch variable incorrect! Expected {expected_batches}, got {actual_batches}")
```

### Step 3: Preserve Raw Counts & Normalization

**⚠️ CRITICAL: Save raw counts BEFORE normalization for pseudobulk DE analysis.**

```python
# CRITICAL: Preserve raw counts in a layer before normalization
# Pseudobulk DE (PyDESeq2) requires raw integer counts
adata_combined.layers['counts'] = adata_combined.X.copy()
print("✓ Raw counts preserved in adata.layers['counts']")

# Normalize to 10K counts per cell
sc.pp.normalize_total(adata_combined, target_sum=1e4)

# Log transform
sc.pp.log1p(adata_combined)

# Find HVGs accounting for batch effects
sc.pp.highly_variable_genes(
    adata_combined,
    batch_key='batch',  # Uses dataset_id batches
    n_top_genes=3000
)

print(f"Highly variable genes: {adata_combined.var['highly_variable'].sum()}")
```

**Why preserve raw counts:**
- Pseudobulk differential expression requires raw integer counts
- PyDESeq2 negative binomial model needs count data
- Normalization destroys count structure (creates floats)
- Saving in layer allows access to both raw and normalized data

### Step 4: Run Harmony Batch Correction

```python
# Scale and compute PCA on HVGs
sc.pp.scale(adata_combined, max_value=10)
sc.pp.pca(adata_combined, n_comps=50, use_highly_variable=True)

print(f"\nPCA computed on {adata_combined.var['highly_variable'].sum()} HVGs")
print(f"Harmony will correct {adata_combined.obs['batch'].nunique()} batches (dataset_ids)")

# Run Harmony integration
sc.external.pp.harmony_integrate(
    adata_combined,
    key='batch',  # Contains dataset_id values (NOT 'Healthy'/'Cancer')
    basis='X_pca',
    adjusted_basis='X_pca_harmony'
)

print(f"✓ Harmony complete - corrected {adata_combined.obs['batch'].nunique()} technical batches")

# Compute neighbors and UMAP using Harmony-corrected embeddings
sc.pp.neighbors(adata_combined, use_rep='X_pca_harmony', n_neighbors=15)
sc.tl.umap(adata_combined)
sc.tl.leiden(adata_combined, resolution=0.8)
```

### Step 5: Validate Batch Correction (MANDATORY)

Use TWO quantitative metrics: iLISI (integration LISI) and ASW_batch (Average Silhouette Width).

```python
import matplotlib.pyplot as plt
from sklearn.neighbors import NearestNeighbors
import scib

# Visual validation - before/after comparison
fig, axes = plt.subplots(1, 3, figsize=(15, 4))

sc.pl.pca(adata_combined, color='batch', ax=axes[0], show=False,
          title=f'PCA Before Harmony ({adata_combined.obs["batch"].nunique()} batches)')
sc.pl.pca(adata_combined, color='batch', use_rep='X_pca_harmony', ax=axes[1], show=False,
          title='PCA After Harmony')
sc.pl.umap(adata_combined, color='batch', ax=axes[2], show=False,
           title='UMAP After Harmony - Check Mixing')
plt.tight_layout()
plt.savefig(f'{figure_dir}/batch_correction_validation.png', dpi=300, bbox_inches='tight')
plt.close()

# Verify biological signal preserved
fig, axes = plt.subplots(1, 2, figsize=(12, 5))
sc.pl.umap(adata_combined, color='condition', ax=axes[0], show=False, title='By Condition')
sc.pl.umap(adata_combined, color='unified_cell_type', ax=axes[1], show=False, title='By Cell Type')
plt.tight_layout()
plt.savefig(f'{figure_dir}/biology_preservation.png', dpi=300, bbox_inches='tight')
plt.close()
```

## Quantitative Validation Metrics

See [VALIDATION_METRICS.md](VALIDATION_METRICS.md) for complete implementation of iLISI and ASW_batch calculations.

**Quick implementation**:

```python
import scib

# Metric 1: iLISI (integration Local Inverse Simpson's Index)
ilisi_score = calculate_ilisi(adata_combined, batch_key='batch', use_rep='X_pca_harmony', perplexity=30)

# Metric 2: ASW_batch (Average Silhouette Width)
asw_batch = scib.metrics.silhouette_batch(
    adata_combined,
    batch_key='batch',
    label_key='unified_cell_type',
    embed='X_pca_harmony',
    verbose=False
)

n_batches = adata_combined.obs['batch'].nunique()

print(f'\nBatch Correction Quality Metrics:')
print(f'  Number of batches (dataset_ids): {n_batches}')
print(f'  iLISI: {ilisi_score:.3f} (target: >{n_batches * 0.7:.1f}, max={n_batches}.0)')
print(f'  ASW_batch: {asw_batch:.3f} (target: >0.7)')

# Interpret results
ilisi_good = ilisi_score > (n_batches * 0.7)
asw_good = asw_batch > 0.7

if ilisi_good and asw_good:
    assessment = 'PASS - Good batch mixing'
elif ilisi_good or asw_good:
    assessment = 'MARGINAL - One metric passes, review visually'
else:
    assessment = 'FAIL - Poor mixing, consider alternatives'

print(f'  Overall Assessment: {assessment}')
print(f'\nInterpretation:')
print(f'  - iLISI {ilisi_score:.2f}/{n_batches}.0 = {100*ilisi_score/n_batches:.1f}% of perfect mixing')
print(f'  - Target is >70% mixing for good integration')
```

## Metric Interpretation

### iLISI (integration LISI)
- **Range**: 1.0 (no mixing) to N (perfect mixing), where N = number of batches
- **Target**: >70% of maximum = good mixing
- **Examples**:
  - 2 batches: iLISI >1.4 is good
  - 5 batches: iLISI >3.5 is good
  - 13 batches: iLISI >9.1 is good
  - 20 batches: iLISI >14.0 is good

### ASW_batch (Average Silhouette Width)
- **Range**: 0.0 to 1.0
- **Target**: >0.7 for good mixing
- Independent of number of batches

**Both metrics should agree**. If they conflict, inspect PCA/UMAP plots visually.

## Troubleshooting Poor Batch Mixing

If both metrics fail (iLISI <70% and ASW <0.7):

1. **Visual inspection**: Check UMAP colored by batch - are dataset_ids still separated?
2. **Try alternative methods**:
   - Increase Harmony iterations: `sc.external.pp.harmony_integrate(..., max_iter_harmony=20)`
   - Use scVI (deep learning): More powerful but slower
   - Use ComBat: Simpler, good for small datasets
3. **Check data compatibility**: Cross-platform datasets may be too different to integrate
4. **Document limitation**: If integration fails, proceed with caution OR abort

## Alternative Methods

### ComBat (Simpler, good for small datasets)
```python
sc.pp.combat(adata, key='batch')
sc.pp.neighbors(adata)
sc.tl.umap(adata)
```

### scVI (Deep learning, very powerful but slower)
```python
import scvi
scvi.model.SCVI.setup_anndata(adata, batch_key='batch')
model = scvi.model.SCVI(adata, n_latent=30)
model.train(max_epochs=400, early_stopping=True)
adata.obsm['X_scvi'] = model.get_latent_representation()
sc.pp.neighbors(adata, use_rep='X_scvi')
```

## Validation Checklist

- [ ] Batch variable contains dataset_ids (not just 'Healthy'/'Cancer')
- [ ] Before/after PCA plots generated (colored by batch)
- [ ] iLISI calculated (target >70% of n_batches)
- [ ] ASW_batch calculated (target >0.7)
- [ ] Both metrics pass OR visual inspection confirms mixing
- [ ] Cell types still cluster after Harmony (biology preserved)
- [ ] UMAP shows batch mixing, not batch separation
- [ ] Assessment documented in analysis report

## Expected Results

**Success criteria**:
- Dataset_ids should overlap on UMAP (technical variation removed)
- Conditions (Healthy vs Disease) should still separate (biology preserved)
- Cell types should form distinct clusters (biology preserved)

## Documentation Template

Include this in your methods section:

```markdown
## Batch Correction

**Method**: Harmony integration on PCA space (50 components) with dataset_id-based
batch variable (N={n_batches} batches).

**Validation**: iLISI = {ilisi_score:.2f} ({percent:.0f}% of perfect mixing),
ASW_batch = {asw_batch:.2f}. Assessment: {assessment}.

**Result**: Technical batch effects successfully removed while preserving biological
variation between conditions and cell types.
```
