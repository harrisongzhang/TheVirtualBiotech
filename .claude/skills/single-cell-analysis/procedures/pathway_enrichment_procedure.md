
# Pathway Enrichment Analysis with gseapy

## Overview

Pathway enrichment identifies biological processes and signaling pathways altered in disease. Uses Gene Set Enrichment Analysis (GSEA) to find pathways significantly associated with differential expression patterns.

**Tool:** gseapy GSEA across multiple databases (Hallmark, KEGG, Reactome, GO_Biological_Process)

**Input:** Ranked gene list from differential expression results (PyDESeq2 or Wilcoxon)

**Output:** Pathways with normalized enrichment scores (NES), p-values, and FDR

---

## Prerequisites

**Before running pathway enrichment:**

1. ✅ **DE results exist** - Must have completed either:
   - `pseudobulk_de_procedure.md` (PyDESeq2 results with log2FoldChange, padj)
   - `within_donor_meta_analysis.md` (Wilcoxon meta-analysis results)

2. ✅ **Gene symbols in results** - Gene names as symbols (e.g., 'TP53', 'BRCA1'), not IDs

3. ✅ **Multiple cell types analyzed** - Run pathway enrichment for EACH cell type with DE results

---

## Workflow

### Step 1: Prepare Ranked Gene List

**⚠️ CRITICAL: Ranking metric depends on DE method used.**

#### From PyDESeq2 Results

```python
import pandas as pd
import numpy as np

# Load PyDESeq2 results for a cell type
de_results = pd.read_csv(f'{table_dir}/de_genes_{celltype}.csv', index_col=0)

# Rank by: -log10(padj) * sign(log2FC)
# This captures both significance and direction
gene_ranks = pd.Series(
    -np.log10(de_results['padj'] + 1e-300) * np.sign(de_results['log2FoldChange']),
    index=de_results.index
)

# Remove NaN and Inf
gene_ranks = gene_ranks.replace([np.inf, -np.inf], np.nan).dropna()

# Sort descending (upregulated genes at top)
gene_ranks = gene_ranks.sort_values(ascending=False)

print(f'✓ Ranked gene list prepared: {len(gene_ranks)} genes')
```

#### From Wilcoxon Meta-Analysis Results

```python
# Load meta-analysis results
de_results = pd.read_csv(f'{table_dir}/meta_analysis_{celltype}.csv', index_col=0)

# Rank by: -log10(meta_pval) * sign(meta_lfc)
gene_ranks = pd.Series(
    -np.log10(de_results['meta_pval'] + 1e-300) * np.sign(de_results['meta_lfc']),
    index=de_results.index
)

# Remove NaN and Inf
gene_ranks = gene_ranks.replace([np.inf, -np.inf], np.nan).dropna()
gene_ranks = gene_ranks.sort_values(ascending=False)

print(f'✓ Ranked gene list prepared: {len(gene_ranks)} genes')
```

**Save ranked list:**
```python
gene_ranks.to_csv(f'{table_dir}/gene_ranks_{celltype}.csv', header=['rank_metric'])
```

---

### Step 2: Run gseapy GSEA

**⚠️ CRITICAL BUG TO AVOID:**
```python
import gseapy as gp  # Module imported as 'gp'

# ❌ NEVER use 'gp' as loop variable - overwrites module!
for gp in pathway_list:  # BUG! gp is now a string, not the module
    gp.prerank(...)      # ERROR: 'str' has no attribute 'prerank'

# ✅ ALWAYS use descriptive variable names
for pathway_term in pathway_list:
    ...
```

**Run GSEA across multiple databases:**

```python
import gseapy as gp

# Recommended databases (run all for comprehensive coverage)
databases = [
    'MSigDB_Hallmark_2020',           # Curated hallmark pathways (50 pathways)
    'KEGG_2021_Human',                # KEGG pathways
    'Reactome_2022',                  # Reactome pathways
    'GO_Biological_Process_2023'      # GO biological processes
]

gsea_results = {}

for db in databases:
    print(f'\nRunning GSEA: {db}...')

    gsea_res = gp.prerank(
        rnk=gene_ranks,           # Ranked gene list (Series with gene symbols as index)
        gene_sets=db,             # Database name
        outdir=None,              # Don't save to disk (handle manually)
        permutation_num=1000,     # Number of permutations
        min_size=15,              # Minimum genes per pathway
        max_size=500,             # Maximum genes per pathway
        seed=42                   # Reproducibility
    )

    gsea_results[db] = gsea_res.res2d
    print(f'  ✓ {len(gsea_res.res2d)} pathways tested')

print(f'\n✓ GSEA complete across {len(databases)} databases')
```

---

### Step 3: Filter and Interpret Results

**Filter for significance:**

```python
# Combine results from all databases
all_pathways = []

for db, results in gsea_results.items():
    # Filter for significant pathways
    sig_pathways = results[
        (results['FDR q-val'] < 0.05) &           # FDR < 0.05
        (results['NES'].abs() > 1.5)              # |NES| > 1.5
    ].copy()

    sig_pathways['database'] = db
    all_pathways.append(sig_pathways)

# Concatenate all significant pathways
all_sig = pd.concat(all_pathways, ignore_index=True)

# Sort by absolute NES
all_sig = all_sig.sort_values('NES', key=abs, ascending=False)

print(f'\nSignificant pathways across all databases: {len(all_sig)}')
print(f'  Upregulated (NES>0): {(all_sig["NES"] > 0).sum()}')
print(f'  Downregulated (NES<0): {(all_sig["NES"] < 0).sum()}')

# Save results
all_sig.to_csv(f'{table_dir}/gsea_pathways_{celltype}.csv')

# Show top results
print(f'\nTop 10 pathways by |NES|:')
print(all_sig[['Term', 'NES', 'FDR q-val', 'database']].head(10))
```

---

## Complete Example Script

```python
#!/usr/bin/env python3
"""Run pathway enrichment on all DE results"""

import pandas as pd
import numpy as np
import gseapy as gp
from pathlib import Path

# Setup paths
table_dir = Path('results/tables')
de_files = list(table_dir.glob('de_genes_*.csv'))

print(f'Found {len(de_files)} DE result files')

# Databases to query
databases = [
    'MSigDB_Hallmark_2020',
    'KEGG_2021_Human',
    'Reactome_2022',
    'GO_Biological_Process_2023'
]

# Process each cell type
for de_file in de_files:
    celltype = de_file.stem.replace('de_genes_', '')
    print(f'\n{"="*80}')
    print(f'Pathway enrichment: {celltype}')
    print(f'{"="*80}')

    # Load DE results
    de_results = pd.read_csv(de_file, index_col=0)

    # Create ranked gene list
    gene_ranks = pd.Series(
        -np.log10(de_results['padj'] + 1e-300) * np.sign(de_results['log2FoldChange']),
        index=de_results.index
    )
    gene_ranks = gene_ranks.replace([np.inf, -np.inf], np.nan).dropna()
    gene_ranks = gene_ranks.sort_values(ascending=False)

    print(f'  Ranked genes: {len(gene_ranks)}')

    # Save ranked list
    gene_ranks.to_csv(f'{table_dir}/gene_ranks_{celltype}.csv', header=['rank_metric'])

    # Run GSEA across databases
    gsea_results = {}
    for db in databases:
        print(f'  Running GSEA: {db}...')
        gsea_res = gp.prerank(
            rnk=gene_ranks,
            gene_sets=db,
            outdir=None,
            permutation_num=1000,
            min_size=15,
            max_size=500,
            seed=42
        )
        gsea_results[db] = gsea_res.res2d

    # Combine and filter
    all_pathways = []
    for db, results in gsea_results.items():
        sig = results[
            (results['FDR q-val'] < 0.05) &
            (results['NES'].abs() > 1.5)
        ].copy()
        sig['database'] = db
        all_pathways.append(sig)

    all_sig = pd.concat(all_pathways, ignore_index=True)
    all_sig = all_sig.sort_values('NES', key=abs, ascending=False)

    # Save results
    all_sig.to_csv(f'{table_dir}/gsea_pathways_{celltype}.csv')

    print(f'  ✓ Significant pathways: {len(all_sig)}')
    print(f'    Top pathway: {all_sig.iloc[0]["Term"]} (NES={all_sig.iloc[0]["NES"]:.2f})')

print('\n✓ Pathway enrichment complete for all cell types')
```

---

## Recommended Databases

| Database | Description | Size | Use For |
|----------|-------------|------|---------|
| **MSigDB_Hallmark_2020** | Curated hallmark pathways | 50 pathways | Cancer, disease processes |
| **KEGG_2021_Human** | KEGG pathways | ~300 pathways | Metabolic, signaling |
| **Reactome_2022** | Reactome pathways | ~2000 pathways | Detailed mechanisms |
| **GO_Biological_Process_2023** | Gene Ontology BP | ~7000 terms | Broad biological processes |

**Recommendation:** Always run Hallmark + KEGG + Reactome. Add GO_BP for exploratory analysis.

---

## Handling Different DE Result Formats

### PyDESeq2 Results (from pseudobulk_de_procedure.md)

**Expected columns:** `gene`, `baseMean`, `log2FoldChange`, `lfcSE`, `stat`, `pvalue`, `padj`

**Ranking formula:**
```python
-np.log10(padj + 1e-300) * np.sign(log2FoldChange)
```

### Wilcoxon Meta-Analysis Results (from within_donor_meta_analysis.md)

**Expected columns:** `gene`, `meta_lfc`, `meta_se`, `meta_pval`, `meta_fdr`, `tau2`, `I2`

**Ranking formula:**
```python
-np.log10(meta_pval + 1e-300) * np.sign(meta_lfc)
```

**Both produce:** High positive values = upregulated in disease, high negative values = downregulated in disease

---

## Visualization (Optional)

**Dotplot of top pathways:**

```python
import matplotlib.pyplot as plt

# Load results
pathways = pd.read_csv(f'{table_dir}/gsea_pathways_{celltype}.csv', index_col=0)

# Top 20 pathways by |NES|
top20 = pathways.nlargest(20, 'NES', key=abs)

# Create dotplot
fig, ax = plt.subplots(figsize=(10, 8))

scatter = ax.scatter(
    top20['NES'],
    range(len(top20)),
    c=top20['FDR q-val'],
    s=100,
    cmap='YlOrRd_r',
    vmin=0, vmax=0.05
)

ax.set_yticks(range(len(top20)))
ax.set_yticklabels(top20['Term'], fontsize=9)
ax.set_xlabel('Normalized Enrichment Score (NES)', fontsize=11)
ax.axvline(0, color='gray', linestyle='--', linewidth=0.8)
ax.grid(axis='x', alpha=0.3)

cbar = plt.colorbar(scatter, ax=ax)
cbar.set_label('FDR q-value', fontsize=10)

plt.title(f'Top 20 Pathways: {celltype}', fontsize=12, fontweight='bold')
plt.tight_layout()
plt.savefig(f'{figure_dir}/gsea_dotplot_{celltype}.png', dpi=300, bbox_inches='tight')
plt.close()

print(f'✓ Saved dotplot: gsea_dotplot_{celltype}.png')
```

---

## Checklist

- [ ] Verified DE results exist (PyDESeq2 or Wilcoxon meta-analysis)
- [ ] Gene symbols present in DE results (not integer IDs)
- [ ] Created ranked gene list using appropriate formula
- [ ] Ran gseapy GSEA across multiple databases (Hallmark, KEGG, Reactome minimum)
- [ ] Filtered for significance (FDR<0.05, |NES|>1.5)
- [ ] Saved pathway results: `gsea_pathways_{celltype}.csv`
- [ ] Saved ranked gene list: `gene_ranks_{celltype}.csv`
- [ ] Generated visualization (dotplot or barplot)
- [ ] Interpreted top pathways in biological context
- [ ] Repeated for ALL cell types with DE results

---

## Important Notes

### Variable Naming Bug

**⚠️ CRITICAL:** Never use `gp` as a loop variable when gseapy is imported as `gp`:

```python
import gseapy as gp

# ❌ WRONG - overwrites module
for gp in pathways:
    gp.prerank(...)  # ERROR

# ✅ CORRECT
for pathway_term in pathways:
    gp.prerank(...)
```

### NES Interpretation

- **NES > 0**: Pathway upregulated in disease (enriched in genes with positive log2FC)
- **NES < 0**: Pathway downregulated in disease (enriched in genes with negative log2FC)
- **|NES| > 1.5**: Strong enrichment (recommended threshold)
- **FDR < 0.05**: Statistical significance after multiple testing correction

### Database Selection

Start with **Hallmark + KEGG + Reactome** for most analyses. These provide:
- Hallmark: Well-defined biological states (immune response, proliferation, apoptosis)
- KEGG: Metabolic and signaling pathways
- Reactome: Detailed molecular mechanisms

Add GO_Biological_Process for broader exploratory analysis (but expect more pathways).

### Gene List Size

- **Minimum:** 500 genes (GSEA needs sufficient dynamic range)
- **Optimal:** 5,000-20,000 genes (use all tested genes from DE)
- **Don't pre-filter:** Include all genes with ranks, not just significant ones

GSEA uses the full ranked list to assess if pathway genes are enriched at the top/bottom.

---
