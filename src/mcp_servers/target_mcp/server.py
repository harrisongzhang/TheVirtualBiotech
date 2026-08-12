#!/usr/bin/env python3
"""
Target MCP Server
FastMCP server providing access to Open Targets target annotation and safety data

Usage:
    python server.py

Tools provided:
- get_target_info: Get target annotations by Ensembl ID
- search_targets_by_name: Search for targets by gene symbol or name
- get_target_tractability: Get druggability predictions
- get_target_prioritisation_scores: Get multi-factor target scoring
- prioritize_targets: Multi-factor target prioritization and filtering
- get_target_safety_profile: Get adverse events for a target
- get_mouse_phenotype: Get mouse knockout phenotypes
- get_pharmacogenomics: Get pharmacogenomics relationships
- get_target_hallmarks: Get cancer hallmark annotations
- get_target_tep: Get Target Enabling Package info
- get_chemical_probes: Get available chemical probes
- get_genetic_constraint: Get gnomAD constraint metrics
- get_subcellular_locations: Get protein localization
- get_target_class: Get ChEMBL target classification
- get_homologues: Get cross-species homologue info
- get_comprehensive_target_profile: Get comprehensive target profile
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.target_mcp.tools import (
    get_target_info,
    search_targets_by_name,
    get_target_tractability,
    get_target_prioritisation_scores,
    prioritize_targets,
    get_target_safety_profile,
    get_mouse_phenotype,
    get_pharmacogenomics,
    get_comprehensive_target_profile,
    # Phase 1: Additional target characterization tools
    get_target_hallmarks,
    get_target_tep,
    get_chemical_probes,
    get_genetic_constraint,
    get_subcellular_locations,
    get_target_class,
    get_homologues
)

# Initialize FastMCP server
mcp = FastMCP("Target MCP")

# Register tools
mcp.tool()(get_target_info)
mcp.tool()(search_targets_by_name)
mcp.tool()(get_target_tractability)
mcp.tool()(get_target_prioritisation_scores)
mcp.tool()(prioritize_targets)
mcp.tool()(get_target_safety_profile)
mcp.tool()(get_mouse_phenotype)
mcp.tool()(get_pharmacogenomics)
mcp.tool()(get_comprehensive_target_profile)
# Phase 1: Additional target characterization tools
mcp.tool()(get_target_hallmarks)
mcp.tool()(get_target_tep)
mcp.tool()(get_chemical_probes)
mcp.tool()(get_genetic_constraint)
mcp.tool()(get_subcellular_locations)
mcp.tool()(get_target_class)
mcp.tool()(get_homologues)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
