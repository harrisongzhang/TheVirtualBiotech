
# Pseudobulk Differential Expression with PyDESeq2

## ⚠️ REQUIRED for Condition Comparisons

**When you MUST use this procedure:**
- Comparing conditions between donors (Disease vs Healthy, Treatment vs Control, etc.)
- You have biological replicates (multiple donors per condition)

**This is NOT optional** - pseudobulk aggregation is **REQUIRED** to avoid pseudoreplication and ensure valid statistical inference.

**For cancer cell comparisons (malignant vs normal within disease):** See [within_donor_meta_analysis.md](within_donor_meta_analysis.md)

---

## Why Pseudobulk Analysis is Required

**Problem**: Testing at the cell level treats cells as independent samples (**pseudoreplication**). Cells from the same donor are correlated - they share genetics, environment, and technical factors.

**Solution**: Aggregate cells by donor x condition (pseudobulk), then test at donor level. This is the **field standard** and statistically correct approach for condition comparisons.

**Bottom line**: Without pseudobulk, you're comparing cells (n=1000s, inflated) instead of donors (n=6, correct). This leads to false positives and invalid conclusions.

## Key Benefits

- **Proper sample size**: Tests at donor level (n=6 vs n=4), not cell level (n=1000s)
- **Avoids pseudoreplication**: Accounts for donor-to-donor correlation
- **Negative binomial model**: Appropriate for count data with overdispersion
- **Dispersion shrinkage**: Borrows information across genes for stable estimates
- **Field standard**: DESeq2 is the established method for RNA-seq differential expression

## Requirements

- **PyDESeq2 v0.5.2** (compatible with numpy 1.26.4) with optimized settings for speed
- **scanpy** for data handling and pseudobulk aggregation
- Donor/sample ID metadata in `adata.obs`
- Condition metadata (`Disease`/`Healthy` or similar) in `adata.obs`

**Performance:** Optimized configuration (`n_cpus=8`, `fit_type='mean'`, `refit_cooks=False`) provides ~10-15x speedup over defaults.

## Workflow

### Step 0: Find Matched Dataset (Before Download)

**⚠️ CRITICAL: Disease and healthy samples MUST come from the SAME dataset. NEVER compare across studies.**

```python
# Query metadata to identify datasets with BOTH conditions
from cellxgene_census import query_cell_metadata

metadata = query_cell_metadata(
    organism='homo_sapiens',
    tissue='breast',  # Adjust to your tissue
    disease=['breast cancer', 'normal']
)

# Find datasets with both disease AND healthy cells (>=50 each)
study_counts = metadata.groupby(['dataset_id', 'disease']).size().unstack(fill_value=0)
matched_datasets = study_counts[
    (study_counts['breast cancer'] >= 50) & (study_counts['normal'] >= 50)
]

if len(matched_datasets) == 0:
    print('✗ No datasets contain both disease and healthy samples')
    print('→ Use within_donor_meta_analysis.md for within-disease cell type comparisons instead')
    raise ValueError("No matched datasets available - cannot do cross-condition comparison")

# Select LARGEST matched dataset (by total cell count)
matched_datasets['total_cells'] = matched_datasets.sum(axis=1)
largest_dataset = matched_datasets['total_cells'].idxmax()

print(f'✓ Found {len(matched_datasets)} matched datasets')
print(f'✓ Selected largest: {largest_dataset} ({matched_datasets.loc[largest_dataset, "total_cells"]:.0f} cells)')

# Download from single matched dataset
adata = get_anndata(
    organism='homo_sapiens',
    dataset_id=[largest_dataset],  # Single largest dataset
    obs_value_filter="disease in ['breast cancer', 'normal']"
)
```

**Why this matters:** Comparing disease from Study A vs healthy from Study B confounds study effects (protocols, batches, platforms) with disease effects. Cross-study comparisons are statistically invalid.

### Step 1: Identify ALL Qualifying Cell Types

**⚠️ IMPORTANT: Analyze ALL cell types that meet sample size criteria, not just a subset.**

```python
import scanpy as sc
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
import pandas as pd
import numpy as np

# Load integrated data (from batch correction step)
# Should have raw counts preserved in adata.layers['counts']
adata = sc.read_h5ad(processed_dir / 'integrated.h5ad')

# Verify raw counts layer exists
if 'counts' not in adata.layers:
    raise ValueError("Raw counts layer not found! Must preserve raw counts during batch correction.")

print(f'✓ Raw counts layer found: {adata.layers["counts"].shape}')

# Identify ALL cell types with adequate representation in BOTH conditions
print('\nIdentifying cell types for DE analysis...')
cell_type_counts = adata.obs['unified_cell_type'].value_counts()

target_cell_types = []
for ct in cell_type_counts.index:
    ct_adata = adata[adata.obs['unified_cell_type'] == ct]
    disease_cells = (ct_adata.obs['condition'] == 'Disease').sum()
    healthy_cells = (ct_adata.obs['condition'] == 'Healthy').sum()

    # Minimum criteria: 50 cells per condition (ensures >=3 donors typically)
    if disease_cells >= 50 and healthy_cells >= 50:
        target_cell_types.append(ct)
        print(f'  ✓ {ct}: Disease={disease_cells}, Healthy={healthy_cells}')

print(f'\n✓ Selected {len(target_cell_types)} cell types for analysis')
print('  Analyzing ALL qualifying cell types (comprehensive analysis)')

# Begin loop through ALL qualifying cell types
for celltype in target_cell_types:
    print(f'\n{"="*80}')
    print(f'Analyzing {celltype}')
    print(f'{"="*80}')

    # Extract cell type
    adata_ct = adata[adata.obs['unified_cell_type'] == celltype].copy()
```

**Do NOT:**
- Arbitrarily limit to "top 5" or "top 10" cell types
- Stop after finding a few qualifying types
- Skip cell types based on personal judgment

**DO:**
- Analyze every cell type that meets the >=50 cells per condition threshold
- If many cell types qualify (e.g., 15+), that's good - analyze all of them
- Document which cell types were excluded and why (sample size)

### Step 2: Extract Raw Counts for Pseudobulk

**⚠️ CRITICAL: Use raw counts from the preserved 'counts' layer, not normalized values.**

**AnnData Structure Clarification:**
- **Original cell-level adata**: Raw counts in `adata.layers['counts']`, normalized data in `adata.X`
- **Pseudobulk adata (pdata)**: Aggregated counts go directly in `pdata.X` (PyDESeq2 requires counts in .X)

```python
# Extract raw counts from the preserved 'counts' layer
adata_ct_raw = adata_ct.copy()
adata_ct_raw.X = adata_ct.layers['counts'].copy()

# Verify raw counts (should be integers)
print(f'\nRaw counts verification:')
print(f'  Data type: {adata_ct_raw.X.dtype}')
print(f'  Max value: {adata_ct_raw.X.max():.0f}')
print(f'  Min value: {adata_ct_raw.X.min():.0f}')
print(f'  Sample (first cell, first 5 genes): {adata_ct_raw.X[0, :5]}')

# If counts are floats, convert to integers
if adata_ct_raw.X.dtype != int:
    adata_ct_raw.X = adata_ct_raw.X.astype(int)
    print('✓ Converted to integer counts')
```

### Step 3: Create Pseudobulk Aggregation

**⚠️ CRITICAL: Aggregate by donor_id x condition to create separate pseudobulk samples for each donor-condition combination.**

```python
# Create unique sample identifier: donor_id x condition
# This ensures we have separate pseudobulk samples for each donor in each condition
adata_ct_raw.obs['pseudobulk_sample'] = (
    adata_ct_raw.obs['donor_id'].astype(str) + '_' +
    adata_ct_raw.obs['condition'].astype(str)
)

print(f'\nCreating pseudobulk samples:')
print(f'  Total cells: {adata_ct_raw.n_obs:,}')
print(f'  Unique donors: {adata_ct_raw.obs["donor_id"].nunique()}')
print(f'  Unique pseudobulk samples: {adata_ct_raw.obs["pseudobulk_sample"].nunique()}')

# Manual pseudobulk aggregation: sum counts per pseudobulk sample
# Convert to DataFrame for aggregation
counts_df = pd.DataFrame(
    adata_ct_raw.X.toarray() if hasattr(adata_ct_raw.X, 'toarray') else adata_ct_raw.X,
    index=adata_ct_raw.obs['pseudobulk_sample'],
    columns=adata_ct_raw.var_names
)

# Sum counts by pseudobulk sample (donor x condition)
pseudobulk_counts = counts_df.groupby(level=0).sum()

# Create metadata for pseudobulk samples
pseudobulk_meta = adata_ct_raw.obs.groupby('pseudobulk_sample').agg({
    'donor_id': 'first',
    'condition': 'first'
}).reset_index(drop=True)

# Count cells per pseudobulk sample
pseudobulk_meta['n_cells'] = adata_ct_raw.obs.groupby('pseudobulk_sample').size().values

# Create pseudobulk AnnData object
# IMPORTANT: Pseudobulk counts go in .X (not in a layer)
# PyDESeq2 reads from .X by default - this is the correct structure
pdata = sc.AnnData(
    X=pseudobulk_counts.values.astype(int),
    obs=pseudobulk_meta,
    var=adata_ct_raw.var
)

# Result: n_donor_condition_pairs samples instead of n_cells
print(f'\nPseudobulk aggregation complete:')
print(f'  Pseudobulk samples: {pdata.n_obs}')
print(f'  Cells per sample: min={pdata.obs["n_cells"].min()}, max={pdata.obs["n_cells"].max()}')
print(f'  Conditions: {pdata.obs["condition"].value_counts().to_dict()}')
print(f'  Donors per condition:')
print(pdata.obs.groupby('condition')['donor_id'].nunique())
```

### Step 4: Verify Integer Counts for DESeq2

```python
# Verify counts are integers and reasonable
print(f'\nCount verification:')
print(f'  Data type: {pdata.X.dtype}')
print(f'  Mean counts per sample: {pdata.X.sum(axis=1).mean():.0f}')
print(f'  Median counts per sample: {np.median(pdata.X.sum(axis=1)):.0f}')
print(f'  Total counts: {pdata.X.sum():.0f}')
```

### Step 5: Run PyDESeq2

**Input structure for PyDESeq2:**
- `pdata.X`: Integer count matrix (pseudobulk aggregated counts)
- `pdata.obs`: Metadata with `donor_id`, `condition`, and `n_cells` columns
- Each row = one pseudobulk sample (donor x condition combination)

```python
# IMPORTANT: Use PyDESeq2 v0.5.2 (compatible with numpy 1.26.4)
# pdata.X contains the pseudobulk counts (already in .X, not in a layer)
dds = DeseqDataSet(
    adata=pdata,
    design_factors='condition',  # Will be deprecated, but works in v0.5.2
    n_cpus=8,              # Parallel processing (~5-8x speedup)
    fit_type='mean',       # Fast dispersion fitting (~2-3x speedup)
    refit_cooks=False,     # Skip outlier detection (~1.5x speedup)
    quiet=True             # Reduce output overhead
)

# Run DESeq2 normalization and dispersion estimation
print('\nRunning DESeq2 normalization and dispersion fitting...')
dds.deseq2()
print('✓ DESeq2 normalization and dispersion fitting complete')
```

### Step 6: Statistical Testing

```python
# Perform differential expression with contrast
stat_res = DeseqStats(
    dds,
    contrast=['condition', 'Disease', 'Healthy']  # [column, case, control]
)

# Run statistical tests
print('\nRunning statistical tests...')
stat_res.summary()
de_results = stat_res.results_df

print(f'Total genes tested: {len(de_results)}')
```

### Step 7: Filter for Significance

```python
# Filter for significant genes
sig_genes = de_results[
    (de_results['padj'] < 0.05) &
    (de_results['log2FoldChange'].abs() > 0.5)
]

print(f'\nSignificant genes (padj<0.05, |log2FC|>0.5): {len(sig_genes)}')
print(f'  Upregulated: {len(sig_genes[sig_genes["log2FoldChange"] > 0])}')
print(f'  Downregulated: {len(sig_genes[sig_genes["log2FoldChange"] < 0])}')

# Sort by significance
sig_genes_sorted = sig_genes.sort_values('padj')

# Save results
sig_genes_sorted.to_csv(f'{table_dir}/de_genes_{celltype}.csv')

# Show top upregulated and downregulated
print(f'\nTop 10 upregulated genes:')
print(sig_genes_sorted.nlargest(10, 'log2FoldChange')[['log2FoldChange', 'padj']])

print(f'\nTop 10 downregulated genes:')
print(sig_genes_sorted.nsmallest(10, 'log2FoldChange')[['log2FoldChange', 'padj']])
```

### Step 8: Prepare Ranked Gene List for Pathway Enrichment

```python
# Create ranked gene list for GSEA
# Rank by: -log10(padj) * sign(log2FC) for directionality
gene_ranks = pd.Series(
    -np.log10(de_results['padj'] + 1e-300) * np.sign(de_results['log2FoldChange']),
    index=de_results.index
)

# Remove NaN and Inf values
gene_ranks = gene_ranks.replace([np.inf, -np.inf], np.nan).dropna()

# Sort by rank
gene_ranks = gene_ranks.sort_values(ascending=False)

print(f'\nRanked gene list prepared: {len(gene_ranks)} genes')

# Save for pathway enrichment
gene_ranks.to_csv(f'{table_dir}/gene_ranks_{celltype}.csv')
```

## Complete Example Script

```python
#!/usr/bin/env python3
"""Pseudobulk differential expression analysis for a cell type"""

import sys
sys.path.insert(0, '/path/to/TheVirtualBiotech')

import scanpy as sc
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
import pandas as pd
import numpy as np
from src.utils.workspace_manager import WorkspaceManager

# Setup
wm = WorkspaceManager(agent_name='single_cell_analyst')
processed_dir = wm.get_data_path('processed')
table_dir = wm.get_results_path('tables')

# Load integrated data
adata = sc.read_h5ad(f'{processed_dir}/integrated.h5ad')

# Verify raw counts layer exists
if 'counts' not in adata.layers:
    raise ValueError("Raw counts layer missing! Check batch correction step.")

# Identify ALL cell types with adequate sample size in BOTH conditions
print('Identifying qualifying cell types...')
cell_type_counts = adata.obs['unified_cell_type'].value_counts()

target_cell_types = []
for ct in cell_type_counts.index:
    ct_adata = adata[adata.obs['unified_cell_type'] == ct]
    disease_cells = (ct_adata.obs['condition'] == 'Disease').sum()
    healthy_cells = (ct_adata.obs['condition'] == 'Healthy').sum()

    # Criteria: Minimum 50 cells per condition
    if disease_cells >= 50 and healthy_cells >= 50:
        target_cell_types.append(ct)
        print(f'  ✓ {ct}: D={disease_cells}, H={healthy_cells}')

print(f'\n✓ Analyzing ALL {len(target_cell_types)} qualifying cell types')

# Analyze EACH qualifying cell type
for celltype in target_cell_types:
    print(f'\n{"="*80}')
    print(f'Analyzing {celltype}')
    print(f'{"="*80}')

    # Extract cell type
    adata_ct = adata[adata.obs['unified_cell_type'] == celltype].copy()

    # Use raw counts
    adata_ct.X = adata_ct.layers['counts'].copy()

    # Create pseudobulk samples: donor_id x condition
    adata_ct.obs['pseudobulk_sample'] = (
        adata_ct.obs['donor_id'].astype(str) + '_' +
        adata_ct.obs['condition'].astype(str)
    )

    # Aggregate by pseudobulk sample
    counts_df = pd.DataFrame(
        adata_ct.X.toarray() if hasattr(adata_ct.X, 'toarray') else adata_ct.X,
        index=adata_ct.obs['pseudobulk_sample'],
        columns=adata_ct.var_names
    )
    pseudobulk_counts = counts_df.groupby(level=0).sum()

    # Create metadata
    pseudobulk_meta = adata_ct.obs.groupby('pseudobulk_sample').agg({
        'donor_id': 'first',
        'condition': 'first'
    }).reset_index(drop=True)
    pseudobulk_meta['n_cells'] = adata_ct.obs.groupby('pseudobulk_sample').size().values

    # Create pseudobulk AnnData
    pdata = sc.AnnData(
        X=pseudobulk_counts.values.astype(int),
        obs=pseudobulk_meta,
        var=adata_ct.var
    )

    # Run DESeq2 with optimized parameters
    dds = DeseqDataSet(
        adata=pdata,
        design_factors='condition',
        n_cpus=8,              # Parallel processing
        fit_type='mean',       # Fast dispersion fitting
        refit_cooks=False,     # Skip outlier detection
        quiet=True
    )
    dds.deseq2()

    # Statistical testing
    stat_res = DeseqStats(dds, contrast=['condition', 'Disease', 'Healthy'])
    stat_res.summary()
    de_results = stat_res.results_df

    # Filter significant
    sig_genes = de_results[
        (de_results['padj'] < 0.05) &
        (de_results['log2FoldChange'].abs() > 0.5)
    ]

    # Save
    sig_genes.sort_values('padj').to_csv(f'{table_dir}/de_genes_{celltype}.csv')

    print(f'✓ {celltype}: {len(sig_genes)} significant genes')

print('\n✓ All cell types analyzed')
```

## Important Notes

### Version Requirements

- **PyDESeq2 v0.5.2** is required for compatibility with numpy 1.26.4
- The `design_factors` parameter will be deprecated in future versions but works in v0.5.2

### Sample Size Considerations

- **Minimum**: 3 samples per condition (donors)
- **Recommended**: ≥6 samples per condition for reliable dispersion estimation
- **With <3 samples**: Consider alternative methods (e.g., Wilcoxon rank-sum at cell level with caution about pseudoreplication)

### Contrast Specification

The contrast parameter format is: `['column_name', 'case', 'control']`

Examples:
```python
# Disease vs Healthy
contrast=['condition', 'Disease', 'Healthy']

# COVID vs Control
contrast=['condition', 'COVID-19', 'normal']

# Treated vs Untreated
contrast=['treatment', 'Treated', 'Untreated']
```

### Donor ID Metadata

Ensure your AnnData has proper donor/sample IDs:

```python
# Check donor metadata
print(f'Donor IDs available: {adata.obs["donor_id"].nunique()}')
print(f'Samples per condition:')
print(adata.obs.groupby('condition')['donor_id'].nunique())
```

If donor_id is missing, use available sample identifiers or create pseudo-donors if necessary (less ideal).

## Advantages Over Cell-Level Testing

| Cell-Level (Wilcoxon) | Pseudobulk (DESeq2) |
|------------------------|---------------------|
| Treats cells as independent | Accounts for donor correlation |
| n = thousands (inflated) | n = donors (correct) |
| Pseudoreplication problem | Proper replication |
| No dispersion modeling | Negative binomial with shrinkage |
| Less robust p-values | More conservative, reliable p-values |

## Checklist

- [ ] **Identified ALL qualifying cell types** (>=50 cells per condition in both disease and healthy)
- [ ] **Analyzing ALL qualifying types** (not stopping at arbitrary limit like "top 5")
- [ ] Verified donor_id metadata available in integrated data
- [ ] Verified raw counts layer exists in integrated data
- [ ] For each cell type: Extracted raw counts from adata.layers['counts']
- [ ] For each cell type: Created pseudobulk aggregation (sum counts per donor)
- [ ] For each cell type: Ensured integer counts for DESeq2
- [ ] For each cell type: Ran DESeq2 normalization and dispersion fitting
- [ ] For each cell type: Performed statistical testing with proper contrast
- [ ] For each cell type: Filtered for significance (padj<0.05, |log2FC|>0.5)
- [ ] For each cell type: Saved DE results and ranked gene list
- [ ] Documented sample sizes (number of donors) for each cell type in methods
