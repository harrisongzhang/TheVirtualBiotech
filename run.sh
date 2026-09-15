#!/bin/bash
#
# run.sh — headless queries, setup checks, and an optional web interface.
#
# Interactive CLI (recommended; run from the repository root after setup):
#   conda activate vbt
#   python setup_mcp.py
#   python run.py
#
# Usage:
#   ./run.sh run "<query>" [...]        run headlessly; repeat for a conversation
#   ./run.sh run -f questions.txt       one turn per line
#   ./run.sh replay <RUN_ID>            re-run a past run's turns, then diff
#   ./run.sh verify <RUN_ID> [--rerun]  re-hash artifacts; --rerun re-executes code
#   ./run.sh audit <session_dir>        rebuild an audit trail for an old session
#   ./run.sh index                      rebuild runs/INDEX.md
#   ./run.sh doctor [--skip-api-key] [--smoke]  check local setup; no model requests
#   ./run.sh test                       run the audit test suite
#   ./run.sh web [--share]              launch the optional web interface
#
# Environment:
#   VBT_RUNS_DIR           where runs are written (default: ./runs)
#   CLAUDE_CONFIG_DIR      SDK config/transcripts (must be writable by you)
#   GRADIO_SERVER_PORT     web port (default: 7860)
#   BIOTECH_APP_PASSWORD   web access password (web only)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RUNS_DIR="${VBT_RUNS_DIR:-$SCRIPT_DIR/runs}"
export VBT_RUNS_DIR="$RUNS_DIR"

# ── Environment ──────────────────────────────────────────────────────

# Source activate.sh with -e/-u suspended. Conda's own activation scripts
# reference unset variables and return non-zero on a missing env, either of
# which would take this whole script down under `set -euo pipefail`.
source_activate() {
    # activate.local.sh wins when present for machine-specific activation.
    local script="$SCRIPT_DIR/activate.sh"
    [ -f "$SCRIPT_DIR/activate.local.sh" ] && script="$SCRIPT_DIR/activate.local.sh"
    [ -f "$script" ] || return 0
    set +eu
    # shellcheck disable=SC1091
    source "$script" >&2
    local rc=$?
    set -eu
    return "$rc"
}

# Resolve a Python 3. When conda activation fails, `python` on this cluster is
# system Python 2.7 — the audit tooling is stdlib-only but is not 2.7 code, so
# it must not silently run under the wrong interpreter.
resolve_python() {
    if command -v python >/dev/null 2>&1 && \
       python -c 'import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)' 2>/dev/null; then
        PY=python
    elif command -v python3 >/dev/null 2>&1; then
        PY=python3
    else
        echo "[error] no Python 3 found on PATH." >&2
        return 1
    fi
    export PY
}

activate_env() {
    if ! source_activate; then
        echo "[warn] activate.sh did not complete; using the ambient Python." >&2
    fi
    resolve_python
    export FASTMCP_SHOW_CLI_BANNER=false
    # Default to a per-user scratch location: the SDK writes sub-agent
    # transcripts here, and a directory owned by someone else silently breaks
    # sub-agent tracing (and therefore attribution).
    export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-${SCRATCH:-$HOME}/claude-config}"
    mkdir -p "$CLAUDE_CONFIG_DIR" 2>/dev/null || true
    # Python entry points load .env with python-dotenv. Do not parse or source it
    # here: shell parsing changes quoted values and can override exported keys.
    # Generate mcp_config.json for THIS environment: absolute interpreter + server
    # paths + isolation, so the SDK spawns each MCP server with the right Python.
    # A committed static config can't know where an installed env lives, so we
    # (re)generate at activation — this is what makes a fresh clone work.
    "$PY" setup_mcp.py >/dev/null 2>&1 || \
        echo "[warn] setup_mcp.py failed; MCP servers may not be configured" >&2
}

# ── doctor ───────────────────────────────────────────────────────────

cmd_doctor() {
    if ! source_activate 2>/dev/null; then
        echo "  [FAIL] activate.sh did not complete — the conda env is not usable."
        return 1
    fi
    resolve_python || return 1
    "$PY" "$SCRIPT_DIR/tools/doctor.py" "$@"
}

# ── Commands ─────────────────────────────────────────────────────────

cmd_web() {
    activate_env
    mkdir -p "$RUNS_DIR"
    echo "" >&2
    echo "  The Virtual Biotech — web interface" >&2
    echo "  runs → $RUNS_DIR" >&2
    echo "" >&2
    case "${1:-}" in
        --share) "$PY" -c 'import gradio_cso_app; gradio_cso_app.main(share=True)' ;;
        *) "$PY" gradio_cso_app.py ;;
    esac
}

cmd_run()    { activate_env; "$PY" run_vbt.py run "$@"; finalize_index; }
cmd_replay() { activate_env; "$PY" run_vbt.py replay "$@"; finalize_index; }

cmd_verify() {
    # Verification is pure-stdlib and must work even when the conda env does not.
    source_activate >/dev/null 2>&1 || true
    resolve_python
    "$PY" run_vbt.py verify "$@"
}

cmd_audit() {
    if [ $# -lt 1 ]; then
        echo "usage: ./run.sh audit <session_dir> [--all] [-o OUTPUT]" >&2
        return 2
    fi
    resolve_python
    "$PY" tools/audit_run.py "$@"
}

cmd_index() {
    resolve_python
    "$PY" -c "
import sys; sys.path.insert(0, '.')
from src.utils.run_index import update_index
rows = update_index('$RUNS_DIR')
print(f'{len(rows)} run(s) indexed → $RUNS_DIR/INDEX.md')
"
}

cmd_test() {
    local fail=0
    resolve_python
    for t in tests/test_audit_spine.py tests/test_run_lifecycle.py \
             tests/test_claim_ui.py tests/test_plan_and_verify.py \
             tests/test_regressions.py; do
        [ -f "$t" ] || continue
        echo "--- $t"
        "$PY" "$t" 2>&1 | tail -3 || fail=1
    done
    return "$fail"
}

finalize_index() { cmd_index >/dev/null 2>&1 || true; }

usage() {
    # The comment banner at the top of this file is the help text.
    sed -n '3,/^$/p' "$0" | sed 's/^#\{1,\} \{0,1\}//;s/^#$//'
}

# ── Dispatch ─────────────────────────────────────────────────────────

CMD="${1:-}"
[ $# -gt 0 ] && shift || true

case "$CMD" in
    web)     cmd_web "$@" ;;
    run)     cmd_run "$@" ;;
    replay)  cmd_replay "$@" ;;
    verify)  cmd_verify "$@" ;;
    audit)   cmd_audit "$@" ;;
    index)   cmd_index "$@" ;;
    doctor)  cmd_doctor "$@" ;;
    test)    cmd_test "$@" ;;
    ""|-h|--help|help) usage ;;
    *) echo "unknown command: $CMD" >&2; echo >&2; usage >&2; exit 2 ;;
esac
