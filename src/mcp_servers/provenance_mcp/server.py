#!/usr/bin/env python3
"""
Provenance MCP Server
FastMCP server for the run's audit record: claims, artifact descriptions, plan.

Unlike the other MCP servers, this one does not read an external database — it
reads and writes the current run's own audit trail (MANIFEST.json, trace.jsonl,
evidence/claims.json). It is how agents put evidence on the record in a form
that is checked rather than trusted.

Usage:
    python server.py

Tools provided:
- record_claims:     file claim→evidence objects; validated on write
- register_artifact: describe what one of your outputs shows
- write_plan:        record the analysis DAG before dispatching specialists
- list_artifacts:    list this run's citable evidence
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from fastmcp import FastMCP
from src.mcp_servers.provenance_mcp.tools import (
    record_claims,
    register_artifact,
    write_plan,
    list_artifacts,
)

# Initialize FastMCP server
mcp = FastMCP("Provenance MCP")

# Register tools
mcp.tool()(record_claims)
mcp.tool()(register_artifact)
mcp.tool()(write_plan)
mcp.tool()(list_artifacts)

if __name__ == "__main__":
    # Run server
    mcp.run(show_banner=False)
