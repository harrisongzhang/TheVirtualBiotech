#!/usr/bin/env python3
"""
Functional Genomics MCP Server
FastMCP server providing access to CRISPR screens, DepMap essentiality, and Tahoe drug perturbations

Usage:
    python server.py

CRISPR Essentiality Tools:
- query_gene_essentiality: Get CRISPR essentiality data for a gene
- find_essential_genes: Find essential genes in a disease
- query_cell_line_dependency: Get gene dependencies for a cell line
- compare_essentiality_across_diseases: Compare essentiality patterns
- find_selective_dependencies: Identify disease-selective dependencies

Tahoe Drug Perturbation Tools:
- query_drug_perturbation: Get transcriptomic effects of drug treatment
- find_drugs_affecting_gene: Find drugs that modulate a gene
- compare_drug_effects: Compare signatures between two drugs
- find_cell_line_selective_effects: Identify cancer-selective vulnerabilities
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.registration import register_tool
from src.mcp_servers.functional_genomics_mcp.tools import (
    query_gene_essentiality,
    find_essential_genes,
    query_cell_line_dependency,
    compare_essentiality_across_diseases,
    find_selective_dependencies,
    query_drug_perturbation,
    find_drugs_affecting_gene,
    compare_drug_effects,
    find_cell_line_selective_effects
)

# Initialize FastMCP server
mcp = FastMCP("Functional Genomics MCP")

# Register CRISPR essentiality tools
register_tool(mcp, query_gene_essentiality)
register_tool(mcp, find_essential_genes)
register_tool(mcp, query_cell_line_dependency)
register_tool(mcp, compare_essentiality_across_diseases)
register_tool(mcp, find_selective_dependencies)

# Register Tahoe drug perturbation tools
register_tool(mcp, query_drug_perturbation)
register_tool(mcp, find_drugs_affecting_gene)
register_tool(mcp, compare_drug_effects)
register_tool(mcp, find_cell_line_selective_effects)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
