#!/usr/bin/env python3
"""
Single-Cell MCP Server

Provides access to CELLxGENE Census single-cell RNA-seq data (100M+ human
cells, cloud-based):
   - Disease, development, and diverse experimental conditions
   - Cross-tissue queries spanning multiple organs
   - 1000s of datasets from published studies

## CENSUS TOOLS

Tier 1 - Essential Discovery & Retrieval:
- get_census_info: Get Census version and statistics
- list_metadata_values: Explore available metadata values
- search_genes: Find genes by symbol or Ensembl ID
- query_cell_metadata: Preview cell metadata before retrieval
- get_anndata: Retrieve filtered AnnData objects (main tool)

Tier 2 - Query Planning & Statistics:
- count_cells: Count cells matching filter (check size before retrieval)
- get_gene_statistics: Get detailed gene-level statistics
- summarize_datasets: Get dataset information

Tier 3 - Advanced/Specialized:
- get_cell_type_tissue_matrix: Crosstab of cell types × tissues
- get_expression_for_genes: Get expression without full AnnData
- get_anndata_donor_balanced: Donor-balanced sampling for pseudobulk DE

Usage:
    python -m src.mcp_servers.single_cell_mcp.server
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.single_cell_mcp.tools import (
    # Census Tier 1
    get_census_info,
    list_metadata_values,
    search_genes,
    query_cell_metadata,
    get_anndata,
    # Census Tier 2
    count_cells,
    get_gene_statistics,
    summarize_datasets,
    # Census Tier 3
    get_cell_type_tissue_matrix,
    get_expression_for_genes,
    get_anndata_donor_balanced,
)

# Initialize FastMCP server
mcp = FastMCP("Single Cell Data")

# ============================================================================
# CENSUS TOOLS - Cloud-based access to 100M+ cells
# ============================================================================

# Register Tier 1 tools - Essential Discovery & Retrieval
mcp.tool()(get_census_info)
mcp.tool()(list_metadata_values)
mcp.tool()(search_genes)
mcp.tool()(query_cell_metadata)
mcp.tool()(get_anndata)

# Register Tier 2 tools - Query Planning & Statistics
mcp.tool()(count_cells)
mcp.tool()(get_gene_statistics)
mcp.tool()(summarize_datasets)

# Register Tier 3 tools - Advanced/Specialized
mcp.tool()(get_cell_type_tissue_matrix)
mcp.tool()(get_expression_for_genes)
mcp.tool()(get_anndata_donor_balanced)

if __name__ == "__main__":
    mcp.run(show_banner=False)
