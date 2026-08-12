
# Critical Review Protocol for Single-Cell Analysis

## When to Use This Skill

Use this skill during **Stage 3B** of the single-cell analysis workflow - after completing all analysis scripts and generating the draft report, but **before** creating the final report.

## Persona Shift: Dr. Reviewer

When using this skill, adopt the persona of **Dr. Reviewer** - a senior scientist evaluating this work objectively. Your goal is to identify flaws, overclaimed results, or missing controls before they make it into the final report.

## Five-Category Review Framework

### Category 1: Data Quality Assessment

**Check for**:
- [ ] Low-quality cells filtered appropriately (min_genes, max_mt%)
- [ ] Doublet detection performed and doublets removed
- [ ] Batch effects addressed (if multiple datasets)
- [ ] Sequencing depth adequate (median UMI counts reported)
- [ ] QC metrics documented and visualized

**Red flags**:
- Too permissive filtering (min_genes < 200, max_mt > 20%)
- No doublet detection
- Batch effects visible on UMAP but not corrected
- Very low UMI counts (<500 median)

**Questions to ask**:
- Are QC thresholds justified?
- Were doublets actually removed from the data?
- Do batch effects still confound condition comparisons?

### Category 2: Clustering & Annotation Validation

**Check for**:
- [ ] Clustering resolution appropriate (not too coarse/fine)
- [ ] Cell type annotations supported by canonical markers
- [ ] Novel populations properly justified with marker validation
- [ ] Marker gene validation performed (dotplot or violin plots)
- [ ] No "unknown" or "unclassified" clusters without investigation

**Red flags**:
- Resolution too coarse (6 clusters for 100K cells) or too fine (500 clusters)
- Annotations not validated - just using existing labels
- Claims of novel cell types without marker validation
- Large "unclassified" populations ignored

**Questions to ask**:
- Did the analyst validate cell types with known markers?
- Are clustering resolution choices justified?
- For novel populations: what markers define them?

### Category 3: Statistical Rigor

**Check for**:
- [ ] Appropriate statistical tests used (pseudobulk for DE)
- [ ] Multiple testing correction applied (FDR, not raw p-values)
- [ ] Effect sizes reported (log2FC, not just p-values)
- [ ] Sample size adequate for claims (n donors, not n cells)
- [ ] Confidence intervals or standard errors provided where appropriate

**Red flags**:
- No FDR correction - using raw p-values
- Only p-values, no effect sizes
- Cell-level testing without pseudobulk (pseudoreplication)
- Underpowered comparisons (n=2 donors per condition)

**Questions to ask**:
- Are p-values adjusted for multiple testing?
- Is the sample size (number of donors) adequate?
- Are effect sizes meaningful (log2FC >1 for DE)?

### Category 4: Biological Interpretation

**Check for**:
- [ ] Claims directly supported by data shown
- [ ] Alternative explanations considered
- [ ] Consistent with known biology
- [ ] Technical artifacts distinguished from biology
- [ ] Appropriate level of certainty (associated vs. causes)

**Red flags**:
- Causal claims from correlative data ("X causes Y")
- Artifacts presented as biology (ambient RNA, doublets)
- Contradicts established biological facts
- Overinterpretation of weak signals

**Questions to ask**:
- Does the data actually support this claim?
- Could this be a technical artifact?
- Are there alternative explanations?
- Is the language appropriately cautious?

### Category 5: Reproducibility

**Check for**:
- [ ] Analysis reproducible from scripts provided
- [ ] Random seeds set for stochastic operations
- [ ] Software versions documented
- [ ] Intermediate files saved
- [ ] All code steps present (no missing steps)

**Red flags**:
- Missing code steps - can't reproduce figures
- No random seeds (subsample, Leiden, UMAP)
- Software versions not documented
- Results files present but code to generate them missing

**Questions to ask**:
- Can I reproduce the key findings from the code?
- Are stochastic operations reproducible (random_state set)?
- Is the analysis environment documented?

## Review Workflow

### Step 1: Read Draft Report

Review the `results/reports/analysis_report_DRAFT.md` file.

### Step 2: Review Analysis Scripts

Read the analysis scripts in `code/scripts/` directory:
- Data loading and QC
- Integration and batch correction
- Statistical analysis
- Visualization

### Step 3: Evaluate Each Category

For each of the 5 categories:
1. Check items in the checklist
2. Note any red flags encountered
3. Ask the critical questions listed
4. Classify issues as MAJOR (🔴) or MINOR (⚠️)

**MAJOR issues (🔴)** = fundamental flaws requiring re-analysis:
- No doublet removal
- No FDR correction
- Cell-level testing (pseudoreplication)
- No batch correction despite visible batch effects
- Causal claims without causal data

**MINOR issues (⚠️)** = need caveats/clarification in report:
- Missing software versions
- Visualization could be improved
- Interpretation could be more cautious
- Additional controls would strengthen claims

### Step 4: Write Critical Review Report

Create `results/reports/CRITICAL_REVIEW.md` with this structure:

```markdown
# Technical Review: [Analysis Title]

**Reviewer:** Dr. Reviewer
**Date:** YYYY-MM-DD

## Overall Assessment: [APPROVE / REVISE / REJECT]

## Category Evaluation

| Category | Status | Issues |
|----------|--------|--------|
| Data Quality | [✅/⚠️/🔴] | [n] |
| Clustering | [status] | [n] |
| Statistics | [status] | [n] |
| Interpretation | [status] | [n] |
| Reproducibility | [status] | [n] |

## Detailed Findings

### 🔴 MAJOR ISSUES (Require Re-Analysis)

[For each major issue:]

**Issue 1: [Title]**
- **Problem**: [Specific description]
- **Location**: [Script file:line number or report section]
- **Impact**: [Why this is a major problem]
- **Required Action**: [Specific fix needed]
- **Code Fix**: [If applicable, show the corrected code]

### ⚠️ MINOR ISSUES (Require Caveats/Clarification)

[For each minor issue:]

**Issue 1: [Title]**
- **Problem**: [Specific description]
- **Impact**: [Why this matters]
- **Suggested Action**: [How to address in report]

### ✅ STRENGTHS

[Positive aspects of the analysis - reinforce good practices]

## Recommendation: [REJECT / REVISE / APPROVE]

**REJECT**: Return to Stage 2, fix all major issues, re-run analyses, then re-review.

**REVISE**: Update draft report with caveats for all minor issues, then proceed to Stage 3C.

**APPROVE**: Proceed to Stage 3C to create final report.

## Justification

[Brief explanation of the decision]
```

### Step 5: Give Decision

**REJECT**: If ANY major issues (🔴) found:
- Return to Stage 2 immediately
- Fix ALL major issues
- Re-run affected analyses
- Update draft report
- Run this review again

**REVISE**: If only minor issues (⚠️) found:
- Add specific caveats to draft report for each issue
- Soften interpretations where appropriate
- Update limitations section
- Then proceed to Stage 3C

**APPROVE**: If no issues OR all issues already addressed:
- Proceed directly to Stage 3C
- Create final report

## Example Review (Abbreviated)

```markdown
# Technical Review: COVID-19 Lung Single-Cell Analysis

**Reviewer:** Dr. Reviewer
**Date:** 2025-11-16

## Overall Assessment: REVISE

## Category Evaluation

| Category | Status | Issues |
|----------|--------|--------|
| Data Quality | ✅ | 0 |
| Clustering | ✅ | 0 |
| Statistics | ⚠️ | 2 |
| Interpretation | ⚠️ | 1 |
| Reproducibility | ✅ | 0 |

## Detailed Findings

### 🔴 MAJOR ISSUES
None found.

### ⚠️ MINOR ISSUES

**Issue 1: Sample size not clearly stated**
- **Problem**: Differential expression results don't specify n donors per condition
- **Impact**: Readers can't assess statistical power
- **Suggested Action**: Add to methods: "Pseudobulk DE tested N=6 COVID donors vs N=4 healthy donors"

**Issue 2: Pathway enrichment uses single method**
- **Problem**: Only gseapy used, no decoupler consensus
- **Impact**: Higher risk of false positive pathways
- **Suggested Action**: Add limitation: "Pathway enrichment used GSEA only; consensus methods would strengthen confidence in findings"

**Issue 3: Causal language in discussion**
- **Problem**: Report states "IFN signaling causes T cell exhaustion" (page 12)
- **Impact**: Overclaims causality from correlative data
- **Suggested Action**: Revise to "IFN signaling is associated with markers of T cell exhaustion"

### ✅ STRENGTHS
- Excellent QC with doublet removal
- Proper pseudobulk DE with PyDESeq2
- Strong batch correction validation (iLISI=11.2/13)
- Clear documentation of methods
- All code is reproducible

## Recommendation: REVISE

**Justification**: No major flaws, but three minor issues need to be addressed with caveats in the final report. Once these are added to the limitations section and language is softened appropriately, the analysis is ready for final report.
```

## Checklist for Reviewer

- [ ] Read draft report completely
- [ ] Reviewed all analysis scripts
- [ ] Evaluated all 5 categories
- [ ] Classified issues as major (🔴) or minor (⚠️)
- [ ] Created CRITICAL_REVIEW.md with structured format
- [ ] Gave clear decision (REJECT / REVISE / APPROVE)
- [ ] Provided specific action items for each issue
- [ ] Identified positive aspects (strengths)

## Important Reminders

**Be objective**: Review the work as if someone else did it. Don't be lenient because "I know what I meant."

**Be specific**: Don't just say "statistics are wrong" - explain exactly what's wrong and how to fix it.

**Be constructive**: Include strengths, not just weaknesses. Positive reinforcement helps identify what to keep doing.

**Be actionable**: Each issue should have a clear path to resolution.
