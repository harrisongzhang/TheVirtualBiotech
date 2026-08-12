# Computational Resources Available

## Overview

You have access to a high-performance computing cluster with abundant resources. **Never compromise analysis quality due to computational concerns.**

---

## Available Resources

### Memory (RAM)

**Available:** 650 GB RAM

**Typical usage:**
- Raw data loading: 3-5 GB
- QC and filtering: 2-3 GB
- Integration (100K cells): 10-20 GB
- Harmony batch correction: 8-15 GB
- Differential expression: 3-5 GB
- **Peak usage:** ~20-25 GB (well within limits)

**Implication:** Memory is NOT a constraint. Use full datasets within the 100K cell limit.

---

### Timeout Limits

**Available:** Up to 1,800,000 ms (30 minutes) per script execution

**Recommended timeouts:**
- Data loading: 300,000 ms (5 minutes)
- QC and filtering: 300,000 ms (5 minutes)
- Harmony integration (100K cells): 600,000 ms (10 minutes)
- Differential expression: 600,000 ms (10 minutes)
- Pathway enrichment: 900,000 ms (15 minutes)

**Setting timeout in Bash:**
```python
# When executing scripts:
bash(
    command="python analysis/script.py",
    timeout=1800000  # 30 minutes
)
```

**Implication:** Time is NOT a constraint. Analyses should complete in 5-30 minutes.

---

### CPU Cores

**Available:** Multiple cores for parallel processing

**Usage:**
- scanpy operations use multiple cores automatically
- PyDESeq2: Use `n_cpus=8` for ~5-8x speedup (parallelizes dispersion fitting)
- gseapy can parallelize across gene sets

**Implication:** Always enable parallelization for PyDESeq2 analyses.

---

### Disk Space

**Available:** 549 GB in project directory

**Typical usage:**
- Raw data: 1-5 GB per dataset
- Processed data: 0.5-2 GB after subsampling and layer removal
- Results (figures, tables): 100-500 MB
- **Total per analysis:** 5-15 GB (well within limits)

**Implication:** Disk space is NOT a constraint.

---

## 100K Cell Limit - Purpose and Rationale

### Why the Limit Exists

**The 100K cell limit (50K per condition) exists for:**
- **Practical efficiency:** Analyses complete in 10-20 minutes per step
- **Statistical power:** 50K cells per condition provides excellent power for DE and pathway analysis
- **Context efficiency:** Keeps analysis within comfortable token budget
- **Balanced approach:** Maximizes insight while maintaining efficient execution

### What the Limit Does NOT Excuse

**Within the 100K limit, you MUST still:**
- ✅ Perform proper QC filtering
- ✅ Use robust batch correction (Harmony with validation)
- ✅ Apply appropriate statistical tests with FDR correction
- ✅ Use pseudobulk DE (not single-cell Wilcoxon)
- ✅ Perform pathway enrichment
- ✅ Validate cell type annotations
- ✅ Generate publication-quality visualizations (dpi=300)
- ✅ Complete all 3 stages including critical review

**The 100K limit is about compute efficiency, NOT an excuse to cut corners.**

---

## No Valid Excuses for Shortcuts

### Invalid Excuses:

❌ **"Analysis takes too long"**
- Solution: Use appropriate timeouts (up to 30 minutes)
- If truly needed, increase timeout further

❌ **"Too many cells to process"**
- Solution: Subsample to 100K using stratified sampling
- Follow: `procedures/subsampling_procedure.md`

❌ **"Not enough memory"**
- Solution: You have 650 GB - use it
- If hitting limits, subsample or remove unnecessary data layers

❌ **"Pathway enrichment is slow"**
- Solution: Set timeout to 15-30 minutes
- This is standard and expected

❌ **"Review takes extra time"**
- Solution: Stage 3 is 20% of total time - allocate it
- Quality assurance is not optional

---

## Performance Optimization Guidelines

### DO Optimize:

✅ **Remove unnecessary data:**
```python
# Remove layers after ensuring raw counts in .X
adata.layers = {}  # Clear layers
adata.obsm = {}    # Clear embeddings (recompute after integration)
```

✅ **Subsample large datasets:**
```python
# If >100K cells, subsample to 50K per condition
sc.pp.subsample(adata, n_obs=50000, random_state=42)
```

✅ **Use appropriate timeouts:**
```python
# Set realistic timeouts for intensive steps
bash(command="python script.py", timeout=1800000)  # 30 min
```

### DO NOT "Optimize" by Skipping Steps:

❌ **Skipping QC:**
- "We'll trust the data is clean" - NO

❌ **Using Wilcoxon instead of pseudobulk:**
- "PyDESeq2 is slower than Wilcoxon" - NOT a valid reason

❌ **Skipping batch correction:**
- "Harmony takes 10 minutes" - This is normal and required

❌ **Skipping pathway enrichment:**
- "gseapy takes 15 minutes" - This is expected and REQUIRED

❌ **Skipping critical review:**
- "Review adds an extra step" - Quality control is MANDATORY

---

## Actual Timing Expectations

**From empirical testing (100K cells):**

| Step | Expected Time | Timeout |
|------|---------------|---------|
| Data download | 2-5 min | 5 min |
| QC and filtering | 2-3 min | 5 min |
| Subsampling | 1-2 min | 5 min |
| Harmonization | 1-2 min | 5 min |
| Harmony integration | 8-15 min | 15-30 min |
| Pseudobulk DE (per cell type) | 3-5 min | 15 min |
| Pathway enrichment (per cell type) | 10-15 min | 15-30 min |
| Visualization | 2-5 min | 5 min |

**Total analysis time:** 1-3 hours for complete workflow

**Conclusion:** Computational resources are abundant. All steps should complete successfully within allocated timeouts.

---

## When to Report Computational Issues

**Valid scenarios to report:**
- Script fails with out-of-memory error despite 650 GB available
- Script exceeds 30-minute timeout with 100K cells (after subsampling)
- Harmony fails with technical error (not timeout)

**How to report:**
- Document exact error message
- Report memory usage and cell count
- Describe attempted solutions
- Document workaround if alternative method used

---

## Summary

**You have:**
- 650 GB RAM (abundant)
- 30-minute timeouts (sufficient)
- Multiple CPU cores (parallel processing)
- 549 GB disk space (ample)

**Therefore:**
- No excuses for skipping QC or batch correction
- No excuses for using Wilcoxon when pseudobulk is possible
- No excuses for skipping pathway enrichment
- No excuses for skipping critical review
- No excuses for compromising statistical rigor

**Use the resources available. Complete the workflow fully.**
