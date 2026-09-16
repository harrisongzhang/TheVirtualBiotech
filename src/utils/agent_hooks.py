"""
Agent Hooks for The Virtual Biotech

Provides SDK-native hooks (PreToolUse, PostToolUse, SubagentStop, Stop)
for security, auditing, cost tracking, and lifecycle management.

The hooks and can_use_tool callback share permission checks so parent and
specialist calls follow the same file-access rules.

Usage:
    from src.utils.agent_hooks import build_security_hooks, SecurityConfig

    config = SecurityConfig(workspace_dir="/path/to/workspace")
    hooks = build_security_hooks(config)

    options = ClaudeAgentOptions(
        hooks=hooks,
        ...
    )
"""

import os
import re
import shlex
from pathlib import Path

from claude_agent_sdk.types import (
    PreToolUseHookInput,
    HookContext,
    SyncHookJSONOutput,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from src.utils.runtime_paths import RuntimePaths


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
        runtime_paths: RuntimePaths | None = None,
    ):
        self.workspace_dir = str(Path(workspace_dir).resolve())
        self.app_source_dir = str(Path(app_source_dir).resolve()) if app_source_dir else None
        self.block_pkg_install = block_pkg_install
        self.block_destructive_fs = block_destructive_fs
        self.block_destructive_db = block_destructive_db
        self.block_system_commands = block_system_commands
        self.enforce_path_sandbox = enforce_path_sandbox
        self.runtime_paths = runtime_paths

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

    def resolve_path(self, path: str, cwd: str | None = None) -> Path:
        """Keep relative tool paths relative to the session, not the app process."""
        candidate = Path(path).expanduser()
        return candidate if candidate.is_absolute() else Path(cwd or self.workspace_dir) / candidate

    def is_runtime_path(self, path: Path) -> bool:
        if not self.runtime_paths:
            return False
        return self.runtime_paths.covers(path.absolute()) or self.runtime_paths.covers(path.resolve())

    def allows_read(self, path: str, cwd: str | None = None) -> bool:
        try:
            candidate = self.resolve_path(path, cwd)
            # Explicit blocks also apply when a runtime directory was configured
            # inside a broader normally-readable root.
            if _is_path_within(str(candidate), self.blocked_read_dirs):
                return False
            if self.is_runtime_path(candidate):
                return self.runtime_paths.allows_read(candidate)
            return _is_path_within(str(candidate), self.read_allowed, self.blocked_read_dirs)
        except (OSError, ValueError, RuntimeError):
            return False

    def allows_write(self, path: str, cwd: str | None = None) -> bool:
        try:
            candidate = self.resolve_path(path, cwd)
            return not self.is_runtime_path(candidate) and _is_path_within(
                str(candidate), self.write_allowed, self.blocked_read_dirs,
            )
        except (OSError, ValueError, RuntimeError):
            return False


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
    except (ValueError, OSError, RuntimeError):
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
            except (ValueError, OSError, RuntimeError):
                paths.append(str(p))
    return paths


def _bash_path_denial(command: str, cwd: str, config: SecurityConfig) -> str | None:
    """Apply file permissions to shell paths, including output redirections.

    Reading a spill file never confers write access to runtime storage. Shell
    programs with arbitrary code execution cannot be classified as read-only;
    use the file tools for those inputs or copy their text into the workspace.
    """
    paths = _extract_paths_from_command(command, cwd)
    for path in paths:
        if not config.allows_read(path, cwd):
            return f"Command references path outside allowed directories: {path}"

    try:
        lexer = shlex.shlex(_strip_heredocs(command), posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return "Cannot safely parse shell command; use Read/Write tools for file operations."

    for index, token in enumerate(tokens):
        if token in (">", ">>", "&>", "&>>", ">|", "<>"):
            target = tokens[index + 1] if index + 1 < len(tokens) else ""
            if target == "/dev/null":
                continue
            if not target or not config.allows_write(target, cwd):
                return f"Shell output is restricted to the session workspace: {target}"

    runtime = config.runtime_paths
    uses_runtime = runtime and (
        str(runtime.config_dir) in command
        or str(runtime.temp_dir) in command
        or any(config.is_runtime_path(Path(path)) for path in paths)
        or config.is_runtime_path(Path(cwd))
    )
    if uses_runtime:
        # These commands read their file arguments and write only to stdout.
        # Avoid interpreters, sed -i/e/w, rg --pre, or sort -o being used to
        # write into the newly readable runtime directories.
        readers = {"cat", "head", "tail", "wc", "grep", "cut"}
        if any(marker in command for marker in ("$", "`", "\n", "<(", ">(")) or any(
            token in ("<<", "<<<", "&", "|&", "(", ")") for token in tokens
        ):
            return "Runtime output permits simple read commands only; use Read/Glob/Grep for these files."
        expect_command = True
        skip_target = False
        reader = None
        for token in tokens:
            if skip_target:
                skip_target = False
                continue
            if token in (">", ">>", "<", "&>", "&>>", ">|", "<>"):
                skip_target = True
                continue
            if token in ("|", "||", "&&", ";"):
                expect_command = True
                continue
            if expect_command:
                if token not in readers:
                    return "Runtime output permits simple read commands only; use Read/Glob/Grep for these files."
                reader = token
                expect_command = False
            elif reader == "grep" and (
                token.startswith(("--file", "--exclude-from")) or re.match(r"^-[^-]*f", token)
            ):
                return "Use Grep for runtime output searches requiring additional pattern files."
    return None


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

        # Layer 5: File paths and shell output must respect their respective
        # read/write permissions. Runtime output is a narrow read-only grant.
        if config.enforce_path_sandbox:
            denial = _bash_path_denial(command, cwd, config)
            if denial:
                print(f"[HOOK:SECURITY] BLOCKED path operation: {denial}")
                return _deny_with_message(denial, denial)

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
        if file_path and not config.allows_write(file_path, input_data.get("cwd")):
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
        cwd = input_data.get("cwd", config.workspace_dir)
        file_path = (
            input_data["tool_input"].get("file_path", "")
            or input_data["tool_input"].get("path", "")
            or cwd
        )
        paths = [file_path]
        if input_data.get("tool_name") == "Glob":
            pattern = input_data["tool_input"].get("pattern", "")
            if ".." in Path(pattern).parts:
                return _deny_with_message(
                    "Glob patterns cannot traverse parent directories.",
                    "Set path to an allowed directory and use a pattern within that directory.",
                )
            # Glob can override its path with an absolute pattern. Check the
            # literal prefix before wildcards as well as the search directory.
            prefix = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
            if prefix:
                search_dir = str(config.resolve_path(file_path, cwd))
                paths.append(str(config.resolve_path(prefix, search_dir)))
        denied = next((path for path in paths if not config.allows_read(path, cwd)), None)
        if denied is not None:
            file_path = denied
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
    """Apply the same checks when the runtime asks for a tool permission.

    The pinned client passes a ToolPermissionContext instance and requires
    typed permission results. Sharing checks with the hooks prevents the two
    enforcement layers from disagreeing on runtime spill-file access.
    """
    bash_hook = create_bash_security_hook(config)
    read_hook = create_file_read_security_hook(config)
    write_hook = create_file_write_security_hook(config)

    async def security_callback(
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,
    ):
        # Older test adapters may supply a mapping, but the runtime context has
        # no cwd field. Neither form can widen the configured write workspace.
        cwd = (
            context.get("cwd", config.workspace_dir)
            if isinstance(context, dict) else config.workspace_dir
        )
        hook_input = {
            "tool_name": tool_name,
            "tool_input": input_data,
            "cwd": cwd,
        }
        hook = None
        if tool_name in ("Write", "Edit", "NotebookEdit"):
            hook = write_hook
        elif tool_name in ("Read", "Glob", "Grep"):
            hook = read_hook
        elif tool_name == "Bash":
            hook = bash_hook
        if hook is not None:
            result = await hook(hook_input, getattr(context, "tool_use_id", None), {})
            decision = result.get("hookSpecificOutput", {})
            if decision.get("permissionDecision") == "deny":
                return PermissionResultDeny(
                    message=decision.get("permissionDecisionReason", "Access denied"),
                )
        return PermissionResultAllow(updated_input=input_data)

    return security_callback
