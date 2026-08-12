# Single-Cell Analysis Skills Interface Documentation

## Overview

The single-cell RNA-seq analysis workflow has been split into **two complementary skills** that work together to provide a complete pipeline from raw data to therapeutic target identification:

1. **`single-cell-data-prep-qc`** - Data preparation and quality control
2. **`single-cell-analysis`** - Statistical analysis and reporting

---

## Skill 1: single-cell-data-prep-qc

**Purpose:** Transform raw CELLxGENE Census data → clean, integrated AnnData object

**Scope:**
- Data discovery from CELLxGENE Census
- Quality control and filtering
- Doublet detection (Scrublet)
- Cell type harmonization
- Batch correction (Harmony)
- Comprehensive QC visualizations

**Input:** Disease name + tissue type (user request)

**Output:** `workspace/{date}/{run_id}/single_cell_analyst/data/processed/integrated.h5ad`

**Key characteristics:**
- Data engineering focused
- Technical QC and preprocessing
- ~30-40% of total workflow time
- Generates 9+ QC/integration figures

---

## Skill 2: single-cell-analysis

**Purpose:** Perform statistical analysis and generate publication-ready reports

**Scope:**
- Pseudobulk differential expression (PyDESeq2)
- Pathway enrichment (gseapy + decoupler)
- Critical review and quality evaluation
- Therapeutic target prioritization
- Final report generation

**Input:** `workspace/{date}/{run_id}/single_cell_analyst/data/processed/integrated.h5ad`

**Output:**
- DE results tables (`de_*.csv`)
- Pathway enrichment results (`pathway_*.csv`, `gsea_*.csv`)
- Visualizations (volcano plots, heatmaps, pathway dotplots)
- Three reports (DRAFT, CRITICAL_REVIEW, FINAL)

**Key characteristics:**
- Statistical analysis focused
- Biological interpretation
- ~60-70% of total workflow time
- Generates 15+ analysis figures

---

## Interface Contract

### Data Handoff Requirements

**Skill 1 (data-prep-qc) MUST produce:**

```python
# File: integrated.h5ad
# Location: workspace/{date}/{run_id}/single_cell_analyst/data/processed/

# Required contents:
- .X: Raw counts (integer counts, NOT normalized)
- .var_names: Gene symbols (NOT integers)
- .obs['condition']: Disease/healthy labels
- .obs['donor_id']: Donor identifiers (for pseudobulk DE)
- .obs['unified_cell_type'] or .obs['cell_type']: Cell type annotations
- .obsm['X_pca_harmony']: Batch-corrected embedding (for visualizations)
- .obs['dataset_id']: Batch labels (preserved from integration)
```

**Skill 2 (statistical-analysis) MUST validate:**

```python
# Validation checks before proceeding
import scanpy as sc
import numpy as np

adata = sc.read_h5ad('integrated.h5ad')

# Check 1: Gene symbols set
assert not adata.var_names[0].isdigit(), "Gene names must be symbols, not integers"

# Check 2: Raw counts preserved
assert np.all(np.equal(np.mod(adata.X.data[:1000], 1), 0)), "Counts must be integers"

# Check 3: Required columns exist
assert 'condition' in adata.obs.columns, "Missing 'condition' column"
assert 'unified_cell_type' in adata.obs.columns or 'cell_type' in adata.obs.columns, "Missing cell type annotations"

# Check 4: Donor metadata (preferred, not required)
has_donor = 'donor_id' in adata.obs.columns
print(f"Donor metadata available: {has_donor}")
```

### Workspace Continuity

**CRITICAL:** Both skills use the **same workspace** identified by `date` and `run_id`:

```python
# Skill 1 initializes
wm = WorkspaceManager(agent_name='single_cell_analyst')
current_date = wm.date  # e.g., '2025-11-22'
current_run_id = wm.run_id  # e.g., 'abc123de'

# Skill 1 outputs:
# - Date: 2025-11-22
# - Run ID: abc123de

# Skill 2 uses the SAME values
wm = WorkspaceManager(
    agent_name='single_cell_analyst',
    date='2025-11-22',      # From Skill 1 output
    run_id='abc123de'       # From Skill 1 output
)

# Both skills write to:
# workspace/2025-11-22/abc123de/single_cell_analyst/
```

**File organization:**
```
workspace/{date}/{run_id}/single_cell_analyst/
├── data/
│   ├── raw/
│   │   ├── disease_raw.h5ad              # Skill 1
│   │   ├── healthy_raw.h5ad              # Skill 1
│   │   ├── disease_raw_subsampled.h5ad   # Skill 1
│   │   └── healthy_raw_subsampled.h5ad   # Skill 1
│   └── processed/
│       ├── disease_qc.h5ad               # Skill 1
│       ├── healthy_qc.h5ad               # Skill 1
│       └── integrated.h5ad               # Skill 1 → Skill 2 interface
├── code/
│   └── scripts/
│       ├── 01_data_download.py           # Skill 1
│       ├── 02_qc_doublets.py             # Skill 1
│       ├── 03_batch_correction.py        # Skill 1
│       ├── 04_pseudobulk_de.py           # Skill 2
│       └── 05_pathway_enrichment.py      # Skill 2
├── results/
│   ├── figures/
│   │   ├── qc_*.png                      # Skill 1 (9+ figures)
│   │   ├── integration_*.png             # Skill 1
│   │   ├── de_*.png                      # Skill 2 (10+ figures)
│   │   └── pathway_*.png                 # Skill 2
│   ├── tables/
│   │   ├── cell_type_composition.csv     # Skill 1
│   │   ├── de_*.csv                      # Skill 2
│   │   └── pathway_*.csv                 # Skill 2
│   └── reports/
│       ├── data_prep_summary.md          # Skill 1
│       ├── analysis_report_DRAFT.md      # Skill 2
│       ├── CRITICAL_REVIEW.md            # Skill 2
│       └── FINAL_REPORT.md               # Skill 2
└── metadata.json                         # Both skills
```

---

## Usage Patterns

### Pattern 1: Full Sequential Pipeline (Typical)

```
User: "Analyze breast cancer single-cell data to identify therapeutic targets"

Agent: Invokes Skill 1 (single-cell-data-prep-qc)
  → Downloads data from CELLxGENE Census
  → Performs QC, doublet detection, harmonization
  → Applies batch correction with Harmony
  → Outputs: integrated.h5ad (ready for analysis)
  → Outputs: date=2025-11-22, run_id=abc123de

Agent: Invokes Skill 2 (single-cell-analysis)
  → Loads integrated.h5ad from Skill 1
  → Uses same date/run_id (2025-11-22/abc123de)
  → Runs pseudobulk DE and pathway enrichment
  → Generates visualizations
  → Creates reports with target recommendations
  → Outputs: FINAL_REPORT.md

User receives: Complete analysis with therapeutic targets identified
```

### Pattern 2: Reanalysis with Existing Clean Data

```
User: "I already have clean integrated data at workspace/2025-11-20/xyz789ab/.
       Re-run the statistical analysis with more stringent thresholds."

Agent: Skips Skill 1 (data already prepared)

Agent: Invokes Skill 2 (single-cell-analysis)
  → Uses existing date=2025-11-20, run_id=xyz789ab
  → Loads integrated.h5ad from that workspace
  → Runs DE and pathway enrichment with new parameters
  → Generates new visualizations and reports
  → Outputs overwrite previous analysis results

User receives: Updated analysis with new parameters
```

### Pattern 3: Data Prep Only (for Sharing or Intermediate QC)

```
User: "Prepare breast cancer single-cell data for analysis but don't run statistics yet.
       I want to review the integration first."

Agent: Invokes Skill 1 (single-cell-data-prep-qc)
  → Downloads and prepares data
  → Outputs: integrated.h5ad + QC visualizations
  → Outputs: data_prep_summary.md

User: Reviews QC figures and integration quality

User: "Looks good, proceed with analysis"

Agent: Invokes Skill 2 (single-cell-analysis)
  → Uses same workspace (date/run_id from Skill 1)
  → Completes statistical analysis
```

### Pattern 4: Troubleshooting/Re-prep

```
User: "The DE analysis failed because gene symbols weren't set properly.
       Fix the data prep."

Agent: Invokes Skill 1 (single-cell-data-prep-qc) ONLY
  → Uses NEW date/run_id (fresh workspace)
  → Re-downloads and processes with correct gene symbol mapping
  → Outputs: NEW integrated.h5ad

Agent: Invokes Skill 2 (single-cell-analysis)
  → Uses NEW workspace (date/run_id from fixed Skill 1 run)
  → Runs analysis successfully
```

---

## Visualization Guidelines (Both Skills)

### Skill 1 Visualizations

**QC Metrics:**
- Histograms of n_genes, total_counts, pct_mt (before/after filtering)
- Doublet score distributions
- Cell type composition bar charts

**Integration:**
- UMAP before/after batch correction
- Multi-panel UMAP (by condition, cell type, batch, donor)
- Batch mixing metrics (iLISI, ASW)

**Standards:**
- All figures: dpi=300
- Clear axis labels and titles
- Color-coded by condition (red=disease, blue=healthy)
- Save to `results/figures/`

### Skill 2 Visualizations

**Differential Expression:**
- Volcano plots (one per cell type)
- Heatmaps of top DE genes
- MA plots (optional)
- Gene expression violins for key targets

**Pathway Enrichment:**
- Dotplots (pathway significance vs NES)
- Bar charts of top pathways
- Enrichment networks (optional)
- Pathway summary across cell types

**Standards:**
- All figures: dpi=300
- Include statistical annotations (FDR, log2FC, NES)
- Color by direction (red=upregulated/activated, blue=downregulated/suppressed)
- Save to `results/figures/`

---

## Error Handling

### If Skill 2 validation fails:

**Error:** `Gene names are integers, not symbols`
**Solution:** Re-run Skill 1 with proper gene symbol mapping in QC step

**Error:** `Missing 'condition' column`
**Solution:** Re-run Skill 1, ensure condition labels added during batch correction

**Error:** `Counts are not integers (normalized data detected)`
**Solution:** Re-run Skill 1, preserve raw counts in `.X` throughout pipeline

### If Skill 1 outputs are incomplete:

**Check:** Does `integrated.h5ad` exist?
**Check:** Does `data_prep_summary.md` list all required components?
**Check:** Are all 9+ QC figures generated?

**If any missing:** Re-run Skill 1 completely, verify all checkpoints passed

---

## Transition Points

### From Skill 1 to Skill 2:

**Outputs communicated:**
1. `date` value (e.g., "2025-11-22")
2. `run_id` value (e.g., "abc123de")
3. Path to `integrated.h5ad`
4. Summary of data prep (cell counts, donor counts, cell types)

**Example handoff message:**
```
Data preparation complete!

Workspace: workspace/2025-11-22/abc123de/single_cell_analyst/
Integrated data: data/processed/integrated.h5ad

Summary:
- Total cells: 85,234
- Disease donors: 6
- Healthy donors: 4
- Cell types: 12 (after harmonization)
- Batch correction: ✅ Validated with iLISI

Ready for statistical analysis. Use the same date/run_id to continue.
```

### Within Skill 2 (Stage 1 to Stage 2):

**Stage 1 (DE + Pathways) outputs:**
- DE results tables for all cell types
- Pathway enrichment results
- Comprehensive visualizations

**Stage 2 (Review + Reports) inputs:**
- Reads all DE and pathway results
- Generates DRAFT report
- Performs critical review
- Creates FINAL report

---

## Skill Independence

### Can Skill 2 run without Skill 1?

**YES**, if you have a valid `integrated.h5ad` file that meets interface requirements.

**Requirements:**
- Must have raw counts in `.X`
- Must have gene symbols as `var_names`
- Must have `condition` and cell type columns
- Must be in expected workspace structure

**Use case:** Reanalysis, parameter tuning, or using data from external source

### Can Skill 1 run independently?

**YES**, Skill 1 can prepare data for:
- Manual inspection
- External analysis tools
- Sharing with collaborators
- Multiple downstream analyses

**Output:** Clean, integrated, batch-corrected dataset ready for any analysis

---

## Future Extensions

### Potential additional skills:

1. **single-cell-trajectory-analysis** - Pseudotime and lineage tracing
2. **single-cell-cell-communication** - CellChat, NicheNet, LIANA
3. **single-cell-perturbation-analysis** - CRISPR/drug screen analysis
4. **single-cell-spatial-analysis** - Spatial transcriptomics integration

**All would consume:** `integrated.h5ad` from Skill 1
**All would share:** Same workspace structure and visualization guidelines

---

## Summary

**Key principles:**
1. **Clean interface:** Skill 1 outputs integrated.h5ad, Skill 2 validates and analyzes
2. **Workspace continuity:** Both skills use same date/run_id
3. **Independent operation:** Either skill can be re-run without the other
4. **Comprehensive visualization:** Both skills generate publication-quality figures
5. **Quality assurance:** Validation at handoff point ensures compatibility

**Result:** Modular, maintainable, reusable single-cell analysis pipeline

---

**Last updated:** 2025-11-22
