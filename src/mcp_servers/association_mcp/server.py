#!/usr/bin/env python3
"""
Association MCP Server
FastMCP server providing access to target-disease associations

Usage:
    python server.py

Tools provided:
- query_associations: Query target-disease associations with filters
- get_associations_for_disease: Get all targets associated with a disease
- get_associations_for_target: Get all diseases associated with a target
- compare_direct_indirect: Compare direct vs indirect association evidence
- filter_by_datatype: Filter associations by evidence datatype
- filter_by_datasource: Filter associations by data source
- query_evidence: Query detailed evidence strings
- get_evidence_by_publication: Get evidence by PubMed ID
- search_literature: Search publication metadata
- find_similar_entities: Find semantically similar entities (NEW)
- compute_entity_similarity: Compute similarity between two entities (NEW)
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.association_mcp.tools import (
    query_associations,
    get_associations_for_disease,
    get_associations_for_target,
    compare_direct_indirect,
    filter_by_datatype,
    filter_by_datasource,
    query_evidence,
    get_evidence_by_publication,
    search_literature,
    find_similar_entities,          # NEW: Literature vector similarity search
    compute_entity_similarity        # NEW: Pairwise entity similarity
)

# Initialize FastMCP server
mcp = FastMCP("Association MCP")

# Register tools
mcp.tool()(query_associations)
mcp.tool()(get_associations_for_disease)
mcp.tool()(get_associations_for_target)
mcp.tool()(compare_direct_indirect)
mcp.tool()(filter_by_datatype)
mcp.tool()(filter_by_datasource)
mcp.tool()(query_evidence)
mcp.tool()(get_evidence_by_publication)
mcp.tool()(search_literature)
mcp.tool()(find_similar_entities)      # NEW: Literature vector similarity search
mcp.tool()(compute_entity_similarity)  # NEW: Pairwise entity similarity

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
