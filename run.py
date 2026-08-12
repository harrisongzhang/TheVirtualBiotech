"""
The Virtual Biotech — Interactive CLI

Headless interactive CLI for The Virtual Biotech multi-agent system.
Run any user query through the CSO and specialist pool, with per-turn
usage tracking and auditable session reports.

Usage:
    python3 run.py
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import sys
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.sdk_init_retry import RobustClaudeSDKClient
from src.utils.cost_tracker import CostTracker
from src.utils.trace_logger import TraceLogger, parse_agent_transcript, compute_agent_cost

# =============================================================================
# Configuration
# =============================================================================

SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

REPO_ROOT = Path(__file__).parent


def _resolve_mcp_config(mcp_servers: dict) -> dict:
    """Resolve portable mcp_config.json to absolute paths for this environment.

    Replaces:
    - "python" command → sys.executable (current conda env Python)
    - Relative server script paths → absolute paths from REPO_ROOT
    - "${VAR}" placeholders in server env/headers → environment variable values
    """
    resolved = {}
    for name, cfg in mcp_servers.items():
        cfg = dict(cfg)

        # Resolve python-based servers
        if cfg.get("command") in ("python", "python3"):
            cfg["command"] = sys.executable
            if cfg.get("args"):
                cfg["args"] = [
                    str(REPO_ROOT / a) if (not a.startswith("-") and not Path(a).is_absolute()) else a
                    for a in cfg["args"]
                ]

        # Resolve environment variable placeholders in headers
        if "headers" in cfg:
            cfg["headers"] = {
                k: os.environ.get(v.lstrip("${").rstrip("}"), v) if v.startswith("${") else v
                for k, v in cfg["headers"].items()
            }

        resolved[name] = cfg
    return resolved

# =============================================================================
# Permission Filter (package install blocking only — web access allowed)
# =============================================================================

async def tool_filter(
    tool_name: str,
    input_data: dict,
    context: dict
):
    """
    Permission callback for interactive sessions.

    Blocks package installation commands only.
    WebFetch and WebSearch are allowed.
    """
    if tool_name == "Bash":
        command = input_data.get("command", "")

        prohibited_patterns = [
            (r'\bpip\s+install\b', 'pip install'),
            (r'\bpip3\s+install\b', 'pip3 install'),
            (r'\bpython\s+-m\s+pip\s+install\b', 'python -m pip install'),
            (r'\bconda\s+install\b', 'conda install'),
            (r'\bapt-get\s+install\b', 'apt-get install'),
            (r'\byum\s+install\b', 'yum install'),
            (r'\bdnf\s+install\b', 'dnf install'),
            (r'\bnpm\s+install\b', 'npm install'),
            (r'\bnpm\s+i\b', 'npm i'),
            (r'\byarn\s+add\b', 'yarn add'),
            (r'\bbrew\s+install\b', 'brew install'),
            (r'\bgem\s+install\b', 'gem install'),
            (r'\bcargo\s+install\b', 'cargo install'),
            (r'\bpoetry\s+add\b', 'poetry add'),
            (r'\bpipenv\s+install\b', 'pipenv install'),
        ]

        for pattern, name in prohibited_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                print(f"[SECURITY] Blocked package installation attempt: {name}")
                return {
                    "behavior": "deny",
                    "message": f"Package installation is prohibited. Command blocked: '{name}'",
                    "interrupt": True
                }

    return {
        "behavior": "allow",
        "updatedInput": input_data
    }

# =============================================================================
# Prompt Loading
# =============================================================================

def load_prompts():
    """Load all specialist system prompts"""
    base = Path(__file__).parent / 'src' / 'agents'
    prompts = {}

    # CSO prompt
    with open(base / 'cso' / 'system_prompt.md') as f:
        prompts['cso'] = f.read()

    # Target ID Division specialists (shortened versions)
    with open(base / 'target_id' / 'system_prompts' / 'genomics_analyst_short.md') as f:
        prompts['genomics'] = f.read()

    with open(base / 'target_id' / 'system_prompts' / 'functional_genomics_analyst_short.md') as f:
        prompts['functional_genomics'] = f.read()

    with open(base / 'target_id' / 'system_prompts' / 'single_cell_analyst_short.md') as f:
        prompts['single_cell'] = f.read()

    # Target Safety Division specialists (shortened versions)
    with open(base / 'safety' / 'system_prompts' / 'fda_safety_officer_short.md') as f:
        prompts['fda_safety'] = f.read()

    with open(base / 'safety' / 'system_prompts' / 'bio_pathways_ppi_analyst_short.md') as f:
        prompts['bio_pathways_ppi'] = f.read()

    # Clinical Officers Division specialists
    with open(base / 'safety' / 'system_prompts' / 'clinical_trialist_short.md') as f:
        prompts['clinical_trialist'] = f.read()

    # Modality Selection Division specialists (shortened versions)
    with open(base / 'modality_selection' / 'system_prompts' / 'target_biologist_short.md') as f:
        prompts['target_biologist'] = f.read()

    with open(base / 'modality_selection' / 'system_prompts' / 'medchem_pharmacologist_short.md') as f:
        prompts['medchem'] = f.read()

    # Chief of Staff (Haiku-powered intelligence brief)
    with open(base / 'chief_of_staff' / 'system_prompt.md') as f:
        prompts['chief_of_staff'] = f.read()

    # Scientific Reviewer (Haiku-powered quality assurance)
    with open(base / 'scientific_reviewer' / 'system_prompt.md') as f:
        prompts['scientific_reviewer'] = f.read()

    # Trial Matching Specialist
    with open(base / 'trial_matching' / 'system_prompt.md') as f:
        prompts['trial_matching'] = f.read()

    return prompts


# =============================================================================
# Specialist Agent Builder
# =============================================================================

def build_specialist_agents(prompts, workspace_dir: str = None):
    """Build flat pool of all specialist agents.

    Args:
        prompts: Dictionary of loaded system prompts
        workspace_dir: Optional workspace directory path to inject into prompts.
    """
    from claude_agent_sdk import AgentDefinition

    agents = {}

    if workspace_dir:
        workspace_instruction = f"""
IMPORTANT: All file operations (Write, Edit, Bash output files) MUST use this workspace directory:
{workspace_dir}

When writing files, always use absolute paths starting with the workspace directory above.
Example: {workspace_dir}/analysis_results.parquet

"""
    else:
        workspace_instruction = ""

    # TARGET ID DIVISION
    agents['genomics-analyst'] = AgentDefinition(
        description='[Target ID] Genetic evidence: GWAS, L2G predictions, QTL colocalization, target tractability, druggability.',
        prompt=workspace_instruction + prompts['genomics'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
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
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    agents['functional-genomics-analyst'] = AgentDefinition(
        description='[Target ID] CRISPR essentiality, DepMap dependency, drug perturbation, cancer selectivity. CANCER ONLY.',
        prompt=workspace_instruction + prompts['functional_genomics'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
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
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    agents['single-cell-analyst'] = AgentDefinition(
        description='[Target ID] Single-cell RNA-seq: cell type expression, differential expression, disease biology, CELLxGENE Census.',
        prompt=workspace_instruction + prompts['single_cell'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__single_cell__get_census_info',
            'mcp__single_cell__list_metadata_values',
            'mcp__single_cell__search_genes',
            'mcp__single_cell__query_cell_metadata',
            'mcp__single_cell__get_anndata',
            'mcp__single_cell__count_cells',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
            'mcp__expression__list_available_tissues',
            'mcp__expression__query_expression_by_gene',
            'mcp__expression__query_expression_by_tissue',
            'mcp__expression__compare_expression_across_tissues',
            'mcp__expression__find_tissue_specific_genes',
            'mcp__expression__search_biosample_ontology',
        ],
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    # TARGET SAFETY & CLINICAL OFFICERS
    agents['fda-safety-officer'] = AgentDefinition(
        description='[Target Safety & Clinical Officers] FDA regulatory safety: drug warnings, adverse events, target liabilities, mouse phenotypes, risk-benefit.',
        prompt=workspace_instruction + prompts['fda_safety'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__drug__search_known_drugs', 'mcp__drug__get_drug_warnings',
            'mcp__drug__get_drug_indications', 'mcp__drug__get_drug_mechanisms',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
            'mcp__target__get_target_safety_profile', 'mcp__target__get_mouse_phenotype',
            'mcp__target__get_pharmacogenomics', 'mcp__target__get_homologues',
        ],
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    agents['bio-pathways-ppi-analyst'] = AgentDefinition(
        description='[Target Safety] Pathway context and PPI networks: Reactome pathways, GO annotations, protein interactions, network-based safety.',
        prompt=workspace_instruction + prompts['bio_pathways_ppi'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__pathway__get_gene_pathways', 'mcp__pathway__search_pathways',
            'mcp__pathway__get_gene_ontology', 'mcp__pathway__search_go_terms',
            'mcp__pathway__find_genes_in_pathway', 'mcp__pathway__get_pathway_enrichment',
            'mcp__pathway__get_go_enrichment', 'mcp__pathway__get_go_term_info',
            'mcp__pathway__get_pathway_info',
            'mcp__interaction__get_interactions', 'mcp__interaction__get_interaction_evidence',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
        ],
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    # CLINICAL OFFICERS DIVISION
    agents['clinical-trialist'] = AgentDefinition(
        description='[Clinical Officers] Clinical trial data extraction: ClinicalTrials.gov, cBioPortal cancer genomics, trial outcomes, clinical precedence.',
        prompt=workspace_instruction + prompts['clinical_trialist'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__clinicaltrials__get_clinical_trial_details', 'mcp__clinicaltrials__clear_trial_cache',
            'mcp__clinicaltrials__get_all_cancer_types', 'mcp__clinicaltrials__search_studies',
            'mcp__clinicaltrials__get_study_details', 'mcp__clinicaltrials__get_clinical_data',
            'mcp__drug__search_known_drugs', 'mcp__drug__get_drug_mechanisms',
            'mcp__drug__get_drug_indications',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
        ],
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    # MODALITY SELECTION DIVISION
    agents['target-biologist'] = AgentDefinition(
        description='[Modality] Protein structure, target biology: druggability, binding sites, localization, mechanism, pathway context.',
        prompt=workspace_instruction + prompts['target_biologist'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
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
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    agents['medchem-pharmacologist'] = AgentDefinition(
        description='[Modality] Drug development: clinical precedence, modality ranking (top 3), feasibility, timeline, cost.',
        prompt=workspace_instruction + prompts['medchem'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite', 'Skill', 'NotebookEdit',
            'mcp__drug__search_known_drugs', 'mcp__drug__get_drug_mechanisms',
            'mcp__drug__get_drug_indications', 'mcp__drug__get_drug_warnings',
            'mcp__target__get_target_info', 'mcp__target__search_targets_by_name',
            'mcp__target__get_target_tractability', 'mcp__target__get_chemical_probes',
            'mcp__target__get_homologues',
            'mcp__pathway__get_gene_pathways', 'mcp__pathway__find_genes_in_pathway',
            'mcp__interaction__get_interactions',
        ],
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    # CHIEF OF STAFF — gets WebSearch
    agents['chief-of-staff'] = AgentDefinition(
        description='[Intelligence] Rapid due diligence: field overview, data landscape, recent news/context.',
        prompt=workspace_instruction + prompts['chief_of_staff'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite',
            'WebFetch', 'WebSearch',
        ],
        model='haiku',
        memory='project',
    )

    # SCIENTIFIC REVIEWER
    agents['scientific-reviewer'] = AgentDefinition(
        description='[Quality Assurance] Review specialist outputs for scientific rigor, user alignment, logical conclusions.',
        prompt=workspace_instruction + prompts['scientific_reviewer'],
        tools=[
            'Read',
        ],
        model='haiku',
        memory='project',
    )

    # TRIAL MATCHING SPECIALIST
    agents['trial-matching-specialist'] = AgentDefinition(
        description='[Clinical Officers] Patient-to-trial matching: searches ClinicalTrials.gov for recruiting trials, evaluates eligibility criteria against patient profile, produces ranked recommendations.',
        prompt=workspace_instruction + prompts['trial_matching'],
        tools=[
            'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash', 'TodoWrite',
            'WebSearch', 'WebFetch',
            'mcp__clinicaltrials__search_clinical_trials',
            'mcp__clinicaltrials__count_clinical_trials',
            'mcp__clinicaltrials__get_clinical_trial_details',
            'mcp__clinicaltrials__get_all_cancer_types',
            'mcp__clinicaltrials__search_studies',
            'mcp__drug__search_known_drugs',
            'mcp__drug__get_drug_mechanisms',
            'mcp__drug__get_drug_indications',
            'mcp__drug__get_drug_warnings',
        ],
        model='inherit',
        effort='high',
        background=True,
        memory='project',
    )

    return agents


# =============================================================================
# Session Management
# =============================================================================

class Session:
    """Interactive REPL session with per-turn usage tracking."""

    def __init__(self, model: str = "claude-sonnet-4-5-20250929"):
        self.model = model
        self.start_time = datetime.now()
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        self.session_dir = SESSIONS_DIR / timestamp
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.workspace_dir = self.session_dir / "workspace"
        self.workspace_dir.mkdir(exist_ok=True)

        # Copy .claude/skills to workspace
        skills_src = Path(__file__).parent / '.claude' / 'skills'
        skills_dst = self.workspace_dir / '.claude' / 'skills'
        if skills_src.exists() and not skills_dst.exists():
            skills_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skills_src, skills_dst)

        # Usage tracking state
        self.turns = []
        self.previous_cumulative_cost = 0.0
        self.cost_tracker = CostTracker(model=self.model.rsplit('-', 1)[0])
        self.client = None
        self._shutdown_requested = False
        self.trace_logger = TraceLogger()

    def _build_hooks(self):
        """Build SDK hooks for fine-grained execution tracing.

        Captures sub-agent conversations (via transcript JSONL), tool calls
        with inputs/outputs, and timing — all routed to self.trace_logger.
        """
        from claude_agent_sdk import HookMatcher
        trace = self.trace_logger

        async def on_subagent_start(hook_input, _matcher, _ctx):
            try:
                trace.agent_start(hook_input['agent_id'],
                                  hook_input['agent_type'])
            except Exception:
                pass
            return {}

        async def on_subagent_stop(hook_input, _matcher, _ctx):
            try:
                tp = hook_input.get('agent_transcript_path', '')
                conv = parse_agent_transcript(tp) if tp else []
                cost = compute_agent_cost(tp) if tp else None
                trace.agent_stop(
                    hook_input['agent_id'], hook_input['agent_type'],
                    transcript_path=tp, conversation=conv, cost=cost,
                )
            except Exception:
                pass
            return {}

        async def on_pre_tool(hook_input, _matcher, _ctx):
            try:
                trace.tool_start(
                    hook_input['tool_use_id'], hook_input['tool_name'],
                    hook_input.get('tool_input', {}),
                )
            except Exception:
                pass
            return {}

        async def on_post_tool(hook_input, _matcher, _ctx):
            try:
                trace.tool_end(
                    hook_input['tool_use_id'], hook_input['tool_name'],
                    hook_input.get('tool_input', {}),
                    hook_input.get('tool_response', ''),
                )
            except Exception:
                pass
            return {}

        async def on_tool_error(hook_input, _matcher, _ctx):
            try:
                trace.tool_error(
                    hook_input['tool_use_id'], hook_input['tool_name'],
                    hook_input.get('tool_input', {}),
                    hook_input.get('error', 'unknown'),
                )
            except Exception:
                pass
            return {}

        return {
            'SubagentStart': [HookMatcher(hooks=[on_subagent_start])],
            'SubagentStop': [HookMatcher(hooks=[on_subagent_stop])],
            'PreToolUse': [HookMatcher(hooks=[on_pre_tool])],
            'PostToolUse': [HookMatcher(hooks=[on_post_tool])],
            'PostToolUseFailure': [HookMatcher(hooks=[on_tool_error])],
        }

    async def initialize(self):
        """Initialize the CSO client."""
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.types import ThinkingConfigAdaptive

        prompts = load_prompts()
        mcp_config_path = Path(__file__).parent / 'mcp_config.json'
        with open(mcp_config_path) as f:
            mcp_config = json.load(f)
        mcp_servers = _resolve_mcp_config(mcp_config.get('mcpServers', {}))

        specialist_agents = build_specialist_agents(prompts, workspace_dir=str(self.workspace_dir))

        cso_options = ClaudeAgentOptions(
            model=self.model,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": prompts['cso']
            },
            allowed_tools=[
                'Task', 'TodoWrite',
                'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash',
                'Skill', 'NotebookEdit',
                'WebFetch', 'WebSearch',
            ],
            agents=specialist_agents,
            mcp_servers=mcp_servers,
            max_turns=100,
            cwd=str(self.workspace_dir),
            permission_mode='bypassPermissions',
            can_use_tool=tool_filter,
            hooks=self._build_hooks(),
            thinking=ThinkingConfigAdaptive(type="adaptive"),
            effort="high",
        )

        print("[Initializing CSO client...]")
        self.client = await RobustClaudeSDKClient(
            options=cso_options,
            max_retries=5,
            initial_delay=3.0,
            backoff_factor=1.5,
            pre_warm=True,
            patch_timeout=True,
            verbose=True
        ).__aenter__()

        print(f"[CSO client ready]\n")

    async def run_turn(self, user_input: str) -> str:
        """Execute a single conversation turn.

        Returns:
            The CSO's response text.
        """
        from claude_agent_sdk import AssistantMessage, TextBlock, ThinkingBlock, ToolUseBlock, ResultMessage

        turn_number = len(self.turns) + 1
        turn_start = datetime.now()
        agents_dispatched = []
        mcp_tools_used = []
        response_text = ""
        trace_start = len(self.trace_logger.events)

        await self.client.query(user_input)

        async for msg in self.client.receive_response():
            if isinstance(msg, AssistantMessage):
                self.cost_tracker.process_message(msg)

                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(block.text, end="", flush=True)
                        if response_text and not response_text.endswith('\n'):
                            response_text += "\n\n"
                        response_text += block.text

                    elif isinstance(block, ThinkingBlock):
                        self.trace_logger.thinking(block.thinking)

                    elif isinstance(block, ToolUseBlock):
                        if block.name == 'Task':
                            agent = block.input.get('subagent_type', 'unknown')
                            desc = block.input.get('description', '')
                            agents_dispatched.append(agent)
                            print(f"\n\n[Delegating to {agent}: {desc}]\n")

                        elif block.name.startswith('mcp__'):
                            parts = block.name.split('__')
                            if len(parts) >= 3:
                                mcp_tools_used.append(block.name)
                                print(f"\n[MCP] {parts[1]}: {parts[2]}", end="")

                        elif block.name in ('WebFetch', 'WebSearch'):
                            print(f"\n[Tool: {block.name}]", end="")

                        elif block.name not in ('TodoWrite',):
                            print(f"\n[Tool: {block.name}]", end="")

            elif isinstance(msg, ResultMessage):
                self.cost_tracker.process_result(msg)
                cumulative_cost = getattr(msg, 'total_cost_usd', 0.0) or 0.0
                turn_cost = cumulative_cost - self.previous_cumulative_cost

                # Extract execution traces captured during this turn
                turn_events = self.trace_logger.events_since(trace_start)
                thinking_traces = self.trace_logger.extract_thinking(turn_events)
                subagent_traces = self.trace_logger.extract_subagent_traces(turn_events)

                turn_data = {
                    "turn": turn_number,
                    "timestamp": turn_start.isoformat(),
                    "prompt": user_input,
                    "response": response_text,
                    "cost_usd": round(turn_cost, 6),
                    "cumulative_cost_usd": round(cumulative_cost, 6),
                    "agents_dispatched": agents_dispatched,
                    "mcp_tools_used": mcp_tools_used,
                    "response_length_chars": len(response_text),
                    "thinking_traces": thinking_traces,
                    "subagent_traces": subagent_traces,
                }
                self.turns.append(turn_data)
                self.previous_cumulative_cost = cumulative_cost

                # Display turn summary banner
                print(f"\n")
                agents_str = ", ".join(agents_dispatched) if agents_dispatched else "(none)"
                print(f"--- Turn {turn_number} ---{''.rjust(38, '-')}")
                print(f"  Turn cost:    ${turn_cost:.2f}")
                print(f"  Total so far: ${cumulative_cost:.2f}")
                print(f"  Agents used:  {agents_str}")
                print(f"{''.rjust(48, '-')}")

        return response_text

    def print_summary(self):
        """Print current session summary."""
        total = self.previous_cumulative_cost
        print(f"\n{'='*50}")
        print(f"SESSION SUMMARY")
        print(f"{'='*50}")
        print(f"  Turns:      {len(self.turns)}")
        print(f"  Total cost: ${total:.2f}")
        if self.turns:
            print(f"\n  Per-turn breakdown:")
            for t in self.turns:
                agents = ", ".join(t['agents_dispatched']) if t['agents_dispatched'] else "(none)"
                print(f"    Turn {t['turn']}: ${t['cost_usd']:.2f}  [{agents}]")
        print(f"{'='*50}\n")

    def write_reports(self):
        """Write session_report.json and transcript.md to session directory."""
        end_time = datetime.now()

        # session_report.json
        report = {
            "session_dir": str(self.session_dir.name),
            "start_time": self.start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "total_cost_usd": round(self.previous_cumulative_cost, 6),
            "num_turns": len(self.turns),
            "trace_events": len(self.trace_logger.events),
            "turns": self.turns,
        }
        report_path = self.session_dir / "session_report.json"
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        print(f"[Written] {report_path}")

        # trace.jsonl — full event log (tool I/O, agent transcripts, reasoning)
        trace_path = self.session_dir / "trace.jsonl"
        self.trace_logger.write_jsonl(trace_path)
        print(f"[Written] {trace_path} ({len(self.trace_logger.events)} events)")

        # transcript.md
        lines = [
            f"# Virtual Biotech Session: {self.session_dir.name}",
            f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Ended: {end_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total cost: ${self.previous_cumulative_cost:.2f}",
            "",
        ]
        for t in self.turns:
            agents = ", ".join(t['agents_dispatched']) if t['agents_dispatched'] else "(none)"
            lines.append(f"## Turn {t['turn']}")
            lines.append(f"**User:** {t['prompt']}")
            lines.append("")
            lines.append(f"**CSO:** {t.get('response', '')}")
            lines.append("")
            lines.append(f"**Cost:** ${t['cost_usd']:.2f} (cumulative: ${t['cumulative_cost_usd']:.2f})")
            lines.append(f"**Agents:** {agents}")
            lines.append("")

            # CSO reasoning traces
            thinking = t.get('thinking_traces', [])
            if thinking:
                lines.append("### CSO Reasoning")
                for th in thinking:
                    preview = th.replace('\n', '\n> ')[:2000]
                    lines.append(f"> {preview}")
                    if len(th) > 2000:
                        lines.append(f"> *... [{len(th):,} chars total]*")
                    lines.append("")

            # Sub-agent trace summaries
            subagents = t.get('subagent_traces', [])
            if subagents:
                lines.append("### Sub-agent Traces")
                for sa in subagents:
                    dur = f" — {sa['duration_s']}s" if sa.get('duration_s') else ""
                    n_msgs = len(sa.get('conversation', []))
                    lines.append(f"- **{sa['agent_type']}**{dur} ({n_msgs} messages)")
                lines.append("")
                lines.append("*Full sub-agent conversations in session_report.json and trace.jsonl*")
                lines.append("")

        transcript_path = self.session_dir / "transcript.md"
        with open(transcript_path, 'w') as f:
            f.write("\n".join(lines))
        print(f"[Written] {transcript_path}")

    async def run_repl(self):
        """Run the interactive REPL loop."""
        print("=" * 60)
        print(f"THE VIRTUAL BIOTECH")
        print("=" * 60)
        print(f"Session dir: {self.session_dir}")
        print(f"Workspace:   {self.workspace_dir}")
        print()
        print("Commands:")
        print("  /done or quit  — end session, write reports")
        print("  /summary       — print current session summary")
        print("  /help          — show this help")
        print("  Ctrl+C         — graceful shutdown, write reports")
        print("=" * 60)
        print()

        await self.initialize()

        # Set up Ctrl+C handler
        loop = asyncio.get_event_loop()
        original_handler = signal.getsignal(signal.SIGINT)

        def sigint_handler(sig, frame):
            self._shutdown_requested = True
            print("\n\n[Ctrl+C received — finishing up and writing reports...]")
            # Restore original handler so a second Ctrl+C will force-exit
            signal.signal(signal.SIGINT, original_handler)

        signal.signal(signal.SIGINT, sigint_handler)

        try:
            while not self._shutdown_requested:
                try:
                    user_input = input("\nYou: ").strip()
                except EOFError:
                    break

                if not user_input:
                    continue

                # Multi-line mode: start with """ and end with """ on its own line
                if user_input == '"""' or user_input.startswith('"""'):
                    first_line = user_input[3:].strip()
                    lines = [first_line] if first_line else []
                    print('... (enter """ on its own line to finish)')
                    while True:
                        try:
                            line = input("... ")
                        except EOFError:
                            break
                        if line.strip() == '"""':
                            break
                        lines.append(line)
                    user_input = "\n".join(lines).strip()
                    if not user_input:
                        continue

                if user_input.lower() in ('/done', 'quit'):
                    break

                if user_input.lower() == '/summary':
                    self.print_summary()
                    continue

                if user_input.lower() == '/help':
                    print("Commands:")
                    print('  """            — start multi-line input (end with """ on its own line)')
                    print("  /done or quit  — end session, write reports")
                    print("  /summary       — print current session summary")
                    print("  /help          — show this help")
                    continue

                # Run the turn
                print("\nCSO: ", end="", flush=True)
                try:
                    await self.run_turn(user_input)
                except Exception as e:
                    print(f"\n[ERROR] Turn failed: {e}")
                    import traceback
                    traceback.print_exc()
                    print("[You can try again or type /done to finish.]")

        finally:
            # Always write reports
            print()
            self.print_summary()
            self.write_reports()

            # Disconnect client
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass

            signal.signal(signal.SIGINT, original_handler)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="The Virtual Biotech — interactive CSO command-line interface.",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-5-20250929",
        metavar="ANTHROPIC_MODEL_ID",
        help=(
            "Anthropic API model ID for the CSO and its specialist agents "
            "(default: claude-sonnet-4-5-20250929). Accepts any current model ID "
            "from https://platform.claude.com/docs/en/about-claude/models/overview "
            "(e.g. claude-opus-4-6). The chief-of-staff and scientific-reviewer "
            "agents always run on Haiku."
        ),
    )
    args = parser.parse_args()
    session = Session(model=args.model)
    asyncio.run(session.run_repl())


if __name__ == "__main__":
    main()
