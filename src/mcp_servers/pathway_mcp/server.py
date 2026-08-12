#!/usr/bin/env python3
"""
Pathway MCP Server
FastMCP server providing access to pathway and ontology data (Reactome, GO, SO)

Usage:
    python server.py

Tools provided:
- get_gene_pathways: Get Reactome pathways for a gene
- search_pathways: Search pathways by name or pathway ID
- get_gene_ontology: Get GO annotations for a gene
- search_go_terms: Search Gene Ontology terms
- find_genes_in_pathway: Find all genes in a specific pathway
- get_pathway_enrichment: Perform pathway enrichment analysis for gene list
- get_go_enrichment: Perform GO enrichment analysis for gene list (NEW)
- get_go_term_info: Get info for a specific GO term
- get_pathway_info: Get info for a specific pathway
- get_sequence_ontology_term: Get Sequence Ontology term info
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.pathway_mcp.tools import (
    get_gene_pathways,
    search_pathways,
    get_gene_ontology,
    search_go_terms,
    find_genes_in_pathway,
    get_pathway_enrichment,
    get_go_enrichment,          # NEW: GO enrichment analysis
    get_go_term_info,
    get_pathway_info,
    get_sequence_ontology_term
)

# Initialize FastMCP server
mcp = FastMCP("Pathway MCP")

# Register tools
mcp.tool()(get_gene_pathways)
mcp.tool()(search_pathways)
mcp.tool()(get_gene_ontology)
mcp.tool()(search_go_terms)
mcp.tool()(find_genes_in_pathway)
mcp.tool()(get_pathway_enrichment)
mcp.tool()(get_go_enrichment)           # NEW: GO enrichment analysis
mcp.tool()(get_go_term_info)
mcp.tool()(get_pathway_info)
mcp.tool()(get_sequence_ontology_term)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
