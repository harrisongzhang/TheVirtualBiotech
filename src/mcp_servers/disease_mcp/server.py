#!/usr/bin/env python3
"""
Disease MCP Server
FastMCP server providing access to Open Targets disease ontology and annotations

Usage:
    python server.py

Tools provided:
- get_disease_info: Get disease annotations by EFO ID
- search_diseases_by_name: Search for diseases by name or synonym
- get_disease_hierarchy: Get disease hierarchy (parents, children, ancestors)
- get_disease_phenotypes: Get HPO phenotypes associated with a disease
- find_diseases_by_therapeutic_area: Find diseases in a therapeutic area
- find_diseases_by_phenotype: Find diseases associated with a phenotype (NEW - reverse search)
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.disease_mcp.tools import (
    get_disease_info,
    search_diseases_by_name,
    get_disease_hierarchy,
    get_disease_phenotypes,
    find_diseases_by_therapeutic_area,
    find_diseases_by_phenotype          # NEW: Reverse phenotype search
)

# Initialize FastMCP server
mcp = FastMCP("Disease MCP")

# Register tools
mcp.tool()(get_disease_info)
mcp.tool()(search_diseases_by_name)
mcp.tool()(get_disease_hierarchy)
mcp.tool()(get_disease_phenotypes)
mcp.tool()(find_diseases_by_therapeutic_area)
mcp.tool()(find_diseases_by_phenotype)          # NEW: Reverse phenotype search

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
