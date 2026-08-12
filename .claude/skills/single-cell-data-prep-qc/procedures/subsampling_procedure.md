
# Large Dataset Subsampling Strategy

## Mandatory Rule

**HARD REQUIREMENT: When combined datasets exceed 100K cells, you MUST subsample. This is non-negotiable.**

## When to Apply

**Location in Pipeline:** Stage 1, Step 1.8 (immediately after download, BEFORE any QC or processing)

**Trigger**: If `adata_healthy.n_obs + adata_disease.n_obs > 100,000`

**Required action**: Subsample to **50,000 cells per data source** (100K total maximum)

## Why This Limit Exists

- **100K cells provides excellent statistical power** for differential expression and pathway analysis
- **Ensures analyses complete efficiently** (10-20 minutes for most steps)
- **Maintains analytical rigor** - does NOT excuse skipping QC, batch correction, or statistical tests
- **Context efficiency** - keeps analysis within comfortable token budget while providing robust statistics
- **Resource efficiency** - balances comprehensive analysis with practical constraints

## Why Subsample BEFORE QC?

**Critical rationale:**
- **QC operates on clean, representative data**: Subsampling preserves cell type proportions, ensuring QC metrics are calculated on a balanced sample
- **Efficiency**: QC on 100K cells is much faster than on 500K-1M cells
- **Consistency**: All downstream analyses operate on the same subsampled data, avoiding confusion about which version to use
- **Simplicity**: One subsampling step at the beginning, not conditional logic later in the pipeline

## Stratified Subsampling Strategy

**Why not simple random sampling?**
- Simple random sampling can lose rare donors (bad for pseudobulk DE)
- Can over-represent donors with many cells → single-donor bias
- May not preserve cell type distributions within donors

**Two-stage stratified approach:**
1. **Stage 1: Donor-level balancing** - Cap cells per donor to prevent dominance, preserve all donors
2. **Stage 2: Cell-type preservation** - Maintain biological cell type proportions within each donor

**Benefits for pseudobulk differential expression:**
- ✓ Preserves ALL donors → better statistical power
- ✓ Prevents single-donor dominance → reduces bias
- ✓ Maintains cell type biology within each donor
- ✓ Reproducible with random seed

## Implementation

### Utility Function Available

**Location:** `src/utils/sc_preprocessing.py`

A ready-to-use function `subsample_datasets_for_integration()` implements the two-stage stratified approach.

**Key features:**
- Preserves all donors (critical for pseudobulk)
- Caps cells per donor at `target / n_donors × 1.5`
- Maintains cell type proportions within each donor
- Reproducible with random seed

See the module docstring for algorithm details and examples.

### Step 1: Check Combined Size (MANDATORY)

```python
n_combined = adata_healthy.n_obs + adata_disease.n_obs

if n_combined > 100000:
    print(f'MANDATORY SUBSAMPLING: {n_combined:,} cells exceeds 100K limit')
    target_per_source = 50000
```

### Step 2: Two-Stage Stratified Subsampling (REQUIRED)

Use the utility function to subsample both datasets:

```python
from src.utils.sc_preprocessing import subsample_datasets_for_integration

# Apply stratified subsampling (only if needed)
if n_combined > 100000:
    adata_disease, adata_healthy = subsample_datasets_for_integration(
        adata_disease=adata_disease,
        adata_healthy=adata_healthy,
        target_per_source=50000,
        donor_col='donor_id',
        celltype_col='cell_type',
        random_state=42,
        verbose=True
    )

# Output shows:
# - Original vs final cell counts
# - Donors preserved in each dataset
# - Per-donor sampling decisions
# - Validation of donor balance
```

**What this does:**
1. For each dataset (disease, healthy):
   - Calculate max cells per donor = 50K / n_donors × 1.5
   - Iterate through each donor:
     - If donor ≤ max: keep all cells
     - If donor > max: subsample proportionally by cell type
2. Returns balanced datasets ready for QC

### Step 3: Remove Unnecessary Data (CRITICAL)

Reduce file size by removing extra layers and embeddings:

```python
# Remove all layers (keep only .X with raw counts)
adata_healthy.layers = {}
adata_disease.layers = {}

# Remove embeddings (will be computed fresh after QC and integration)
adata_healthy.obsm = {}
adata_healthy.varm = {}
adata_disease.obsm = {}
adata_disease.varm = {}

# Save to raw_dir with clear naming convention
# Stage 2 will load these files as input
adata_healthy.write_h5ad(f'{raw_dir}/healthy_raw_subsampled.h5ad')
adata_disease.write_h5ad(f'{raw_dir}/disease_raw_subsampled.h5ad')
print('Saved subsampled data (layers removed for efficiency)')
print('Stage 2 will load: *_raw_subsampled.h5ad files')
```

## Complete Workflow

```python
import scanpy as sc
import numpy as np
from src.utils.sc_preprocessing import subsample_datasets_for_integration

# Set random seed for reproducibility
np.random.seed(42)

# Load data (from MCP tools or other sources)
adata_healthy = sc.read_h5ad('healthy_raw.h5ad')
adata_disease = sc.read_h5ad('disease_raw.h5ad')

# Step 1: Check size (MANDATORY CHECK)
n_combined = adata_healthy.n_obs + adata_disease.n_obs
print(f'[Subsampling Check]')
print(f'Combined dataset: {n_combined:,} cells')

if n_combined > 100000:
    print(f'MANDATORY SUBSAMPLING: {n_combined:,} cells exceeds 100K limit')
    target_per_source = 50000
    print(f'Target: {target_per_source:,} cells per source ({target_per_source * 2:,} total)')
    print()

    # Step 2: Two-stage stratified subsampling - REQUIRED
    # This preserves donors and cell type proportions
    adata_disease, adata_healthy = subsample_datasets_for_integration(
        adata_disease=adata_disease,
        adata_healthy=adata_healthy,
        target_per_source=target_per_source,
        donor_col='donor_id',
        celltype_col='cell_type',
        random_state=42,
        verbose=True
    )
    print()

    # Step 3: CRITICAL - Remove unnecessary layers to reduce file size
    # Remove all layers (keep only .X with raw counts)
    adata_healthy.layers = {}
    adata_disease.layers = {}

    # Remove embeddings (will be computed fresh after QC)
    adata_healthy.obsm = {}
    adata_healthy.varm = {}
    adata_disease.obsm = {}
    adata_disease.varm = {}

    # Save to raw_dir with clear naming convention
    # Stage 2 will load these as input
    adata_healthy.write_h5ad(f'{raw_dir}/healthy_raw_subsampled.h5ad')
    adata_disease.write_h5ad(f'{raw_dir}/disease_raw_subsampled.h5ad')
    print('[Saving]')
    print(f'  {raw_dir}/healthy_raw_subsampled.h5ad')
    print(f'  {raw_dir}/disease_raw_subsampled.h5ad')
    print('  ✓ Layers removed for efficiency')
    print('  ✓ Stage 2 will load: *_raw_subsampled.h5ad files')
else:
    print(f'No subsampling needed: {n_combined:,} cells is within 100K limit')
    # Still save with consistent naming for Stage 2
    adata_healthy.write_h5ad(f'{raw_dir}/healthy_raw_subsampled.h5ad')
    adata_disease.write_h5ad(f'{raw_dir}/disease_raw_subsampled.h5ad')
```

## Documentation in Methods

Include this statement in your analysis report:

```markdown
## Methods

**Subsampling**: Performed mandatory two-stage stratified subsampling to 50,000 cells
per data source (100,000 total). Stage 1 ensured donor diversity by capping cells per
donor (target/n_donors × 1.5), preserving all donors for robust pseudobulk analysis.
Stage 2 maintained cell type proportions within each donor through stratified sampling.
This approach prevents single-donor bias while preserving biological heterogeneity
across donors and cell types (random_state=42 for reproducibility).
```

## Key Points

- **Performed in Stage 1** immediately after download, BEFORE any QC or processing
- **Two-stage stratified approach**:
  - Stage 1: Donor-level balancing (preserves all donors, caps per-donor cells)
  - Stage 2: Cell-type preservation (maintains proportions within donors)
- **Critical for pseudobulk DE**: All donors preserved, no single-donor bias
- **Uses utility function**: `src/utils/sc_preprocessing.subsample_datasets_for_integration()`
- **Subsample each dataset independently** before any concatenation
- **Remove unnecessary data** (layers, embeddings) to reduce file size to ~200-500MB
- **Set random seed** (random_state=42) for reproducibility
- **Saves as `*_raw_subsampled.h5ad`** which Stage 2 loads as input
- **Document in methods** - this is rigorous practice, not a limitation

## What This Does NOT Excuse

Within the 100K limit, you must still:
- ✅ Perform proper QC filtering
- ✅ Use robust batch correction (Harmony is default)
- ✅ Apply appropriate statistical tests with FDR correction
- ✅ Validate cell type annotations with marker genes
- ✅ Generate publication-quality visualizations (dpi=300)

The 100K subsampling rule exists for **practical efficiency**, NOT to compromise analytical quality.

## Checklist

- [ ] Checked if n_combined > 100,000
- [ ] Subsampled to 50K per source if needed
- [ ] Used stratified sampling (sc.pp.subsample with default settings)
- [ ] Set random_state=42 for reproducibility
- [ ] Removed unnecessary layers and embeddings
- [ ] Verified file sizes are reasonable (~100-300MB)
- [ ] Documented subsampling in methods section
