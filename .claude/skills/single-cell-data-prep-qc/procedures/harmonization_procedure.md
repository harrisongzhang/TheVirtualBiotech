
# Cell Type Harmonization for Single-Cell Analysis

## Contents
- [When to Use This Skill](#when-to-use-this-skill)
- [Core Principle: MINIMAL & CONSERVATIVE](#core-principle-minimal--conservative)
- [Required Workflow: Manual Adjudication](#required-workflow-manual-adjudication)
- [What TO Harmonize (General Principles)](#what-to-harmonize-general-principles)
  - [1. Remove Redundant Tissue/Organ Labels](#1-remove-redundant-tissueorgan-labels)
  - [2. Normalize Equivalent Terminology](#2-normalize-equivalent-terminology)
  - [3. Standardize Case and Formatting (Optional)](#3-standardize-case-and-formatting-optional)
- [What NOT TO Harmonize (Keep Separate)](#what-not-to-harmonize-keep-separate)
- [Handling Annotation Granularity Differences](#handling-annotation-granularity-differences)
- [Systematic Harmonization Algorithm](#systematic-harmonization-algorithm)
  - [Step 1: List All Cell Types from Both Datasets](#step-1-list-all-cell-types-from-both-datasets)
  - [Step 2: Manually Adjudicate Each Unmatched Pair](#step-2-manually-adjudicate-each-unmatched-pair)
  - [Step 3: Apply Harmonization to Both Datasets](#step-3-apply-harmonization-to-both-datasets)
  - [Step 4: Validate Harmonization Results](#step-4-validate-harmonization-results)
  - [Step 5: Document Harmonization in Analysis Report](#step-5-document-harmonization-in-analysis-report)
  - [Step 6: Validate Harmonization (MANDATORY)](#step-6-validate-harmonization-mandatory)
- [Custom Harmonization Guidelines](#custom-harmonization-guidelines)
- [Complete Example: Breast Cancer Analysis (Reference Only)](#complete-example-breast-cancer-analysis-reference-only)
- [Checklist Before Proceeding](#checklist-before-proceeding)

## When to Use This Skill

When comparing datasets from the **same tissue** (e.g., healthy breast vs. breast cancer), cell type names may differ due to annotation conventions, not biology. Apply **minimal harmonization** to remove artifacts while preserving biological distinctions.

## Core Principle: MINIMAL & CONSERVATIVE

**Only harmonize when labels represent THE SAME cell type with different names. NEVER merge biologically distinct cell types.**

## Required Workflow: Manual Adjudication

**⚠️ CRITICAL: You MUST manually review ALL unmatched cell type pairs.**

**Process:**
1. List disease-only and healthy-only cell types
2. For EACH unmatched type, check if it could match another type
3. Create a markdown table documenting EVERY decision (harmonize YES/NO with rationale)
4. Only after completing the table, create the harmonization_map code
5. Document all decisions in analysis report

**Do NOT:**
- Skip pairs without reviewing them
- Use automated pattern matching without manual validation
- Assume only 2-3 harmonizations are needed
- Proceed without documenting rationale for each decision

## What TO Harmonize (General Principles)

### 1. Remove Redundant Tissue/Organ Labels

**Principle:** When ALL cells are from the same tissue (based on query filter), tissue labels are redundant.

```python
# Pattern: "X of [tissue]" → "X"
# Examples for various tissues:

# Breast tissue:
'fibroblast of breast' → 'fibroblast'
'adipocyte of breast' → 'adipocyte'
'fibroblast of mammary gland' → 'fibroblast'

# Lung tissue:
'fibroblast of lung' → 'fibroblast'
'epithelial cell of lung' → 'epithelial_cell'

# Brain tissue:
'astrocyte of brain' → 'astrocyte'
'neuron of cerebral cortex' → 'neuron'  # If all from cortex
```

**Rationale**: If query filtered for `tissue == '[specific tissue]'`, ALL cells are from that tissue. Adding tissue name is redundant information.

### 2. Normalize Equivalent Terminology

**Principle:** Different names for the SAME biological cell type should be merged.

```python
# Pattern: Synonyms or shortened versions
# Examples:

# Epithelial cell variants:
'basal cell' ↔ 'basal epithelial cell' ↔ 'basal-myoepithelial cell'
'luminal epithelial cell of mammary gland' ↔ 'luminal epithelial cell'

# Generic vs specific organ labels:
'adipocyte' ↔ 'adipocyte of [tissue]'  # If tissue is redundant
'endothelial cell' ↔ 'endothelial cell of vascular tree'  # Both generic vascular
```

**Warning:** Only merge if >95% confident they're the same cell type. When uncertain, keep separate.

### 3. Standardize Case and Formatting (Optional)

**Principle:** Consistent naming convention improves readability.

```python
# Optional: Use lowercase with underscores for consistency
'T cell' → 't_cell'
'B cell' → 'b_cell'
'NK cell' → 'nk_cell'
```

**Note:** This is cosmetic and optional. Focus on biological equivalence first.

## What NOT TO Harmonize (Keep Separate)

### ❌ NEVER Merge Immune Subtypes

```python
# BIOLOGICALLY DISTINCT - DO NOT merge:
'CD4-positive, alpha-beta T cell'  # Helper T cells
'CD8-positive, alpha-beta T cell'  # Cytotoxic T cells
'T cell' (generic)                 # Untyped T cells

'macrophage'     # Tissue resident
'monocyte'       # Circulating precursor
'myeloid cell'   # Broader category

'B cell'         # Naive B cells
'plasma cell'    # Activated, antibody-secreting
'plasmablast'    # Pre-plasma intermediate
'memory B cell'  # Memory B cells
```

**Why**: CD4+ and CD8+ T cells have **opposite functions**. Macrophages and monocytes are at **different maturation stages**. Merging loses critical biology.

### ❌ NEVER Merge Endothelial Subtypes

```python
# ANATOMICALLY & FUNCTIONALLY DISTINCT - DO NOT merge:
'vein endothelial cell'
'capillary endothelial cell'
'endothelial cell of artery'
'endothelial cell of lymphatic vessel'
```

**Why**: Different vascular locations have **distinct gene expression**, respond differently to **angiogenic signals**, and are important for vascular-specific target identification.

## Handling Annotation Granularity Differences

**Common scenario**: One dataset has specific subtypes, the other has broad categories.

**Example**:
- Cancer dataset: "CD4+ T cell" (11,238), "CD8+ T cell" (6,610), "T cell" (14,069)
- Healthy dataset: Only "T cell" (3,348) - not subtyped

**Correct approach**:
```python
# Keep all cell types SEPARATE in the data
# DO NOT merge CD4+/CD8+ into generic "T cell"

# During differential expression, you CAN compare:
# - Cancer CD4+ T cells vs. Healthy T cells (mixed population)
# - Cancer CD8+ T cells vs. Healthy T cells (mixed population)
# - Cancer T cells (generic) vs. Healthy T cells (generic)
```

**Document this in your report**:
```markdown
## Differential Expression: CD4+ T cells

**Comparison**: Cancer CD4+ T cells (11,238) vs. Healthy T cells (3,348)

**Note**: Healthy dataset lacks CD4+/CD8+ subtype annotations. The healthy "T cell"
population represents a mixed T cell compartment likely containing both CD4+ and CD8+
cells. This comparison identifies CD4+ T cell-specific signatures in cancer relative
to the baseline healthy T cell transcriptome.
```

## Systematic Harmonization Algorithm

### Step 1: List All Cell Types from Both Datasets

**Before concatenation, analyze cell type overlap systematically:**

```python
import pandas as pd

# Get cell types from each dataset
disease_types = set(adata_disease.obs['cell_type'].unique())
healthy_types = set(adata_healthy.obs['cell_type'].unique())

print(f'Disease cell types: {len(disease_types)}')
for ct in sorted(disease_types):
    count = (adata_disease.obs['cell_type'] == ct).sum()
    print(f'  {ct}: {count}')

print(f'\nHealthy cell types: {len(healthy_types)}')
for ct in sorted(healthy_types):
    count = (adata_healthy.obs['cell_type'] == ct).sum()
    print(f'  {ct}: {count}')

# Exact matches
overlap = disease_types.intersection(healthy_types)
print(f'\nExact matches: {len(overlap)}')
for ct in sorted(overlap):
    print(f'  ✓ {ct}')

# Unmatched types
disease_only = disease_types - healthy_types
healthy_only = healthy_types - disease_types

print(f'\nDisease-only ({len(disease_only)}):')
for ct in sorted(disease_only):
    print(f'  {ct}')

print(f'\nHealthy-only ({len(healthy_only)}):')
for ct in sorted(healthy_only):
    print(f'  {ct}')
```

### Step 2: Manually Adjudicate Each Unmatched Pair

**⚠️ CRITICAL: You must MANUALLY review each unmatched type and decide if harmonization is appropriate.**

**For YOUR dataset, go through the disease-only and healthy-only lists from Step 1:**

**Create a harmonization decision table for YOUR data:**

```markdown
## Harmonization Decisions

| Disease Type | Count | Healthy Type | Count | Harmonize? | Rationale | Unified Name |
|-------------|-------|-------------|-------|------------|-----------|--------------|
| [type_A] | [N] | [type_X] | [M] | YES/NO | [Your reasoning] | [unified_name] |
| [type_B] | [N] | [type_Y] | [M] | YES/NO | [Your reasoning] | [unified_name] |
| ... | ... | ... | ... | ... | ... | ... |
```

**For EACH unmatched pair, ask:**
1. Do these represent the same cell type with different names?
2. Is one just adding a redundant tissue/organ label? (e.g., "X of breast" vs "X")
3. Are they biologically distinct subtypes? (If yes → NO harmonization)
4. Is one generic and the other specific? (Usually → NO harmonization)

**After completing YOUR table, translate YOUR decisions into code:**

```python
# Initialize harmonization map based on YOUR manual adjudication
harmonization_map = {}

# Add each harmonization decision with rationale comment
# Example format:
# harmonization_map['original_name_A'] = 'unified_name'
# harmonization_map['original_name_B'] = 'unified_name'  # Same unified name = merge
# Rationale: [explain why these are the same cell type]

# For YOUR tissue/disease:
# - Check for redundant tissue labels (e.g., "X of [tissue]" → "X")
# - Check for equivalent terminology (e.g., "basal cell" ↔ "basal epithelial cell")
# - Check for generic vs generic matches (e.g., "endothelial cell" ↔ "endothelial cell of vascular tree")
# - DO NOT merge specific subtypes (CD4+/CD8+, artery/vein, etc.)

print('\nHarmonization Map (based on YOUR manual adjudication):')
for orig, unified in harmonization_map.items():
    print(f'  "{orig}" → "{unified}"')

print(f'\nTotal mappings: {len(harmonization_map)}')
```

**Important:** Document YOUR reasoning for each decision specific to YOUR analysis. This creates an audit trail for reviewers.

### Step 3: Apply Harmonization to Both Datasets

```python
# Apply to disease dataset
adata_disease.obs['unified_cell_type'] = adata_disease.obs['cell_type'].map(
    harmonization_map
).fillna(adata_disease.obs['cell_type'])

# Apply to healthy dataset
adata_healthy.obs['unified_cell_type'] = adata_healthy.obs['cell_type'].map(
    harmonization_map
).fillna(adata_healthy.obs['cell_type'])

# Verify no cells lost
assert adata_disease.obs['unified_cell_type'].notna().all()
assert adata_healthy.obs['unified_cell_type'].notna().all()

print('✓ Harmonization applied to both datasets')
```

### Step 4: Validate Harmonization Results

**Check the improvement in overlap:**

```python
# Re-analyze overlap after harmonization
disease_unified = set(adata_disease.obs['unified_cell_type'].unique())
healthy_unified = set(adata_healthy.obs['unified_cell_type'].unique())
overlap_after = disease_unified.intersection(healthy_unified)

print('\n' + '='*80)
print('HARMONIZATION IMPACT')
print('='*80)
print(f'BEFORE harmonization:')
print(f'  Disease types: {len(disease_types)}')
print(f'  Healthy types: {len(healthy_types)}')
print(f'  Exact matches: {len(overlap)} ({100*len(overlap)/max(len(disease_types), len(healthy_types)):.0f}%)')

print(f'\nAFTER harmonization:')
print(f'  Disease types: {len(disease_unified)}')
print(f'  Healthy types: {len(healthy_unified)}')
print(f'  Matches: {len(overlap_after)} ({100*len(overlap_after)/max(len(disease_unified), len(healthy_unified)):.0f}%)')

print(f'\nImprovement: +{len(overlap_after) - len(overlap)} matched cell types')

print('\nMatched cell types for DE analysis:')
for ct in sorted(overlap_after):
    d_count = (adata_disease.obs['unified_cell_type'] == ct).sum()
    h_count = (adata_healthy.obs['unified_cell_type'] == ct).sum()
    print(f'  {ct}: D={d_count}, H={h_count}')
```

### Step 5: Document Harmonization in Analysis Report

**Save your harmonization table to the analysis report:**

```python
# Save harmonization documentation
harmonization_doc = f"""
## Cell Type Harmonization Decisions

Total harmonizations applied: {len(harmonization_map)}

### Harmonization Map:
"""

for orig, unified in harmonization_map.items():
    harmonization_doc += f'\n- `{orig}` → `{unified}`'

harmonization_doc += """

### Rationale:
- Removed redundant tissue labels (all cells from breast/mammary gland tissue)
- Normalized basal epithelial cell terminology
- Merged generic endothelial cells (kept specific subtypes separate)
- Preserved all immune cell subtypes (CD4+/CD8+, macrophage/monocyte, B cell subtypes)

### Impact:
- Before: {before} exact matches
- After: {after} matches
- Improvement: +{gain} cell types available for DE analysis
"""

# Save to file
with open(report_dir / 'harmonization_decisions.md', 'w') as f:
    f.write(harmonization_doc.format(
        before=len(overlap),
        after=len(overlap_after),
        gain=len(overlap_after) - len(overlap)
    ))

print('✓ Harmonization decisions documented')
```

### Step 6: Validate Harmonization (MANDATORY)

```python
# 1. Count check - total cells unchanged
assert adata.n_obs == original_n_obs

# 2. No unmapped cells
assert adata.obs['cell_type_harmonized'].notna().all()

# 3. Review mapping results
print('\nHarmonization Summary:')
print(f'Original types: {adata.obs["cell_type"].nunique()}')
print(f'Harmonized types: {adata.obs["cell_type_harmonized"].nunique()}')
print(f'Types merged: {adata.obs["cell_type"].nunique() - adata.obs["cell_type_harmonized"].nunique()}')

# 4. Show which types were merged
merged = adata.obs.groupby('cell_type_harmonized')['cell_type'].unique()
for harmonized, originals in merged.items():
    if len(originals) > 1:
        print(f'{harmonized}: {list(originals)}')
```

## Custom Harmonization Guidelines

When encountering obvious naming differences:

1. **Document the mapping** in your analysis script with clear rationale
2. **Apply conservatively** - only map when >95% confident they're the same cell type
3. **Validate with marker genes** - check that mapped types express same markers
4. **Note in report** - explain any custom mappings applied

## Complete Example: Breast Cancer Analysis (Reference Only)

**⚠️ This is a SPECIFIC example for breast tissue - adapt the approach to YOUR tissue/disease.**

**Example Dataset:**
- Disease: 18 cell types, 4,914 cells (invasive ductal breast carcinoma)
- Healthy: 10 cell types, 4,999 cells (normal breast tissue)

**Step 1 Results (example):**
- Exact matches: 5 (28%)
- Disease-only: 13 types
- Healthy-only: 5 types

**Step 2 Manual Adjudication Table (example showing the process):**

| Disease Type | Count | Healthy Type | Count | Decision | Rationale | Unified Name |
|-------------|-------|-------------|-------|----------|-----------|--------------|
| fibroblast of breast | 304 | fibroblast | 743 | **YES** | Redundant tissue label | fibroblast |
| adipocyte of breast | - | adipocyte of breast | 407 | **YES** | Redundant tissue label | adipocyte |
| basal-myoepithelial cell | 12 | basal cell | 841 | **YES** | Both are basal epithelial | basal_epithelial |
| endothelial cell | 267 | endothelial cell of vascular tree | 372 | **YES** | Both generic vascular | endothelial_cell_generic |
| CD4+ T cell | 1136 | T cell | 295 | **NO** | Specific subtype | - |
| CD8+ T cell | 696 | T cell | 295 | **NO** | Specific subtype | - |
| macrophage | 207 | myeloid cell | 362 | **NO** | Different maturation | - |
| monocyte | 73 | myeloid cell | 362 | **NO** | Different maturation | - |
| mammary gland epithelial | 702 | luminal epithelial... | 1664 | **NO** | Uncertain, keep separate | - |
| (Other types reviewed...) | ... | ... | ... | **NO** | (Various rationale) | - |

**Step 3 Example Code (for this specific breast cancer dataset):**
```python
# THIS IS SPECIFIC TO BREAST TISSUE - CREATE YOUR OWN MAP
harmonization_map = {
    'fibroblast of breast': 'fibroblast',
    'adipocyte of breast': 'adipocyte',
    'basal cell': 'basal_epithelial',
    'basal-myoepithelial cell of mammary gland': 'basal_epithelial',
    'endothelial cell': 'endothelial_cell_generic',
    'endothelial cell of vascular tree': 'endothelial_cell_generic',
}
```

**Step 4 Example Results:**
- Before: 5 exact matches (28%)
- After: 8 matched cell types (44%)
- Improvement: +3 cell types (+60%)

**Key lesson from this example:**
- Started with only 28% overlap (poor)
- Systematic review identified 3 additional matches
- Result: 44% overlap (acceptable for tissue-matched datasets)
- Proper harmonization increases statistical power and analysis coverage

## Checklist Before Proceeding

- [ ] **Printed disease-only and healthy-only cell type lists** (Step 1)
- [ ] **Manually reviewed EACH unmatched cell type pair** (Step 2)
- [ ] **Created harmonization decision table with rationale** for EVERY potential match
- [ ] **Only harmonized truly equivalent cell types** (synonyms, redundant labels)
- [ ] **Did NOT merge immune subtypes** (CD4+/CD8+, macrophage/monocyte, B/plasma)
- [ ] **Did NOT merge specific endothelial subtypes** (lymphatic/artery/vein kept separate)
- [ ] **Validated overlap improvement** (Step 4 - printed before/after statistics)
- [ ] **Documented all decisions in report** (Step 5 - saved harmonization_decisions.md)
- [ ] **Validated total cell count unchanged** (Step 6)
- [ ] **Reviewed which cell types were merged** (Step 6)

**IMPORTANT:** Do not skip to code without completing the manual adjudication table first.
