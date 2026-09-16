"""Locate runtime output files without exposing runtime credentials or other runs."""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from pathlib import Path
from uuid import UUID, uuid4


class RuntimePaths:
    """The file locations and environment for one runtime conversation.

    Give ``session_id`` and ``sdk_env`` to the client options. The explicit UUID
    makes spill-file permissions available before the first tool call, including
    calls from specialists. Config/auth stays in its existing location; only
    this conversation's persisted tool results and task outputs are readable.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        *,
        env: dict[str, str] | None = None,
        session_id: str | None = None,
        temp_parent: str | Path | None = None,
    ):
        environment = dict(os.environ if env is None else env)
        self.workspace_dir = Path(workspace_dir).resolve()
        self.session_id = str(UUID(session_id)) if session_id else str(uuid4())
        configured = environment.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
        self.config_dir = Path(unicodedata.normalize("NFC", configured)).expanduser().resolve()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="vbt-runtime-", dir=temp_parent)).resolve()
        self.sdk_env = {
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            "CLAUDE_CODE_TMPDIR": str(self.temp_dir),
        }
        # The pinned runtime replaces non-ASCII-alphanumeric characters and
        # hashes names longer than 200 characters. Match the long-name prefix
        # as the SDK does, while still requiring our exact conversation UUID.
        cwd = unicodedata.normalize("NFC", str(self.workspace_dir))
        self._project_name = re.sub(r"[^a-zA-Z0-9]", "-", cwd)
        self._temp_user = "claude" if os.name == "nt" else f"claude-{os.getuid()}"

    def covers(self, path: Path) -> bool:
        """Whether a lexical or resolved path enters protected runtime storage."""
        return any(path.is_relative_to(root) for root in (self.config_dir, self.temp_dir))

    def _project_matches(self, name: str) -> bool:
        if len(self._project_name) <= 200:
            return name == self._project_name
        prefix = self._project_name[:200] + "-"
        return name.startswith(prefix) and bool(re.fullmatch(r"[a-z0-9]+", name[len(prefix):]))

    def _is_output_path(self, path: Path) -> bool:
        roots = (
            (self.config_dir / "projects", "tool-results"),
            (self.temp_dir / self._temp_user, "tasks"),
        )
        for root, output_dir in roots:
            try:
                parts = path.relative_to(root).parts
            except ValueError:
                continue
            if (
                len(parts) >= 3
                and self._project_matches(parts[0])
                and parts[1] == self.session_id
                and parts[2] == output_dir
                and not any(part.startswith(".") for part in parts[3:])
            ):
                return True
        return False

    def allows_read(self, path: Path) -> bool:
        """Check both the supplied path and its symlink-resolved destination."""
        try:
            if ".." in path.parts:
                return False
            return self._is_output_path(path.absolute()) and self._is_output_path(path.resolve())
        except (OSError, ValueError, RuntimeError):
            return False
