"""Scientist definitions shared by the conversational CLI and other interfaces."""

from pathlib import Path


def build_specialist_agents(prompts, workspace_dir: str = None, specialist_model: str = 'inherit'):
    """Build flat pool of all specialist agents

    Args:
        prompts: Dictionary of loaded system prompts
        workspace_dir: Optional workspace directory path to inject into prompts.
                      If provided, specialists will be instructed to write all files here.
        specialist_model: Model ID for specialist agents (from UI selection).
                         Chief of Staff and Scientific Reviewer remain pinned to 'haiku'.
    """
    from claude_agent_sdk import AgentDefinition

    agents = {}

    # Per-agent workspace instruction.
    #
    # Previously every specialist was handed the SAME directory, so a run ended
    # as one flat pile of files with no indication of which agent wrote what
    # (reviewer comment R2.5). Each specialist now owns a subtree of the run.
    # The manifest hook in CSOSession still records anything written outside it,
    # so organisation does not depend on the model complying with this text.
    def workspace_instruction(agent_name: str) -> str:
        if not workspace_dir:
            return ""
        ws = str(Path(workspace_dir) / "work" / agent_name)
        return f"""
IMPORTANT — YOUR WORKSPACE. All file operations (Write, Edit, Bash output files) MUST go
under YOUR OWN directory. Do not write to another agent's directory or to the run root:

{ws}

Use this layout, always with absolute paths:
  {ws}/code/scripts/     analysis scripts you write
  {ws}/data/raw/         data as pulled from a tool or database
  {ws}/data/processed/   data after your QC / transformation
  {ws}/results/figures/  plots (.png, .pdf)
  {ws}/results/tables/   result tables (.csv, .tsv, .parquet)
  {ws}/results/reports/  your written findings (.md)

Name files for what they contain, not for the order you made them: prefer
`il33_expression_by_celltype.csv` over `analysis2.csv`, and never `results_final_v3.csv`.
Every file you leave behind is an audit artifact someone else has to interpret.

Before starting, CHECK FOR EXISTING WORK from earlier in this run:
  ls {workspace_dir}/work/*/results/ {workspace_dir}/work/*/data/processed/ 2>/dev/null
If a prior analysis already produced what you need, load it rather than recomputing.
Always SAVE intermediate artifacts so later steps — and later auditors — can reuse them.

Before returning findings, call mcp__provenance__register_artifact for each
supporting file, with a short description. Return its exact registered path
and any supporting tool-use IDs so the CSO can file claims during this turn.
Files are also captured automatically; explicit registration adds context.

If a data tool fails, report the failed source and the resulting limitation.
A failed query provides no evidence about the target, drug, or indication.
Identify web or other replacement sources explicitly; never describe them as
results from the unavailable database. A successful query with zero matches
is a separate outcome and must retain its scope and filters.

"""

    # TARGET ID DIVISION
    agents['genomics-analyst'] = AgentDefinition(
        description='[Target ID] Genetic evidence: GWAS, L2G predictions, QTL colocalization, target tractability, druggability.',
        prompt=workspace_instruction('genomics-analyst') + prompts['genomics'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts',
            'mcp__genetics__query_gwas_associations', 'mcp__genetics__query_l2g_predictions',
            'mcp__genetics__get_credible_sets', 'mcp__genetics__get_qtl_colocalization',
            'mcp__genetics__convert_rsid_to_variant_id', 'mcp__genetics__get_variant_annotation',
            'mcp__genetics__get_study_metadata', 'mcp__genetics__query_regulatory_regions',
            'mcp__genetics__query_colocalisation', 'mcp__genetics__get_colocalisation_by_chromosome',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
            'mcp__target__get_target_tractability', 'mcp__target__get_target_prioritisation_scores',
            'mcp__target__prioritize_targets', 'mcp__target__get_target_safety_profile',
            'mcp__disease__get_disease_info', 'mcp__disease__search_diseases_by_name',
        ],
        model=specialist_model,
        effort='high',
        memory='project',
    )

    agents['functional-genomics-analyst'] = AgentDefinition(
        description='[Target ID] CRISPR essentiality, DepMap dependency, drug perturbation, cancer selectivity. CANCER ONLY.',
        prompt=workspace_instruction('functional-genomics-analyst') + prompts['functional_genomics'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts',
            'mcp__functional_genomics__query_gene_essentiality',
            'mcp__functional_genomics__find_essential_genes',
            'mcp__functional_genomics__query_cell_line_dependency',
            'mcp__functional_genomics__compare_essentiality_across_diseases',
            'mcp__functional_genomics__find_selective_dependencies',
            'mcp__functional_genomics__query_drug_perturbation',
            'mcp__functional_genomics__find_drugs_affecting_gene',
            'mcp__functional_genomics__compare_drug_effects',
            'mcp__functional_genomics__find_cell_line_selective_effects',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
        ],
        model=specialist_model,
        effort='high',
        memory='project',
    )

    agents['single-cell-analyst'] = AgentDefinition(
        description='[Target ID] Single-cell RNA-seq: cell type expression, differential expression, disease biology, CELLxGENE Census.',
        prompt=workspace_instruction('single-cell-analyst') + prompts['single_cell'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts',
            'WebFetch', 'WebSearch',
            # Single-cell MCP tools - CELLxGENE Census
            'mcp__single_cell__get_census_info',
            'mcp__single_cell__list_metadata_values',
            'mcp__single_cell__search_genes',
            'mcp__single_cell__query_cell_metadata',
            'mcp__single_cell__get_anndata',
            'mcp__single_cell__count_cells',
            # Target MCP tools
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
            # Expression MCP tools
            'mcp__expression__list_available_tissues',
            'mcp__expression__query_expression_by_gene',
            'mcp__expression__query_expression_by_tissue',
            'mcp__expression__compare_expression_across_tissues',
            'mcp__expression__find_tissue_specific_genes',
            'mcp__expression__search_biosample_ontology',
        ],
        model=specialist_model,
        effort='max',
        memory='project',
    )

    # TARGET SAFETY & CLINICAL OFFICERS (shared)
    agents['fda-safety-officer'] = AgentDefinition(
        description='[Target Safety & Clinical Officers] FDA regulatory safety: drug warnings, adverse events, target liabilities, mouse phenotypes, risk-benefit.',
        prompt=workspace_instruction('fda-safety-officer') + prompts['fda_safety'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts',
            'WebFetch', 'WebSearch',
            'mcp__drug__search_known_drugs', 'mcp__drug__get_drug_warnings',
            'mcp__drug__get_drug_indications', 'mcp__drug__get_drug_mechanisms',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
            'mcp__target__get_target_safety_profile', 'mcp__target__get_mouse_phenotype',
            'mcp__target__get_pharmacogenomics', 'mcp__target__get_homologues',
        ],
        model=specialist_model,
        effort='high',
        memory='project',
    )

    agents['bio-pathways-ppi-analyst'] = AgentDefinition(
        description='[Target Safety] Pathway context and PPI networks: Reactome pathways, GO annotations, protein interactions, network-based safety.',
        prompt=workspace_instruction('bio-pathways-ppi-analyst') + prompts['bio_pathways_ppi'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts',
            # Pathway MCP tools
            'mcp__pathway__get_gene_pathways', 'mcp__pathway__search_pathways',
            'mcp__pathway__get_gene_ontology', 'mcp__pathway__search_go_terms',
            'mcp__pathway__find_genes_in_pathway', 'mcp__pathway__get_pathway_enrichment',
            'mcp__pathway__get_go_enrichment', 'mcp__pathway__get_go_term_info',
            'mcp__pathway__get_pathway_info',
            # Interaction MCP tools
            'mcp__interaction__get_interactions', 'mcp__interaction__get_interaction_evidence',
            # Target MCP tools
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
        ],
        model=specialist_model,
        effort='high',
        memory='project',
    )

    # CLINICAL OFFICERS DIVISION
    agents['clinical-trialist'] = AgentDefinition(
        description='[Clinical Officers] Clinical trial data extraction: ClinicalTrials.gov, cBioPortal cancer genomics, trial outcomes, clinical precedence.',
        prompt=workspace_instruction('clinical-trialist') + prompts['clinical_trialist'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts',
            'WebFetch', 'WebSearch',
            # ClinicalTrials.gov tools
            'mcp__clinicaltrials__get_clinical_trial_details', 'mcp__clinicaltrials__clear_trial_cache',
            # cBioPortal tools
            'mcp__clinicaltrials__get_all_cancer_types', 'mcp__clinicaltrials__search_studies',
            'mcp__clinicaltrials__get_study_details', 'mcp__clinicaltrials__get_clinical_data',
            # Drug MCP for context
            'mcp__drug__search_known_drugs', 'mcp__drug__get_drug_mechanisms',
            'mcp__drug__get_drug_indications',
            # Target MCP tools
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
        ],
        model=specialist_model,
        effort='high',
        memory='project',
    )

    # MODALITY SELECTION DIVISION
    agents['target-biologist'] = AgentDefinition(
        description='[Modality] Protein structure, target biology: druggability, binding sites, localization, mechanism, pathway context.',
        prompt=workspace_instruction('target-biologist') + prompts['target_biologist'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
            'mcp__target__get_target_tractability', 'mcp__target__get_subcellular_locations',
            'mcp__target__get_target_class', 'mcp__target__get_chemical_probes',
            'mcp__target__get_homologues',
            'mcp__drug__search_known_drugs', 'mcp__drug__get_drug_mechanisms',
            'mcp__drug__get_drug_indications',
            'mcp__interaction__get_interactions', 'mcp__interaction__get_interaction_evidence',
            'mcp__pathway__get_gene_pathways', 'mcp__pathway__get_pathway_info',
            'mcp__pathway__find_genes_in_pathway',
            'mcp__expression__list_available_tissues',
            'mcp__expression__query_expression_by_gene',
            'mcp__expression__query_expression_by_tissue',
            'mcp__expression__compare_expression_across_tissues',
            'mcp__expression__find_tissue_specific_genes',
            'mcp__expression__search_biosample_ontology',
        ],
        model=specialist_model,
        effort='high',
        memory='project',
    )

    agents['medchem-pharmacologist'] = AgentDefinition(
        description='[Modality] Drug development: clinical precedence, modality ranking (top 3), feasibility, timeline, cost.',
        prompt=workspace_instruction('medchem-pharmacologist') + prompts['medchem'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts',
            'mcp__drug__search_known_drugs', 'mcp__drug__get_drug_mechanisms',
            'mcp__drug__get_drug_indications', 'mcp__drug__get_drug_warnings',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
            'mcp__target__get_target_tractability', 'mcp__target__get_chemical_probes',
            'mcp__target__get_homologues',
            'mcp__pathway__get_gene_pathways', 'mcp__pathway__find_genes_in_pathway',
            'mcp__interaction__get_interactions',
        ],
        model=specialist_model,
        effort='high',
        memory='project',
    )

    # CHIEF OF STAFF (Haiku-powered intelligence brief)
    agents['chief-of-staff'] = AgentDefinition(
        description='[Intelligence] Rapid due diligence: field overview, data landscape, recent news/context.',
        prompt=workspace_instruction('chief-of-staff') + prompts['chief_of_staff'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite',
            'WebFetch', 'WebSearch',
        ],
        model='haiku',
        memory='project',
    )

    # SCIENTIFIC REVIEWER (Haiku-powered quality assurance)
    agents['scientific-reviewer'] = AgentDefinition(
        description='[Quality Assurance] Review specialist outputs for scientific rigor, user alignment, logical conclusions.',
        prompt=workspace_instruction('scientific-reviewer') + prompts['scientific_reviewer'],
        tools=[
            'Read',
        ],
        model='haiku',
        memory='project',
    )

    # TRIAL MATCHING SPECIALIST
    agents['trial-matching-specialist'] = AgentDefinition(
        description='[Clinical Officers] Patient-to-trial matching: searches ClinicalTrials.gov for recruiting trials, evaluates eligibility criteria against patient profile, produces ranked recommendations.',
        prompt=workspace_instruction('trial-matching-specialist') + prompts['trial_matching'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite',
            'WebSearch', 'WebFetch',
            # ClinicalTrials.gov search + detail tools
            'mcp__clinicaltrials__search_clinical_trials',
            'mcp__clinicaltrials__count_clinical_trials',
            'mcp__clinicaltrials__get_clinical_trial_details',
            # cBioPortal tools (for genomic context)
            'mcp__clinicaltrials__get_all_cancer_types',
            'mcp__clinicaltrials__search_studies',
            # Drug MCP for mechanism/indication lookups
            'mcp__drug__search_known_drugs',
            'mcp__drug__get_drug_mechanisms',
            'mcp__drug__get_drug_indications',
            'mcp__drug__get_drug_warnings',
        ],
        model=specialist_model,
        effort='high',
        memory='project',
    )

    for name, agent in agents.items():
        if name != 'scientific-reviewer':
            for tool in ('Skill', 'mcp__provenance__register_artifact', 'mcp__provenance__list_artifacts'):
                if tool not in agent.tools:
                    agent.tools.append(tool)
    return agents
