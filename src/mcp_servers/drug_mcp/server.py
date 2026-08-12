#!/usr/bin/env python3
"""
Drug MCP Server
FastMCP server providing access to drug information, mechanisms, and safety data

Usage:
    python server.py

Tools provided:
- search_drugs: Search drugs by name, target, or mechanism
- get_drug_info: Get comprehensive drug information
- get_target_tractability: Get druggability predictions for targets
- get_drug_indications: Get disease indications for a drug
- get_drug_warnings: Get safety warnings for a drug
- get_drug_adverse_events: Get significant adverse drug reactions
- get_drug_mechanisms: Get detailed drug mechanism of action
- search_known_drugs: Search clinical-stage drugs with target-disease validation
- get_pharmacogenomics: Get pharmacogenomics relationships
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.drug_mcp.tools import (
    search_drugs,
    get_drug_info,
    get_target_tractability,
    get_drug_indications,
    get_drug_warnings,
    get_drug_adverse_events,
    get_drug_mechanisms,
    search_known_drugs,
    get_pharmacogenomics
)

# Initialize FastMCP server
mcp = FastMCP("Drug MCP")

# Register tools
mcp.tool()(search_drugs)
mcp.tool()(get_drug_info)
mcp.tool()(get_target_tractability)
mcp.tool()(get_drug_indications)
mcp.tool()(get_drug_warnings)
mcp.tool()(get_drug_adverse_events)
mcp.tool()(get_drug_mechanisms)
mcp.tool()(search_known_drugs)
mcp.tool()(get_pharmacogenomics)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
