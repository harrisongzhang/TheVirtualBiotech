#!/usr/bin/env python3
"""
Clinical Data MCP Server
FastMCP server providing access to clinical and cancer genomics data

Data Sources:
1. ClinicalTrials.gov API v2 - Clinical trial registry data
2. cBioPortal API - Cancer genomics and clinical outcomes data

Usage:
    python server.py

Tools provided:

ClinicalTrials.gov Tools:
- get_clinical_trial_details: Get comprehensive trial information by NCT ID
- clear_trial_cache: Clear cached trial data

cBioPortal Tools:
- get_all_cancer_types: List all cancer types (vocabulary for querying)
- search_studies: Find cancer genomics studies by cancer type/gene
- get_study_details: Get detailed study information and available data types
- get_clinical_data: Download patient demographics and outcomes
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.clinicaltrials_mcp.tools import (
    # ClinicalTrials.gov tools
    get_clinical_trial_details,
    clear_trial_cache,
    search_clinical_trials,
    count_clinical_trials,
    # cBioPortal tools
    get_all_cancer_types,
    search_studies,
    get_study_details,
    get_clinical_data,
)

# Initialize FastMCP server
mcp = FastMCP("Clinical Data MCP")

# Register ClinicalTrials.gov tools
mcp.tool()(get_clinical_trial_details)
mcp.tool()(clear_trial_cache)
mcp.tool()(search_clinical_trials)
mcp.tool()(count_clinical_trials)

# Register cBioPortal tools
mcp.tool()(get_all_cancer_types)
mcp.tool()(search_studies)
mcp.tool()(get_study_details)
mcp.tool()(get_clinical_data)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
