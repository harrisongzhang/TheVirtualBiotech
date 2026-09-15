#!/usr/bin/env python3
"""Check an installed checkout without calling a model API."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.config.datasets import OPEN_TARGETS_DATASETS
from tools.download_open_targets import BASE, RELEASE, relative_path

MCP_SERVERS = (
    "expression", "functional_genomics", "genetics", "target", "drug",
    "single_cell", "association", "disease", "interaction", "pathway",
    "clinicaltrials", "provenance",
)


def reference_files(value: str | None) -> dict[str, list[Path]]:
    if not value or not value.strip():
        raise ValueError("OPEN_TARGETS_DATA_PATH is not set; configure it in .env or export it")
    root = Path(value)
    if not root.is_dir():
        raise ValueError(f"OPEN_TARGETS_DATA_PATH is not a directory: {root}")
    files = {}
    for name in OPEN_TARGETS_DATASETS:
        paths = sorted((root / name).rglob("*.parquet"))
        if not paths:
            raise ValueError(f"Missing dataset or Parquet files: {root / name}; rerun the data downloader")
        for path in paths:
            if not path.is_file() or not os.access(path, os.R_OK) or path.stat().st_size < 12:
                raise ValueError(f"Unreadable or truncated Parquet file: {path}")
        files[name] = paths
    manifest_path = root / ".download-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (not isinstance(manifest, dict) or manifest.get("release") != RELEASE
                or manifest.get("base") != BASE or manifest.get("complete") is not True):
            raise ValueError("Download manifest is incomplete or for another release; rerun the data downloader")
        entries = manifest.get("files")
        if (not isinstance(entries, dict) or not entries
                or manifest.get("expected_files") != len(entries)):
            raise ValueError("Download manifest has an invalid file inventory; rerun the data downloader")
        actual = {p.relative_to(root).as_posix(): p for paths in files.values() for p in paths}
        if set(entries) != set(actual):
            raise ValueError("Downloaded files do not match the manifest inventory; rerun the data downloader")
        for name, entry in entries.items():
            relative_path(name)
            if not isinstance(entry, dict) or actual[name].stat().st_size != entry.get("bytes"):
                raise ValueError(f"File size differs from the download manifest: {name}; rerun the downloader")
    if next(root.rglob("*.part"), None) is not None:
        raise ValueError("Partial downloads remain; rerun the data downloader")
    return files


def mcp_configuration(repo: Path) -> dict:
    path = repo / "mcp_config.json"
    try:
        servers = json.loads(path.read_text())["mcpServers"]
        for name in MCP_SERVERS:
            config = servers[name]
            command = Path(config["command"])
            scripts = [Path(arg) for arg in config["args"] if arg.endswith(".py")]
            if not command.is_absolute() or not command.is_file() or not os.access(command, os.X_OK):
                raise ValueError(f"{name}: interpreter is missing or not executable")
            if not scripts or not all(p.is_absolute() and p.is_file() for p in scripts):
                raise ValueError(f"{name}: server script is missing")
        return {name: servers[name] for name in MCP_SERVERS}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid or missing MCP configuration; run: python setup_mcp.py") from exc


def read_sample(files: dict[str, list[Path]]) -> None:
    import pyarrow.parquet as pq
    for path in files["disease"]:
        with pq.ParquetFile(path) as parquet:
            if parquet.metadata.num_rows:
                batch = next(parquet.iter_batches(batch_size=1, columns=["id", "name"]))
                if batch.num_columns != 2 or batch.num_rows != 1:
                    raise ValueError("Disease data is missing the expected id/name columns")
                print("  [ok]   Read one disease record from local Parquet data")
                return
    raise ValueError("Disease dataset contains no records")


async def smoke_mcp(servers: dict, repo: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def connect(name, config):
        params = StdioServerParameters(
            command=config["command"], args=config["args"], cwd=str(repo),
            env={**os.environ, **config.get("env", {})},
        )
        # Server startup output can be noisy. The diagnostic names any failed
        # server without printing configuration values or credentials.
        with tempfile.TemporaryFile(mode="w+") as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    if not tools.tools:
                        raise ValueError("server advertised no tools")
                    print(f"  [ok]   MCP {name}: {len(tools.tools)} tools", flush=True)

    for name, config in servers.items():
        try:
            await asyncio.wait_for(connect(name, config), timeout=30)
        except Exception as exc:
            raise ValueError(f"MCP {name} did not initialize successfully ({type(exc).__name__})") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-api-key", action="store_true", help="check local setup before adding a model key")
    parser.add_argument("--smoke", action="store_true", help="also read a local data record and initialize all 12 MCP servers")
    args = parser.parse_args(argv)
    print("The Virtual Biotech — environment check")
    print(f"repo:        {REPO}")
    print(f"python:      {sys.executable} ({sys.version.split()[0]})")
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env", override=False)
    except ImportError:
        print("  [FAIL] python-dotenv missing; activate the environment from environment.yml")
        return 1
    failed = False

    def check(label, action):
        nonlocal failed
        try:
            result = action()
            print(f"  [ok]   {label}")
            return result
        except Exception as exc:
            failed = True
            print(f"  [FAIL] {label}: {exc}")

    for module in ("gradio", "claude_agent_sdk", "fastmcp", "pandas", "pyarrow"):
        check(module, lambda module=module: importlib.import_module(module))
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or
                      str(Path(os.environ.get("SCRATCH") or Path.home()) / "claude-config"))

    def writable():
        config_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=config_dir):
            pass
    check(f"CLAUDE_CONFIG_DIR writable ({config_dir})", writable)
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("  [ok]   ANTHROPIC_API_KEY configured (model access not tested)")
    elif args.skip_api_key:
        print("  [skip] ANTHROPIC_API_KEY; configure it before live research")
    else:
        print("  [FAIL] no non-empty ANTHROPIC_API_KEY in environment or .env")
        failed = True
    files = check("Open Targets reference data", lambda: reference_files(os.environ.get("OPEN_TARGETS_DATA_PATH")))
    if files:
        print(f"         {len(files)} datasets, {sum(map(len, files.values()))} Parquet files")
        print("         File layout/sizes checked; use the downloader to recheck SHA-256 hashes.")
    servers = check("mcp_config.json — 12 servers", lambda: mcp_configuration(REPO))
    if args.smoke and files and servers and not failed:
        check("Local data/MCP smoke check", lambda: (read_sample(files), asyncio.run(smoke_mcp(servers, REPO))))
    if failed:
        print("FAIL — fix the items above. Audit tooling remains usable without model credentials.")
        return 1
    print("PASS — local setup checks passed. Model authentication and research execution were not tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
