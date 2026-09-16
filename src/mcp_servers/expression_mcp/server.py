#!/usr/bin/env python3
"""
Expression MCP Server
FastMCP server providing access to Open Targets baseline expression (RNA baseline + Human Protein Atlas protein IHC) data

Usage:
    python server.py

Tools provided:
- list_available_tissues: Discover all available tissues with expression data
- query_expression_by_gene: Get gene expression across tissues
- query_expression_by_tissue: Find expressed genes in a tissue
- compare_expression_across_tissues: Comparative expression analysis
- find_tissue_specific_genes: Identify tissue-enriched genes
- search_biosample_ontology: Search biosample/tissue ontology
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.registration import register_tool
from src.mcp_servers.expression_mcp.tools import (
    list_available_tissues,
    query_expression_by_gene,
    query_expression_by_tissue,
    compare_expression_across_tissues,
    find_tissue_specific_genes,
    search_biosample_ontology
)

# Initialize FastMCP server
mcp = FastMCP("Expression MCP")

# Register tools
register_tool(mcp, list_available_tissues)
register_tool(mcp, query_expression_by_gene)
register_tool(mcp, query_expression_by_tissue)
register_tool(mcp, compare_expression_across_tissues)
register_tool(mcp, find_tissue_specific_genes)
register_tool(mcp, search_biosample_ontology)

if __name__ == "__main__":
    # Run server (banner disabled for faster startup with multiple servers)
    mcp.run(show_banner=False)
