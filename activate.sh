#!/bin/bash
# Activate the Virtual Biotech conda environment (`vbt`).
#
# Create it once from the pinned spec:
#     conda env create -f environment.yml        # -> conda env named `vbt`
#   or build the self-contained container:
#     apptainer build vbt.sif vbt.def
#
# Usage:  source activate.sh
#
# Machine-specific setup (a differently-named env, a prefix path)? Copy this to
# activate.local.sh (git-ignored) and edit it — run.sh uses that when present.

# Avoid PYTHONPATH leakage from cluster modules; isolate from user site-packages.
unset PYTHONPATH
export PYTHONNOUSERSITE=1

VBT_ENV="${VBT_ENV:-vbt}"

if command -v conda >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    source "$(conda info --base)/etc/profile.d/conda.sh"
    if conda activate "$VBT_ENV" 2>/dev/null; then
        echo "✓ Activated conda env: $VBT_ENV ($(python --version 2>&1))"
        # Configure MCP servers for this env (absolute interpreter + paths).
        [ -f setup_mcp.py ] && python setup_mcp.py >/dev/null 2>&1 && echo "✓ MCP servers configured"
    else
        echo "[activate.sh] conda env '$VBT_ENV' not found." >&2
        echo "  Create it:  conda env create -f environment.yml" >&2
        return 1 2>/dev/null || exit 1
    fi
else
    echo "[activate.sh] conda not found on PATH." >&2
    echo "  Create the env:  conda env create -f environment.yml" >&2
    echo "  or run inside the Apptainer image (see vbt.def)." >&2
    return 1 2>/dev/null || exit 1
fi
