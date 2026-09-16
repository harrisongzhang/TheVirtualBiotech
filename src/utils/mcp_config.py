"""Resolve portable MCP configuration before spawning run-specific children."""

import os
from pathlib import Path
import sys


def resolve_mcp_servers(servers: dict, repo_root: str | Path) -> dict:
    root = Path(repo_root).resolve()
    resolved = {}
    for name, original in servers.items():
        config = dict(original)
        if config.get("command") in ("python", "python3"):
            config["command"] = sys.executable
            config["args"] = [
                str(root / arg) if not arg.startswith("-") and not Path(arg).is_absolute() else arg
                for arg in config.get("args", [])
            ]
        for field in ("env", "headers"):
            if field in config:
                config[field] = {
                    key: os.path.expandvars(value) if isinstance(value, str) else value
                    for key, value in config[field].items()
                }
        resolved[name] = config
    return resolved
