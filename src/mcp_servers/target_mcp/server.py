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
from src.mcp_servers.registration import register_tool
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
register_tool(mcp, get_target_info)
register_tool(mcp, search_targets_by_name)
register_tool(mcp, get_target_tractability)
register_tool(mcp, get_target_prioritisation_scores)
register_tool(mcp, prioritize_targets)
register_tool(mcp, get_target_safety_profile)
register_tool(mcp, get_mouse_phenotype)
register_tool(mcp, get_pharmacogenomics)
register_tool(mcp, get_comprehensive_target_profile)
# Phase 1: Additional target characterization tools
register_tool(mcp, get_target_hallmarks)
register_tool(mcp, get_target_tep)
register_tool(mcp, get_chemical_probes)
register_tool(mcp, get_genetic_constraint)
register_tool(mcp, get_subcellular_locations)
register_tool(mcp, get_target_class)
register_tool(mcp, get_homologues)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
