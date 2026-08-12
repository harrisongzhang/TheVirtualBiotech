# Stage 1: Statistical Analysis (DE + Pathways)

## Overview

Stage 1 performs the core statistical analyses: pseudobulk differential expression and pathway enrichment. This stage transforms clean integrated data into biological insights with comprehensive visualizations.

**Time allocation:** ~60% of total analysis time

**Critical requirement:** Both checkpoints are MANDATORY and cannot be skipped.

---

## Stage 1 Checklist

Copy this checklist and check off items as you complete them:

```
Stage 1 Progress:
- [ ] Step 1.1: Load and validate integrated.h5ad
- [ ] Step 1.2: Explore cell types and sample sizes
- [ ] Step 1.3: Checkpoint 1 - Pseudobulk differential expression (MANDATORY)
- [ ] Step 1.4: Generate DE visualizations
- [ ] Step 1.5: Checkpoint 2 - Pathway enrichment (MANDATORY)
- [ ] Step 1.6: Generate pathway visualizations
- [ ] Step 1.7: Create analysis summary tables
- [ ] Step 1.8: Validate all outputs exist
```

---

## Step 1.1: Load and Validate Integrated Data

### Initialize Workspace

```python
import sys
sys.path.insert(0, '/path/to/TheVirtualBiotech')
from src.utils.workspace_manager import WorkspaceManager
import scanpy as sc
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Set plotting defaults
sc.settings.set_figure_params(dpi=300, facecolor='white', frameon=False)
sns.set_style("whitegrid")

# CRITICAL: Use stored date/run_id from data prep
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

### Load Integrated Data

```python
# Load integrated data from data prep skill
adata = sc.read_h5ad(processed_dir / 'integrated.h5ad')

print(f"\n[Loaded Integrated Data]")
print(f"  Cells: {adata.n_obs:,}")
print(f"  Genes: {adata.n_vars:,}")
```

### Validate Input Requirements

```python
print("\n[Input Validation]")

# Check gene symbols
is_gene_symbols = not adata.var_names[0].isdigit()
print(f"  ✅ Gene symbols set: {is_gene_symbols}" if is_gene_symbols else f"  ❌ ERROR: Gene names are integers, not symbols")

# Check raw counts preserved
is_integer = np.all(np.equal(np.mod(adata.X.data[:1000], 1), 0))  # Sample first 1000 values
print(f"  ✅ Raw counts preserved: {is_integer}" if is_integer else f"  ⚠️ WARNING: Counts may be normalized")

# Check required columns
has_condition = 'condition' in adata.obs.columns
has_donor_id = 'donor_id' in adata.obs.columns
has_cell_type = 'unified_cell_type' in adata.obs.columns or 'cell_type' in adata.obs.columns

print(f"  ✅ Has condition column: {has_condition}" if has_condition else f"  ❌ ERROR: Missing 'condition' column")
print(f"  ✅ Has donor_id column: {has_donor_id}" if has_donor_id else f"  ⚠️ WARNING: No donor_id - pseudobulk not possible")
print(f"  ✅ Has cell type column: {has_cell_type}" if has_cell_type else f"  ❌ ERROR: Missing cell type annotations")

# Check Harmony embedding
has_harmony = 'X_pca_harmony' in adata.obsm.keys()
print(f"  ✅ Has Harmony embedding: {has_harmony}" if has_harmony else f"  ⚠️ INFO: No Harmony embedding - visualization may be limited")

# Error if critical requirements not met
if not is_gene_symbols:
    raise ValueError("❌ CRITICAL: Gene symbols not set. Re-run data prep with gene symbol mapping.")
if not has_condition:
    raise ValueError("❌ CRITICAL: No condition column. Cannot perform disease vs healthy comparison.")
if not has_cell_type:
    raise ValueError("❌ CRITICAL: No cell type annotations. Cannot perform cell-type-specific analysis.")

print("\n✅ Input validation passed - data ready for analysis")
```

---

## Step 1.2: Explore Cell Types and Sample Sizes

### Identify Cell Types for Analysis

```python
cell_type_col = 'unified_cell_type' if 'unified_cell_type' in adata.obs.columns else 'cell_type'

print(f"\n[Cell Type Analysis]")
print(f"  Using column: {cell_type_col}")

# Count cells per type and condition
ct_condition_counts = adata.obs.groupby([cell_type_col, 'condition']).size().unstack(fill_value=0)
print(f"\nCells per cell type and condition:")
print(ct_condition_counts)

# If donor_id exists, count donors per type and condition
if has_donor_id:
    donor_counts = adata.obs.groupby([cell_type_col, 'condition'])['donor_id'].nunique().unstack(fill_value=0)
    print(f"\nDonors per cell type and condition:")
    print(donor_counts)

    # Identify cell types with adequate sample size for DE
    # Requirement: ≥3 donors per condition AND ≥50 cells per condition
    adequate_cell_types = []
    for ct in donor_counts.index:
        n_donors_disease = donor_counts.loc[ct, 'disease']
        n_donors_healthy = donor_counts.loc[ct, 'healthy']
        n_cells_disease = ct_condition_counts.loc[ct, 'disease']
        n_cells_healthy = ct_condition_counts.loc[ct, 'healthy']

        if (n_donors_disease >= 3 and n_donors_healthy >= 3 and
            n_cells_disease >= 50 and n_cells_healthy >= 50):
            adequate_cell_types.append(ct)

    print(f"\n[Cell Types for Pseudobulk DE]")
    print(f"  Total cell types: {len(donor_counts)}")
    print(f"  Adequate sample size (≥3 donors, ≥50 cells per condition): {len(adequate_cell_types)}")
    print(f"  Cell types to analyze: {adequate_cell_types}")

    if len(adequate_cell_types) == 0:
        print("  ⚠️ WARNING: No cell types meet sample size criteria for robust pseudobulk DE")
else:
    print("\n⚠️ No donor_id available - will use cell-level testing (suboptimal)")
```

---

## Step 1.3: Checkpoint 1 - ⚠️ MANDATORY Pseudobulk Differential Expression

**STOP. This is a mandatory checkpoint that CANNOT be skipped.**

### Follow Pseudobulk DE Procedure

**See [procedures/pseudobulk_de_procedure.md](../procedures/pseudobulk_de_procedure.md)**

**The procedure will guide you through:**
1. Finding largest matched dataset (disease + healthy in same study)
2. Identifying ALL qualifying cell types
3. Extracting raw counts for each cell type
4. Creating pseudobulk aggregation (donor x condition)
5. Running PyDESeq2 differential expression
6. Filtering for significance (FDR<0.05, |log2FC|>0.5)
7. Saving results for each cell type

**Expected output:**
- `results/tables/de_genes_{celltype}.csv` for each analyzed cell type
- Each file contains columns: gene, baseMean, log2FoldChange, lfcSE, stat, pvalue, padj

**YOU MUST NOT:**
- Use `sc.tl.rank_genes_groups()` with Wilcoxon when donor_id exists
- Treat cells as independent samples
- Skip this checkpoint
- Proceed without DE results

---

## Step 1.4: Generate DE Visualizations

After completing the pseudobulk DE procedure, generate comprehensive visualizations.

### 📊 VISUALIZATION 1: Volcano Plots for Each Cell Type

```python
import glob

# Get all DE result files
de_files = glob.glob(str(table_dir / 'de_genes_*.csv'))

print(f"\n[Generating DE Visualizations]")
print(f"  Found {len(de_files)} DE result files")

for de_file in de_files:
    # Extract cell type from filename
    cell_type = de_file.split('de_genes_')[1].replace('.csv', '').replace('_', ' ')

    # Load results
    de_results = pd.read_csv(de_file)

    # Add significance column
    de_results['significant'] = (de_results['padj'] < 0.05) & (np.abs(de_results['log2FoldChange']) > 0.5)
    de_results['direction'] = 'Not significant'
    de_results.loc[(de_results['significant']) & (de_results['log2FoldChange'] > 0), 'direction'] = 'Upregulated'
    de_results.loc[(de_results['significant']) & (de_results['log2FoldChange'] < 0), 'direction'] = 'Downregulated'

    # Volcano plot
    fig, ax = plt.subplots(figsize=(10, 8))

    # Plot non-significant points
    ns = de_results[de_results['direction'] == 'Not significant']
    ax.scatter(ns['log2FoldChange'], -np.log10(ns['pvalue']),
               c='lightgray', alpha=0.5, s=10, label='Not significant')

    # Plot upregulated
    up = de_results[de_results['direction'] == 'Upregulated']
    ax.scatter(up['log2FoldChange'], -np.log10(up['pvalue']),
               c='red', alpha=0.7, s=20, label=f'Upregulated (n={len(up)})')

    # Plot downregulated
    down = de_results[de_results['direction'] == 'Downregulated']
    ax.scatter(down['log2FoldChange'], -np.log10(down['pvalue']),
               c='blue', alpha=0.7, s=20, label=f'Downregulated (n={len(down)})')

    # Add threshold lines
    ax.axhline(y=-np.log10(0.05), color='black', linestyle='--', linewidth=1, alpha=0.5)
    ax.axvline(x=-1, color='black', linestyle='--', linewidth=1, alpha=0.5)
    ax.axvline(x=1, color='black', linestyle='--', linewidth=1, alpha=0.5)

    # Labels and title
    ax.set_xlabel('log2 Fold Change (Disease vs Healthy)', fontsize=12, fontweight='bold')
    ax.set_ylabel('-log10(p-value)', fontsize=12, fontweight='bold')
    ax.set_title(f'Differential Expression: {cell_type}', fontsize=14, fontweight='bold')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Annotate top genes (top 5 up and down by significance)
    top_up = up.nsmallest(5, 'padj')
    top_down = down.nsmallest(5, 'padj')

    for _, gene in top_up.iterrows():
        ax.annotate(gene['gene'],
                    xy=(gene['log2FoldChange'], -np.log10(gene['pvalue'])),
                    xytext=(5, 5), textcoords='offset points',
                    fontsize=8, alpha=0.7)

    for _, gene in top_down.iterrows():
        ax.annotate(gene['gene'],
                    xy=(gene['log2FoldChange'], -np.log10(gene['pvalue'])),
                    xytext=(5, 5), textcoords='offset points',
                    fontsize=8, alpha=0.7)

    plt.tight_layout()
    plt.savefig(figure_dir / f'de_volcano_{cell_type.replace(" ", "_")}.png', dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: de_volcano_{cell_type.replace(' ', '_')}.png")
```

### 📊 VISUALIZATION 2: Heatmap of Top DE Genes

```python
# For each cell type with significant DE genes, create heatmap
for de_file in de_files:
    cell_type = de_file.split('de_genes_')[1].replace('.csv', '').replace('_', ' ')
    de_results = pd.read_csv(de_file)

    # Get top 50 DE genes by significance
    top_genes = de_results.nsmallest(50, 'padj')

    if len(top_genes) == 0:
        print(f"  ⚠️ No significant genes for {cell_type}, skipping heatmap")
        continue

    # Extract expression data for this cell type
    adata_ct = adata[adata.obs[cell_type_col] == cell_type].copy()

    # Get expression for top genes (use log-normalized for visualization)
    sc.pp.normalize_total(adata_ct, target_sum=1e4)
    sc.pp.log1p(adata_ct)

    # Filter to top genes
    genes_in_data = [g for g in top_genes['gene'].values if g in adata_ct.var_names]
    adata_ct = adata_ct[:, genes_in_data].copy()

    # Create heatmap
    fig, ax = plt.subplots(figsize=(12, 10))

    # Get expression matrix
    expr_df = pd.DataFrame(
        adata_ct.X.toarray() if hasattr(adata_ct.X, 'toarray') else adata_ct.X,
        index=adata_ct.obs_names,
        columns=adata_ct.var_names
    )

    # Add condition annotation
    expr_df['condition'] = adata_ct.obs['condition'].values

    # Average by condition
    expr_avg = expr_df.groupby('condition').mean().T

    # Plot heatmap
    sns.heatmap(expr_avg, cmap='RdBu_r', center=expr_avg.mean().mean(),
                cbar_kws={'label': 'log(CPM+1)'}, ax=ax)

    ax.set_title(f'Top 50 DE Genes: {cell_type}', fontsize=14, fontweight='bold')
    ax.set_ylabel('Gene', fontsize=12)
    ax.set_xlabel('Condition', fontsize=12)

    plt.tight_layout()
    plt.savefig(figure_dir / f'de_heatmap_{cell_type.replace(" ", "_")}.png', dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: de_heatmap_{cell_type.replace(' ', '_')}.png")
```

---

## Step 1.5: Checkpoint 2 - ⚠️ MANDATORY Pathway Enrichment

**STOP. This is a mandatory checkpoint that CANNOT be skipped.**

### Verify DE Results Exist

```python
de_files = glob.glob(str(table_dir / 'de_genes_*.csv'))

print(f"\n[Pathway Enrichment Prerequisites]")
print(f"  DE result files: {len(de_files)}")

if len(de_files) == 0:
    raise ValueError("❌ Cannot perform pathway enrichment - no DE results")

print("✅ DE results exist - pathway enrichment REQUIRED")
```

### Follow Pathway Enrichment Procedure

**See [procedures/pathway_enrichment_procedure.md](../procedures/pathway_enrichment_procedure.md)**

**The procedure will guide you through:**
1. Preparing ranked gene lists from DE results
2. Running gseapy GSEA across multiple databases (Hallmark, KEGG, Reactome)
3. Running decoupler multi-method consensus (PROGENy, DoRothEA)
4. Identifying convergent pathways (both tools)
5. Saving all results

**Expected output:**
- `results/tables/gsea_{database}_{celltype}.csv` for each cell type and database
- `results/tables/decoupler_pathways_{celltype}.csv` for each cell type
- Pathway enrichment summary tables

**YOU MUST NOT:**
- Skip pathway enrichment
- Treat this as optional
- Proceed to Stage 2 without pathway results

---

## Step 1.6: Generate Pathway Visualizations

After completing the pathway enrichment procedure, generate visualizations.

### 📊 VISUALIZATION 3: Pathway Enrichment Dotplots

```python
import glob

# Get GSEA result files
gsea_files = glob.glob(str(table_dir / 'gsea_*.csv'))

print(f"\n[Generating Pathway Visualizations]")
print(f"  Found {len(gsea_files)} GSEA result files")

for gsea_file in gsea_files:
    # Extract cell type and database from filename
    parts = gsea_file.split('gsea_')[1].replace('.csv', '').split('_')
    database = parts[0]
    cell_type = '_'.join(parts[1:]).replace('_', ' ')

    # Load results
    gsea_results = pd.read_csv(gsea_file)

    # Filter to top 20 pathways by significance
    top_pathways = gsea_results.nsmallest(20, 'FDR q-val')

    if len(top_pathways) == 0:
        print(f"  ⚠️ No significant pathways for {cell_type} ({database})")
        continue

    # Create dotplot
    fig, ax = plt.subplots(figsize=(10, max(6, len(top_pathways) * 0.4)))

    # Prepare data
    top_pathways = top_pathways.sort_values('NES', ascending=True)

    # Create color map based on NES
    colors = ['red' if nes > 0 else 'blue' for nes in top_pathways['NES']]

    # Scatter plot
    scatter = ax.scatter(top_pathways['NES'], range(len(top_pathways)),
                        s=100*(-np.log10(top_pathways['FDR q-val'])),
                        c=colors, alpha=0.7, edgecolors='black', linewidth=0.5)

    # Labels
    ax.set_yticks(range(len(top_pathways)))
    ax.set_yticklabels(top_pathways['Term'], fontsize=9)
    ax.set_xlabel('Normalized Enrichment Score (NES)', fontsize=12, fontweight='bold')
    ax.set_title(f'Top Pathways ({database}): {cell_type}', fontsize=14, fontweight='bold')
    ax.axvline(x=0, color='black', linestyle='--', linewidth=1, alpha=0.5)
    ax.grid(True, alpha=0.3, axis='x')

    # Add legend for dot size
    handles, labels = scatter.legend_elements(prop="sizes", alpha=0.6, num=4)
    legend = ax.legend(handles, ['-log10(FDR)'] * len(handles),
                      loc="lower right", title="Significance")

    plt.tight_layout()
    plt.savefig(figure_dir / f'pathway_dotplot_{database}_{cell_type.replace(" ", "_")}.png',
                dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: pathway_dotplot_{database}_{cell_type.replace(' ', '_')}.png")
```

### 📊 VISUALIZATION 4: Top Pathways Bar Chart Summary

```python
# Create summary bar chart across all cell types
all_gsea = []
for gsea_file in gsea_files:
    parts = gsea_file.split('gsea_')[1].replace('.csv', '').split('_')
    database = parts[0]
    cell_type = '_'.join(parts[1:])

    gsea_results = pd.read_csv(gsea_file)
    gsea_results['cell_type'] = cell_type
    gsea_results['database'] = database
    all_gsea.append(gsea_results)

if len(all_gsea) > 0:
    all_gsea_df = pd.concat(all_gsea, ignore_index=True)

    # Get top 10 most significant pathways overall
    top_pathways_overall = all_gsea_df.nsmallest(10, 'FDR q-val')

    fig, ax = plt.subplots(figsize=(12, 8))

    # Bar chart
    bars = ax.barh(range(len(top_pathways_overall)),
                   top_pathways_overall['NES'],
                   color=['red' if nes > 0 else 'blue' for nes in top_pathways_overall['NES']],
                   alpha=0.7)

    ax.set_yticks(range(len(top_pathways_overall)))
    ax.set_yticklabels([f"{row['Term'][:40]}... ({row['cell_type']})"
                        for _, row in top_pathways_overall.iterrows()], fontsize=9)
    ax.set_xlabel('Normalized Enrichment Score (NES)', fontsize=12, fontweight='bold')
    ax.set_title('Top 10 Most Significant Pathways (All Cell Types)', fontsize=14, fontweight='bold')
    ax.axvline(x=0, color='black', linestyle='--', linewidth=1)
    ax.grid(True, alpha=0.3, axis='x')

    plt.tight_layout()
    plt.savefig(figure_dir / 'pathway_summary_top10.png', dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: pathway_summary_top10.png")
```

---

## Step 1.7: Create Analysis Summary Tables

### Generate DE Summary Table

```python
# Compile summary of DE results across all cell types
de_summary = []

for de_file in de_files:
    cell_type = de_file.split('de_genes_')[1].replace('.csv', '')
    de_results = pd.read_csv(de_file)

    n_sig = (de_results['padj'] < 0.05).sum()
    n_up = ((de_results['padj'] < 0.05) & (de_results['log2FoldChange'] > 0.5)).sum()
    n_down = ((de_results['padj'] < 0.05) & (de_results['log2FoldChange'] < -0.5)).sum()

    top_gene_up = de_results[de_results['log2FoldChange'] > 0].nsmallest(1, 'padj')
    top_gene_down = de_results[de_results['log2FoldChange'] < 0].nsmallest(1, 'padj')

    de_summary.append({
        'cell_type': cell_type,
        'n_significant_fdr05': n_sig,
        'n_upregulated_fc1': n_up,
        'n_downregulated_fc1': n_down,
        'top_upregulated_gene': top_gene_up['gene'].values[0] if len(top_gene_up) > 0 else 'None',
        'top_upregulated_log2fc': top_gene_up['log2FoldChange'].values[0] if len(top_gene_up) > 0 else np.nan,
        'top_downregulated_gene': top_gene_down['gene'].values[0] if len(top_gene_down) > 0 else 'None',
        'top_downregulated_log2fc': top_gene_down['log2FoldChange'].values[0] if len(top_gene_down) > 0 else np.nan
    })

de_summary_df = pd.DataFrame(de_summary)
de_summary_df.to_csv(table_dir / 'de_summary_all_cell_types.csv', index=False)

print("\n✓ Saved: de_summary_all_cell_types.csv")
print(de_summary_df)
```

### Generate Pathway Summary Table

```python
# Compile summary of pathway results across all cell types
pathway_summary = []

for gsea_file in gsea_files:
    parts = gsea_file.split('gsea_')[1].replace('.csv', '').split('_')
    database = parts[0]
    cell_type = '_'.join(parts[1:])

    gsea_results = pd.read_csv(gsea_file)

    n_sig = (gsea_results['FDR q-val'] < 0.25).sum()
    top_pathway = gsea_results.nsmallest(1, 'FDR q-val')

    pathway_summary.append({
        'cell_type': cell_type,
        'database': database,
        'n_significant_fdr025': n_sig,
        'top_pathway': top_pathway['Term'].values[0] if len(top_pathway) > 0 else 'None',
        'top_pathway_nes': top_pathway['NES'].values[0] if len(top_pathway) > 0 else np.nan,
        'top_pathway_fdr': top_pathway['FDR q-val'].values[0] if len(top_pathway) > 0 else np.nan
    })

pathway_summary_df = pd.DataFrame(pathway_summary)
pathway_summary_df.to_csv(table_dir / 'pathway_summary_all_cell_types.csv', index=False)

print("\n✓ Saved: pathway_summary_all_cell_types.csv")
print(pathway_summary_df)
```

---

## Step 1.8: Validate All Outputs Exist

```python
print("\n[Stage 1 Validation]")

# Check DE results
de_files = list(table_dir.glob('de_*.csv'))
print(f"  DE result files: {len(de_files)}")

# Check pathway results
pathway_files = list(table_dir.glob('pathway_*.csv')) + list(table_dir.glob('gsea_*.csv'))
print(f"  Pathway result files: {len(pathway_files)}")

# Check figures
de_figures = list(figure_dir.glob('de_*.png'))
pathway_figures = list(figure_dir.glob('pathway_*.png'))
print(f"  DE figures: {len(de_figures)}")
print(f"  Pathway figures: {len(pathway_figures)}")

# Validation
if len(de_files) == 0:
    raise ValueError("❌ Stage 1 incomplete: No DE results")
if len(pathway_files) == 0:
    raise ValueError("❌ Stage 1 incomplete: No pathway enrichment results")

print("\n✅ Stage 1 complete - all required analyses finished")
print(f"   Ready to proceed to Stage 2 (Review & Synthesis)")
```

---

## Stage 1 Summary

By the end of Stage 1, you should have:

**DE Analysis:**
- DE results CSV for each cell type analyzed
- Volcano plots for each cell type
- Heatmaps of top DE genes
- DE summary table across all cell types

**Pathway Enrichment:**
- GSEA results for multiple databases per cell type
- Decoupler pathway analysis results
- Pathway dotplots for each database and cell type
- Pathway summary bar charts
- Pathway summary table across all cell types

**All outputs in:** `workspace/{date}/{run_id}/single_cell_analyst/results/`

---

## Cannot Proceed to Stage 2 Until:

- ✅ All checklist items checked off
- ✅ DE results exist (de_*.csv files) - **MANDATORY Checkpoint 1**
- ✅ Pathway enrichment results exist - **MANDATORY Checkpoint 2**
- ✅ All visualizations generated
- ✅ Summary tables created

---

## Next Steps

After completing Stage 1:
1. Review all generated figures
2. Check summary tables for overview of results
3. Verify all cell types were analyzed
4. **Ready to proceed to Stage 2: Review & Synthesis**

---

**Once validation passes, proceed to Stage 2 for critical review and report generation.**
