#!/usr/bin/env python3
"""
Interaction MCP Server
FastMCP server providing access to protein-protein interaction data

Usage:
    python server.py

Tools provided:
- get_interactions: Get protein-protein interactions for a target
- search_interactions: Search interactions with flexible filters
- get_interaction_evidence: Get detailed experimental evidence
- get_interaction_network: Build multi-hop protein interaction network
- find_common_interactors: Find hub proteins connecting multiple targets
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.interaction_mcp.tools import (
    get_interactions,
    search_interactions,
    get_interaction_evidence,
    get_interaction_network,
    find_common_interactors
)

# Initialize FastMCP server
mcp = FastMCP("Interaction MCP")

# Register tools
mcp.tool()(get_interactions)
mcp.tool()(search_interactions)
mcp.tool()(get_interaction_evidence)
mcp.tool()(get_interaction_network)
mcp.tool()(find_common_interactors)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
