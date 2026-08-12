# Within-Donor Meta-Analysis for Cell Type Comparisons

## When to Use

**Use this for comparing cell types/clusters within disease samples:**
- T cells vs B cells in disease tissue
- Malignant vs normal cells within tumors
- Any cell population comparison within disease donors

**Also use as fallback:** When no dataset contains both disease AND healthy samples (cross-study comparison is invalid).

**Don't use this for:**
- Disease vs healthy comparisons when matched datasets exist (use pseudobulk_de_procedure.md instead)

## Method

**Control for donor batch effects by running DE within each donor, then meta-analyzing across donors.**

### Key Requirements

- ≥10 cells per group per donor (minimum)
- ≥3 donors with both cell types (for meta-analysis)
- Use scanpy rank_genes_groups with t-test method
- Use DerSimonian-Laird random-effects meta-analysis

### Workflow

**Step 1: Define comparison groups**
```python
# Example: Malignant vs epithelial cells
EPITHELIAL_TYPES = ['epithelial cell of lung', 'alveolar type 2 cell', ...]
MIN_CELLS = 10
MIN_DONORS = 3
```

**Step 2: Run within-donor DE**
```python
donor_results = {}
for donor in donors:
    # Filter to donor + both cell types
    # Require MIN_CELLS in each group
    # Run: sc.tl.rank_genes_groups(method='t-test')
    # Store: donor_results[donor] = sc.get.rank_genes_groups_df()
```

**Step 3: Meta-analyze across donors**
```python
def random_effects_meta_analysis(lfc_list, se_list):
    """DerSimonian-Laird random-effects model"""
    # 1. Calculate weights (inverse variance)
    # 2. Estimate tau² (between-donor variance)
    # 3. Calculate I² (heterogeneity metric)
    # 4. Compute combined effect and p-value
    return meta_lfc, meta_se, meta_pval, tau2, I2

# For each gene:
#   - Collect LFC and SE from each donor (SE = |LFC/t-stat|)
#   - Run random_effects_meta_analysis()
#   - Apply FDR correction
```

**Step 4: Interpret results**
```python
# meta_padj < 0.05: Significant across donors
# I² < 25%: Low heterogeneity (donors agree)
# I² > 75%: High heterogeneity (donor-specific effects)
```

## Complete Implementation

**See:** `META_ANALYSIS_FUNCTION.md` in reanalysis documentation for the optimized `run_gene_level_meta_analysis()` function.

**Key optimization:** Index donor results by gene name (O(1) lookup) instead of filtering (O(n)). This gives 30x speedup.

## Checklist

- [ ] Defined biologically meaningful comparison groups
- [ ] Ran within-donor DE (≥10 cells per group required)
- [ ] Meta-analyzed with random effects (≥3 donors required)
- [ ] Used indexed lookups (not filtering loops)
- [ ] Checked heterogeneity (I²) for key genes
- [ ] Prepared ranked list for pathway enrichment
