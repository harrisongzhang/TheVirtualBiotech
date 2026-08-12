#!/usr/bin/env python3
"""
Genetics MCP Server
FastMCP server providing access to Open Targets genetic association data

Usage:
    python server.py

Tools provided:
- query_gwas_associations: Get GWAS and QTL associations by study/variant/region
- query_l2g_predictions: Get locus-to-gene predictions for causal genes
- get_credible_sets: Get fine-mapped credible sets
- get_qtl_colocalization: Get QTL colocalization evidence
- convert_rsid_to_variant_id: Convert rsID to variant_id format (FAST - use before get_variant_annotation)
- get_variant_annotation: Get variant annotations (consequences, allele frequencies)
- get_study_metadata: Get GWAS study metadata (sample sizes, ancestry)
- query_regulatory_regions: Query enhancer-gene regulatory interactions
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.genetics_mcp.tools import (
    query_gwas_associations,
    query_l2g_predictions,
    get_credible_sets,
    get_qtl_colocalization,
    convert_rsid_to_variant_id,
    get_variant_annotation,
    get_study_metadata,
    query_regulatory_regions,
    query_colocalisation,
    get_colocalisation_by_chromosome
)

# Initialize FastMCP server
mcp = FastMCP("Genetics MCP")

# Register tools
mcp.tool()(query_gwas_associations)
mcp.tool()(query_l2g_predictions)
mcp.tool()(get_credible_sets)
mcp.tool()(get_qtl_colocalization)
mcp.tool()(convert_rsid_to_variant_id)
mcp.tool()(get_variant_annotation)
mcp.tool()(get_study_metadata)
mcp.tool()(query_regulatory_regions)
mcp.tool()(query_colocalisation)
mcp.tool()(get_colocalisation_by_chromosome)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
