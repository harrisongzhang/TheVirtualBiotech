#!/usr/bin/env python3
"""
MCP Server Configuration Setup
Automatically configures MCP servers to work with the current conda environment

Run this after activating the conda environment:
    source activate.sh
    python setup_mcp.py
"""

import os
import sys
import json
from pathlib import Path


def setup_mcp_config():
    """Generate MCP config with correct conda environment settings"""

    # Get the current Python executable path
    python_exe = sys.executable

    # Get absolute path to project root
    project_root = Path(__file__).parent.resolve()

    print("=" * 70)
    print("MCP SERVER CONFIGURATION SETUP")
    print("=" * 70)
    print(f"Python executable: {python_exe}")
    print(f"Project root: {project_root}")

    # Verify we're in an isolated environment. Test the interpreter rather than
    # its path: a venv is as valid as a conda env here, and matching on the
    # string "conda" rejects perfectly good environments (and would accept a
    # system python that merely lives under a directory named "conda").
    in_venv = sys.prefix != getattr(sys, 'base_prefix', sys.prefix)
    in_conda = bool(os.environ.get('CONDA_PREFIX') or os.environ.get('CONDA_DEFAULT_ENV')) \
        or 'conda' in python_exe.lower() or 'envs' in python_exe
    if not (in_venv or in_conda):
        print("\n⚠️  WARNING: not running inside an isolated environment!")
        print(f"   Interpreter: {python_exe}")
        print("   Activate one first (source activate.sh, or activate.local.sh),")
        print("   then run this script again.")
        return False

    # Environment passed to every MCP server subprocess.
    #
    # The config's "env" replaces the child environment rather than extending
    # it, so anything the interpreter needs to start must be named here. A venv
    # built on a module-provided Python needs LD_LIBRARY_PATH to find
    # libpythonX.Y.so — without it every server dies at exec with a shared
    # library error, which surfaces only as "MCP connection closed".
    server_env = {}
    for var in ("LD_LIBRARY_PATH", "PYTHONNOUSERSITE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if os.environ.get(var):
            server_env[var] = os.environ[var]

    # Define MCP server configurations
    mcp_config = {
        "mcpServers": {
            "expression": {
                "command": python_exe,
                "args": [
                    "-I",  # Isolated mode to avoid system package conflicts
                    str(project_root / "src/mcp_servers/expression_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "functional_genomics": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/functional_genomics_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "genetics": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/genetics_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "target": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/target_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "drug": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/drug_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "single_cell": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/single_cell_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "association": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/association_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "disease": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/disease_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "interaction": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/interaction_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "pathway": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/pathway_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            "clinicaltrials": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/clinicaltrials_mcp/server.py")
                ],
                "env": dict(server_env)
            },
            # The run's own audit record: claims, artifact descriptions, plan.
            # Unlike the others this reads/writes the active run directory
            # rather than an external database.
            "provenance": {
                "command": python_exe,
                "args": [
                    "-I",
                    str(project_root / "src/mcp_servers/provenance_mcp/server.py")
                ],
                "env": dict(server_env)
            }
        }
    }

    # Preserve non-stdio (e.g. HTTP) servers from existing config.
    # Written next to this script, not into the current directory: run.py and
    # the web app look for it beside the repo root, so a config written
    # elsewhere is silently never read.
    config_path = project_root / "mcp_config.json"
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                existing = json.load(f)
            for name, cfg in existing.get("mcpServers", {}).items():
                if name not in mcp_config["mcpServers"] and "command" not in cfg:
                    mcp_config["mcpServers"][name] = cfg
                    print(f"  ↳ Preserved external server: {name}")
        except Exception:
            pass  # If existing config is broken, just overwrite

    # Write configuration
    with open(config_path, 'w') as f:
        json.dump(mcp_config, f, indent=2)

    print(f"\n✓ MCP configuration written to: {config_path}")
    print("\nConfigured servers:")
    for server_name in mcp_config["mcpServers"].keys():
        print(f"  • {server_name}")

    print("\n" + "=" * 70)
    print("SETUP COMPLETE")
    print("=" * 70)
    print("\nMCP servers are now configured to use:")
    print(f"  Python: {python_exe}")
    print(f"  Mode: Isolated (-I flag)")
    print("\nThis ensures MCP servers avoid system package conflicts.")

    return True


if __name__ == "__main__":
    success = setup_mcp_config()
    sys.exit(0 if success else 1)
