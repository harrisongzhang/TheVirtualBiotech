"""
Agent Hooks for The Virtual Biotech

Provides SDK-native hooks (PreToolUse, PostToolUse, SubagentStop, Stop)
for security, auditing, cost tracking, and lifecycle management.

These hooks replace the older can_use_tool callback approach with the
standard Claude Agent SDK hooks API, which supports system message
injection, input modification, and hook chaining.

Usage:
    from src.utils.agent_hooks import build_hooks, SecurityConfig

    config = SecurityConfig(workspace_dir="/path/to/workspace")
    hooks = build_hooks(config)

    options = ClaudeAgentOptions(
        hooks=hooks,
        ...
    )
"""

import os
import re
import shlex
from pathlib import Path
from typing import Any, Union

from claude_agent_sdk.types import (
    PreToolUseHookInput,
    HookContext,
    SyncHookJSONOutput,
)


# =============================================================================
# Configuration
# =============================================================================

class SecurityConfig:
    """Configuration for the security guardrails hook."""

    def __init__(
        self,
        workspace_dir: str,
        app_source_dir: str | None = None,
        extra_read_dirs: list[str] | None = None,
        blocked_read_dirs: list[str] | None = None,
        block_pkg_install: bool = True,
        block_destructive_fs: bool = True,
        block_destructive_db: bool = True,
        block_system_commands: bool = True,
        enforce_path_sandbox: bool = True,
    ):
        self.workspace_dir = str(Path(workspace_dir).resolve())
        self.app_source_dir = str(Path(app_source_dir).resolve()) if app_source_dir else None
        self.block_pkg_install = block_pkg_install
        self.block_destructive_fs = block_destructive_fs
        self.block_destructive_db = block_destructive_db
        self.block_system_commands = block_system_commands
        self.enforce_path_sandbox = enforce_path_sandbox

        # Resolved paths for sandbox enforcement
        self.write_allowed = [self.workspace_dir]
        self.read_allowed = [self.workspace_dir]
        if self.app_source_dir:
            self.read_allowed.append(self.app_source_dir)
        if extra_read_dirs:
            for d in extra_read_dirs:
                self.read_allowed.append(str(Path(d).resolve()))

        # Blocked paths take precedence over read_allowed
        self.blocked_read_dirs = []
        if blocked_read_dirs:
            for d in blocked_read_dirs:
                self.blocked_read_dirs.append(str(Path(d).resolve()))


# =============================================================================
# Path utilities
# =============================================================================

def _is_path_within(path_str: str, allowed_roots: list[str],
                     blocked_roots: list[str] | None = None) -> bool:
    """Check if a path resolves to within one of the allowed root directories.

    If blocked_roots is provided, paths within blocked dirs are denied
    even if they fall within an allowed root (blocklist takes precedence).
    """
    try:
        resolved = str(Path(path_str).resolve())
        # Blocklist takes precedence
        if blocked_roots:
            for root in blocked_roots:
                if resolved == root or resolved.startswith(root + os.sep):
                    return False
        return any(
            resolved == root or resolved.startswith(root + os.sep)
            for root in allowed_roots
        )
    except (ValueError, OSError):
        return False


def _strip_heredocs(command: str) -> str:
    """Strip heredoc blocks (<<'EOF'...EOF) from a bash command.

    Agents frequently embed Python scripts via heredoc; scanning those
    for shell patterns produces false positives (e.g., 'kill' in a
    variable name, '/' as division operator).
    """
    return re.sub(
        r'<<\s*[\'"]?(\w+)[\'"]?.*?\n\1',
        '',
        command,
        flags=re.DOTALL,
    )


def _extract_paths_from_command(command: str, cwd: str) -> list[str]:
    """
    Extract file/directory paths referenced in a bash command.
    Handles redirect operators attached to paths (e.g., >/etc/passwd, 2>>/tmp/log).
    Strips heredoc content (<<'EOF'...EOF) to avoid parsing embedded scripts.
    """
    command_for_paths = _strip_heredocs(command)

    paths = []
    try:
        tokens = shlex.split(command_for_paths)
    except ValueError:
        tokens = command_for_paths.split()

    for token in tokens:
        if token.startswith('-') or token in ('|', '>', '>>', '<', '&&', '||', ';', '2>&1'):
            continue
        # Strip shell redirect prefixes: >/path, >>/path, 2>/path, 2>>/path, 1>/path
        cleaned = re.sub(r'^[012]?>?>?', '', token)
        if not cleaned:
            continue
        # Skip /dev/null — benign output suppression, not a real path reference
        if cleaned == '/dev/null':
            continue
        if '/' in cleaned or cleaned.startswith('.'):
            p = Path(cleaned) if os.path.isabs(cleaned) else Path(cwd) / cleaned
            try:
                paths.append(str(p.resolve()))
            except (ValueError, OSError):
                paths.append(str(p))
    return paths


# =============================================================================
# Pattern definitions
# =============================================================================

# Package installation patterns
PKG_INSTALL_PATTERNS = [
    (r'\bpip3?\s+install\b', 'pip install'),
    (r'\bpython3?\s+-m\s+pip\s+install\b', 'python -m pip install'),
    (r'\bconda\s+install\b', 'conda install'),
    (r'\bapt(?:-get)?\s+install\b', 'apt install'),
    (r'\byum\s+install\b', 'yum install'),
    (r'\bdnf\s+install\b', 'dnf install'),
    (r'\bnpm\s+(?:install|i)\b', 'npm install'),
    (r'\byarn\s+add\b', 'yarn add'),
    (r'\bbrew\s+install\b', 'brew install'),
    (r'\bgem\s+install\b', 'gem install'),
    (r'\bcargo\s+install\b', 'cargo install'),
]

# Destructive filesystem patterns
DESTRUCTIVE_FS_PATTERNS = [
    (r'\brm\b', 'rm (file deletion)'),
    (r'\brmdir\b', 'rmdir'),
    (r'\bunlink\b', 'unlink (file deletion)'),
    (r'\bshred\b', 'shred'),
    (r'\bmkfs\b', 'mkfs'),
    (r'\bdd\b', 'dd'),
    (r'\bmv\b', 'mv (move/rename)'),
    (r'\bln\b', 'ln (symlink/hardlink)'),
    (r'\bchmod\b', 'chmod'),
    (r'\bchown\b', 'chown'),
    (r'\bcurl\b.*\|\s*(?:bash|sh|zsh)\b', 'curl pipe to shell'),
    (r'\bwget\b.*\|\s*(?:bash|sh|zsh)\b', 'wget pipe to shell'),
    (r'>\s*/dev/(?!null\b)', 'write to /dev'),
]

# Destructive database/SQL patterns
DESTRUCTIVE_DB_PATTERNS = [
    (r'\bDROP\s+TABLE\b', 'DROP TABLE'),
    (r'\bDROP\s+DATABASE\b', 'DROP DATABASE'),
    (r'\bDROP\s+SCHEMA\b', 'DROP SCHEMA'),
    (r'\bTRUNCATE\b', 'TRUNCATE'),
    (r'\bDELETE\s+FROM\s+\S+\s*;', 'DELETE FROM without WHERE clause'),
    (r'\bALTER\s+TABLE\s+\S+\s+DROP\b', 'ALTER TABLE DROP'),
]

# System/process disruption patterns
SYSTEM_CMD_PATTERNS = [
    (r'\bkill\b', 'kill'),
    (r'\bkillall\b', 'killall'),
    (r'\bshutdown\b', 'shutdown'),
    (r'\breboot\b', 'reboot'),
    (r'\binit\s+[06]\b', 'init (shutdown/reboot)'),
    (r'\bcrontab\b', 'crontab'),
    (r'\bssh\b', 'ssh'),
    (r'\bscp\b', 'scp'),
    (r'\brsync\b', 'rsync'),
    (r'\bnc\b', 'netcat'),
    (r'\bncat\b', 'ncat'),
]


# =============================================================================
# Hook: Bash Safety Guardrails (PreToolUse)
# =============================================================================

def _deny_with_message(reason: str, guidance: str) -> SyncHookJSONOutput:
    """Helper to build a deny response with system message injection."""
    return {
        "systemMessage": f"[SECURITY] {guidance}",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def create_bash_security_hook(config: SecurityConfig):
    """
    Create a PreToolUse hook that blocks dangerous Bash commands.

    Security layers:
    1. Package installation blocking
    2. Destructive filesystem commands
    3. Destructive SQL/DB commands
    4. System/process disruption commands
    5. Path sandbox enforcement

    The hook injects a system message when blocking so the agent
    understands why the command was rejected and can adjust.
    """

    async def bash_security_hook(
        input_data: PreToolUseHookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        command = input_data["tool_input"].get("command", "")
        cwd = input_data.get("cwd", config.workspace_dir)

        # Strip heredoc content before pattern matching — embedded Python
        # scripts contain words like 'kill', 'rm', 'ssh' as variable names
        # or comments that trigger false positives in Layers 1-4.
        command_shell = _strip_heredocs(command)

        # Layer 1: Block package installation
        if config.block_pkg_install:
            for pattern, name in PKG_INSTALL_PATTERNS:
                if re.search(pattern, command_shell, re.IGNORECASE):
                    print(f"[HOOK:SECURITY] BLOCKED package install: {name} | cmd: {command[:200]}")
                    return _deny_with_message(
                        f"Package installation is prohibited: '{name}'",
                        f"Package installation ({name}) is not allowed. "
                        f"All required packages are pre-installed in the environment.",
                    )

        # Layer 2: Block destructive filesystem commands
        if config.block_destructive_fs:
            for pattern, name in DESTRUCTIVE_FS_PATTERNS:
                if re.search(pattern, command_shell, re.IGNORECASE):
                    print(f"[HOOK:SECURITY] BLOCKED destructive FS: {name} | cmd: {command[:200]}")
                    return _deny_with_message(
                        f"Command blocked for security: '{name}'",
                        f"Destructive filesystem command ({name}) is not allowed. "
                        f"Use Write/Edit tools for file operations within your workspace.",
                    )

        # Layer 3: Block destructive SQL/DB commands
        if config.block_destructive_db:
            for pattern, name in DESTRUCTIVE_DB_PATTERNS:
                if re.search(pattern, command_shell, re.IGNORECASE):
                    print(f"[HOOK:SECURITY] BLOCKED destructive DB: {name} | cmd: {command[:200]}")
                    return _deny_with_message(
                        f"Destructive database command blocked: '{name}'",
                        f"Destructive database operation ({name}) is not allowed. "
                        f"Only read-only database queries are permitted.",
                    )

        # Layer 4: Block system/process disruption commands
        if config.block_system_commands:
            for pattern, name in SYSTEM_CMD_PATTERNS:
                if re.search(pattern, command_shell, re.IGNORECASE):
                    print(f"[HOOK:SECURITY] BLOCKED system cmd: {name} | cmd: {command[:200]}")
                    return _deny_with_message(
                        f"System command blocked: '{name}'",
                        f"System command ({name}) is not allowed in this environment.",
                    )

        # Layer 5: Path sandbox enforcement
        # Use read_allowed (workspace + app source) since Bash commands mostly
        # read/navigate; actual file writes are caught by Layer 2 (destructive FS).
        if config.enforce_path_sandbox:
            paths_in_cmd = _extract_paths_from_command(command, cwd)
            for path in paths_in_cmd:
                if not _is_path_within(path, config.read_allowed, config.blocked_read_dirs):
                    print(f"[HOOK:SECURITY] BLOCKED path: {path} | cmd: {command[:200]}")
                    return _deny_with_message(
                        f"Command references path outside allowed directories: {path}",
                        f"This command references a path outside your workspace and app source ({path}). "
                        f"Use Read/Glob/Grep tools to access other files.",
                    )

        # All checks passed — allow
        return {}

    return bash_security_hook


def create_file_write_security_hook(config: SecurityConfig):
    """
    Create a PreToolUse hook that restricts Write/Edit to the session workspace.
    """

    async def file_write_security_hook(
        input_data: PreToolUseHookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        file_path = (
            input_data["tool_input"].get("file_path", "")
            or input_data["tool_input"].get("notebook_path", "")
        )
        if file_path and not _is_path_within(file_path, config.write_allowed):
            print(f"[HOOK:SECURITY] BLOCKED write outside workspace: {file_path}")
            return _deny_with_message(
                f"File writes restricted to session workspace ({config.workspace_dir}). "
                f"Cannot write to: {file_path}",
                f"Write operation blocked — file is outside your workspace. "
                f"You can only write files within {config.workspace_dir}.",
            )
        return {}

    return file_write_security_hook


def create_file_read_security_hook(config: SecurityConfig):
    """
    Create a PreToolUse hook that restricts Read/Glob/Grep to workspace + app source.
    """

    async def file_read_security_hook(
        input_data: PreToolUseHookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        file_path = (
            input_data["tool_input"].get("file_path", "")
            or input_data["tool_input"].get("path", "")
        )
        if file_path and not _is_path_within(file_path, config.read_allowed, config.blocked_read_dirs):
            print(f"[HOOK:SECURITY] BLOCKED read outside allowed dirs: {file_path}")
            return _deny_with_message(
                f"File reads restricted to workspace and app source. "
                f"Cannot access: {file_path}",
                f"Read operation blocked — file is outside allowed directories. "
                f"You can read files in your workspace and the app source directory.",
            )
        return {}

    return file_read_security_hook


# =============================================================================
# Hook: MCP Tools Auto-Approve (PreToolUse)
# =============================================================================

async def _auto_approve_mcp_tools(
    input_data: PreToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> SyncHookJSONOutput:
    """
    Auto-approve all MCP tool calls.

    When hooks are present, tools that don't match any hook matcher may fall
    through to the SDK's "Default to Ask" permission state, which in headless
    mode (web app) effectively makes them unavailable. This hook explicitly
    approves MCP tools so they remain accessible to specialist subagents.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "MCP tool auto-approved by security hooks",
        }
    }


# =============================================================================
# Hook builder
# =============================================================================

def build_security_hooks(config: SecurityConfig) -> dict:
    """
    Build the complete hooks dictionary for ClaudeAgentOptions.

    Returns a dict suitable for ClaudeAgentOptions(hooks=...).
    """
    from claude_agent_sdk import HookMatcher

    return {
        "PreToolUse": [
            # Auto-approve MCP tool calls (must match so they don't fall
            # through to "Default to Ask" in headless mode)
            HookMatcher(
                matcher="^mcp__",
                hooks=[_auto_approve_mcp_tools],
            ),
            # File write sandbox (Write, Edit, NotebookEdit)
            HookMatcher(
                matcher="Write|Edit|NotebookEdit",
                hooks=[create_file_write_security_hook(config)],
            ),
            # File read sandbox (Read, Glob, Grep)
            HookMatcher(
                matcher="Read|Glob|Grep",
                hooks=[create_file_read_security_hook(config)],
            ),
            # Bash safety guardrails
            HookMatcher(
                matcher="Bash",
                hooks=[create_bash_security_hook(config)],
            ),
        ],
    }


def build_security_callback(config: SecurityConfig):
    """
    Build a can_use_tool callback for subagent tool permission control.

    SDK hooks (PreToolUse etc.) do NOT propagate to subagents. The
    can_use_tool callback *does* apply to subagent tool calls, so we
    use it alongside hooks to ensure MCP tools and sandboxed file ops
    work correctly for specialist subagents in headless (web app) mode.

    Returns a callable suitable for ClaudeAgentOptions(can_use_tool=...).
    """

    async def security_callback(
        tool_name: str,
        input_data: dict,
        context: dict,
    ):
        cwd = context.get("cwd", config.workspace_dir)
        session_workspace = str(Path(cwd).resolve())

        write_allowed = [session_workspace]
        read_allowed = list(config.read_allowed)  # workspace + app source + extras
        blocked = config.blocked_read_dirs

        # ── Write/Edit tools: workspace only ──────────────────────────────
        if tool_name in ("Write", "Edit", "NotebookEdit"):
            file_path = (
                input_data.get("file_path", "") or input_data.get("notebook_path", "")
            )
            if file_path and not _is_path_within(file_path, write_allowed):
                print(f"[CALLBACK:SECURITY] BLOCKED {tool_name} outside workspace: {file_path}")
                return {
                    "behavior": "deny",
                    "message": (
                        f"File writes are restricted to your session workspace "
                        f"({session_workspace}). Cannot write to: {file_path}"
                    ),
                }
            return {"behavior": "allow", "updatedInput": input_data}

        # ── Read tools: workspace + app source ────────────────────────────
        if tool_name in ("Read", "Glob", "Grep"):
            file_path = input_data.get("file_path", "") or input_data.get("path", "")
            if file_path and not _is_path_within(file_path, read_allowed, blocked):
                print(f"[CALLBACK:SECURITY] BLOCKED {tool_name} outside allowed dirs: {file_path}")
                return {
                    "behavior": "deny",
                    "message": (
                        f"File reads are restricted to your workspace and app source. "
                        f"Cannot access: {file_path}"
                    ),
                }
            return {"behavior": "allow", "updatedInput": input_data}

        # ── Bash: multi-layered security checks ──────────────────────────
        if tool_name == "Bash":
            command = input_data.get("command", "")
            command_shell = _strip_heredocs(command)

            if config.block_pkg_install:
                for pattern, name in PKG_INSTALL_PATTERNS:
                    if re.search(pattern, command_shell, re.IGNORECASE):
                        print(f"[CALLBACK:SECURITY] BLOCKED package install: {name} | cmd: {command[:200]}")
                        return {
                            "behavior": "deny",
                            "message": f"Package installation is prohibited: '{name}'",
                        }

            if config.block_destructive_fs:
                for pattern, name in DESTRUCTIVE_FS_PATTERNS:
                    if re.search(pattern, command_shell, re.IGNORECASE):
                        print(f"[CALLBACK:SECURITY] BLOCKED destructive FS: {name} | cmd: {command[:200]}")
                        return {
                            "behavior": "deny",
                            "message": (
                                f"Command blocked for security: '{name}'. "
                                f"Use Write/Edit tools for file operations within your workspace."
                            ),
                        }

            if config.block_destructive_db:
                for pattern, name in DESTRUCTIVE_DB_PATTERNS:
                    if re.search(pattern, command_shell, re.IGNORECASE):
                        print(f"[CALLBACK:SECURITY] BLOCKED destructive DB: {name} | cmd: {command[:200]}")
                        return {
                            "behavior": "deny",
                            "message": f"Destructive database command blocked: '{name}'",
                        }

            if config.block_system_commands:
                for pattern, name in SYSTEM_CMD_PATTERNS:
                    if re.search(pattern, command_shell, re.IGNORECASE):
                        print(f"[CALLBACK:SECURITY] BLOCKED system cmd: {name} | cmd: {command[:200]}")
                        return {
                            "behavior": "deny",
                            "message": f"System command blocked: '{name}'",
                        }

            if config.enforce_path_sandbox:
                paths_in_cmd = _extract_paths_from_command(command, cwd)
                for path in paths_in_cmd:
                    if not _is_path_within(path, read_allowed, blocked):
                        print(f"[CALLBACK:SECURITY] BLOCKED path: {path} | cmd: {command[:200]}")
                        return {
                            "behavior": "deny",
                            "message": (
                                f"Command references path outside allowed directories: {path}. "
                                f"Use Read/Glob/Grep tools to access other files."
                            ),
                        }

            return {"behavior": "allow", "updatedInput": input_data}

        # ── All other tools (MCP, Task, TodoWrite, Skill, WebSearch) ─────
        return {"behavior": "allow", "updatedInput": input_data}

    return security_callback
