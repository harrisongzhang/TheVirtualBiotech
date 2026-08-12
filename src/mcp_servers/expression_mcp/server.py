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
mcp.tool()(list_available_tissues)
mcp.tool()(query_expression_by_gene)
mcp.tool()(query_expression_by_tissue)
mcp.tool()(compare_expression_across_tissues)
mcp.tool()(find_tissue_specific_genes)
mcp.tool()(search_biosample_ontology)

if __name__ == "__main__":
    # Run server (banner disabled for faster startup with multiple servers)
    mcp.run(show_banner=False)
