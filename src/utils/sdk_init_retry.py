"""
SDK Initialization Retry Handler (v2 - Fool-Proof Edition)

Addresses the cold-start timeout issue where MCP servers take >60s to initialize
on first run but succeed on subsequent runs due to OS/npm caching.

Key improvements in v2:
1. TRUE MCP verification - actually sends initialize request and verifies response
2. SDK timeout patching - dynamically increases timeout from 60s to 180s
3. Better diagnostics - detailed timing and failure information
4. Parallel warm-up with individual server timing

The SDK has a hardcoded 60-second timeout for initialization. This wrapper:
1. Pre-warms MCP servers with TRUE initialization (not just spawn/terminate)
2. Patches the SDK timeout to 180s for safety margin
3. Catches timeout exceptions and retries with exponential backoff
4. Provides clear logging so users understand what's happening

Usage:
    from src.utils.sdk_init_retry import initialize_with_retry, RobustClaudeSDKClient

    # Option 1: Use the retry wrapper function
    client = await initialize_with_retry(options, max_retries=3)

    # Option 2: Use the robust client class
    async with RobustClaudeSDKClient(options) as client:
        await client.query("Hello")
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass

# Pacific Standard Time (UTC-8) / Pacific Daylight Time (UTC-7)
_PST = timezone(timedelta(hours=-8), 'PST')
_PDT = timezone(timedelta(hours=-7), 'PDT')

def _now_pacific() -> str:
    """Return current time in US/Pacific as a formatted string."""
    utc_now = datetime.now(timezone.utc)
    # PST: Nov-Mar, PDT: Mar-Nov (approximate; exact cutover varies)
    month = utc_now.month
    is_dst = 3 < month < 11 or (month == 3 and utc_now.day >= 10) or (month == 11 and utc_now.day < 3)
    pacific = _PDT if is_dst else _PST
    return utc_now.astimezone(pacific).strftime('%Y-%m-%d %I:%M:%S %p %Z')

logger = logging.getLogger(__name__)

# Increased timeout for SDK (default is 60s, we want 180s)
SDK_TIMEOUT_SECONDS = 180.0


@dataclass
class ServerWarmupResult:
    """Result of warming a single MCP server."""
    name: str
    success: bool
    duration_seconds: float
    error: Optional[str] = None


@dataclass
class InitializationResult:
    """Result of SDK initialization attempt."""
    success: bool
    attempt: int
    duration_seconds: float
    error: Optional[str] = None


def patch_sdk_timeout(timeout_seconds: float = SDK_TIMEOUT_SECONDS) -> bool:
    """
    Dynamically patch the Claude Agent SDK's hardcoded 60s timeout.

    The SDK has a hardcoded timeout in query.py:
        with anyio.fail_after(60.0):

    This function patches it to use a longer timeout.

    Returns:
        True if patch was successful, False otherwise
    """
    try:
        import claude_agent_sdk._internal.query as query_module
        import anyio

        # Store original fail_after for potential restoration
        original_fail_after = anyio.fail_after

        def patched_fail_after(delay, *args, **kwargs):
            # If the delay is exactly 60.0 (the hardcoded value), increase it
            if delay == 60.0:
                logger.info(f"[SDK Patch] Intercepted 60s timeout, increasing to {timeout_seconds}s")
                delay = timeout_seconds
            return original_fail_after(delay, *args, **kwargs)

        # Patch anyio.fail_after in the query module's namespace
        query_module.anyio.fail_after = patched_fail_after

        logger.info(f"[SDK Patch] Successfully patched SDK timeout to {timeout_seconds}s")
        print(f"[SDK timeout patched: 60s → {timeout_seconds}s]")
        return True

    except Exception as e:
        logger.warning(f"[SDK Patch] Failed to patch timeout: {e}")
        print(f"[WARNING] Could not patch SDK timeout: {e}")
        return False


class MCPServerWarmer:
    """
    Pre-warms MCP servers with TRUE initialization verification.

    Unlike the previous version that just started/terminated processes,
    this version actually sends MCP initialize requests and verifies responses.
    This ensures the server is fully ready and all dependencies are loaded.
    """

    def __init__(self, mcp_config: dict[str, Any], timeout_per_server: float = 120.0):
        """
        Args:
            mcp_config: MCP server configuration dict
            timeout_per_server: Seconds to allow each server to initialize
        """
        self.mcp_config = mcp_config
        self.timeout_per_server = timeout_per_server
        self.results: list[ServerWarmupResult] = []

    async def warm_servers(self, verbose: bool = True) -> bool:
        """
        Pre-warm MCP servers by starting them and sending initialize request.
        This truly verifies each server is ready to accept commands.

        Returns:
            True if all servers warmed successfully
        """
        if not self.mcp_config:
            return True

        server_count = len(self.mcp_config)
        if verbose:
            print(f"[Pre-warming {server_count} MCP servers with TRUE initialization...]")

        # Start all servers in parallel
        start_time = time.time()
        tasks = []
        for name, config in self.mcp_config.items():
            tasks.append(self._warm_single_server(name, config, verbose))

        self.results = await asyncio.gather(*tasks)

        total_duration = time.time() - start_time
        success_count = sum(1 for r in self.results if r.success)

        if verbose:
            print(f"[Warmed {success_count}/{server_count} servers in {total_duration:.1f}s (parallel)]")

            # Report any failures
            failures = [r for r in self.results if not r.success]
            if failures:
                print("[WARNING] Failed servers:")
                for f in failures:
                    print(f"  - {f.name}: {f.error}")

        return success_count == server_count

    async def _warm_single_server(self, name: str, config: dict, verbose: bool) -> ServerWarmupResult:
        """
        Warm a single MCP server by:
        1. Starting the process
        2. Sending MCP initialize request
        3. Waiting for valid response
        4. Terminating the process

        This ensures the server is fully functional, not just started.
        """
        start_time = time.time()
        proc = None

        try:
            command = config.get('command')
            args = config.get('args', [])
            env = config.get('env', {})

            if not command:
                return ServerWarmupResult(name, True, 0.0)

            # Spawn the server process
            full_env = {
                **dict(os.environ),
                **env,
                "FASTMCP_SHOW_CLI_BANNER": "false"  # Suppress FastMCP banners
            }

            proc = await asyncio.create_subprocess_exec(
                command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env
            )

            # Send MCP initialize request (proper JSON-RPC)
            init_request = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "warmup", "version": "1.0"}
                }
            }) + "\n"

            proc.stdin.write(init_request.encode())
            await proc.stdin.drain()

            # Wait for response with timeout
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=self.timeout_per_server
                )

                duration = time.time() - start_time

                if line:
                    response = json.loads(line.decode())
                    if "result" in response:
                        if verbose:
                            print(f"  [OK] {name} ({duration:.1f}s)")
                        return ServerWarmupResult(name, True, duration)
                    elif "error" in response:
                        error_msg = response.get("error", {}).get("message", "Unknown error")
                        if verbose:
                            print(f"  [WARN] {name}: {error_msg} ({duration:.1f}s)")
                        return ServerWarmupResult(name, False, duration, error_msg)

                if verbose:
                    print(f"  [WARN] {name}: No response ({duration:.1f}s)")
                return ServerWarmupResult(name, False, duration, "No response")

            except asyncio.TimeoutError:
                duration = time.time() - start_time
                if verbose:
                    print(f"  [TIMEOUT] {name} ({duration:.1f}s)")
                return ServerWarmupResult(name, False, duration, f"Timeout after {self.timeout_per_server}s")

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            if verbose:
                print(f"  [ERROR] {name}: {error_msg} ({duration:.1f}s)")
            return ServerWarmupResult(name, False, duration, error_msg)

        finally:
            # Always clean up the process
            if proc:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except:
                    try:
                        proc.kill()
                    except:
                        pass


async def initialize_with_retry(
    options: Any,
    max_retries: int = 5,
    initial_delay: float = 3.0,
    backoff_factor: float = 1.5,
    pre_warm: bool = True,
    patch_timeout: bool = True,
    verbose: bool = True
) -> Any:
    """
    Initialize ClaudeSDKClient with retry logic for cold-start timeout.

    Args:
        options: ClaudeAgentOptions instance
        max_retries: Maximum number of initialization attempts
        initial_delay: Delay before first retry (seconds)
        backoff_factor: Multiply delay by this after each retry
        pre_warm: Whether to pre-warm MCP servers before first attempt
        patch_timeout: Whether to patch SDK timeout from 60s to 180s
        verbose: Print progress messages

    Returns:
        Connected ClaudeSDKClient instance

    Raises:
        Exception: If all retries fail
    """
    from claude_agent_sdk import ClaudeSDKClient

    # Patch SDK timeout first (only once)
    if patch_timeout:
        patch_sdk_timeout(SDK_TIMEOUT_SECONDS)

    # Pre-warm MCP servers with TRUE initialization
    if pre_warm and hasattr(options, 'mcp_servers') and options.mcp_servers:
        mcp_config = options.mcp_servers
        if isinstance(mcp_config, dict):
            warmer = MCPServerWarmer(mcp_config, timeout_per_server=120.0)
            warmup_success = await warmer.warm_servers(verbose=verbose)

            if not warmup_success and verbose:
                print("[NOTE] Some servers failed warmup, but proceeding with initialization...")

    last_error = None
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        start_time = time.time()

        if verbose:
            print(f"[Initialization attempt {attempt}/{max_retries}...]")

        try:
            client = ClaudeSDKClient(options=options)
            await client.connect()

            duration = time.time() - start_time
            if verbose:
                print(f"[Connected successfully in {duration:.1f}s at {_now_pacific()}]")

            return client

        except Exception as e:
            duration = time.time() - start_time
            last_error = e
            error_msg = str(e)

            # Check if it's the specific timeout error
            is_timeout = "Control request timeout" in error_msg or "initialize" in error_msg.lower()

            if verbose:
                if is_timeout:
                    print(f"[Attempt {attempt} timed out after {duration:.1f}s]")
                    if attempt < max_retries:
                        print(f"  → MCP servers may still be starting. Will retry...")
                else:
                    print(f"[Attempt {attempt} failed after {duration:.1f}s: {error_msg}]")

            if attempt < max_retries:
                if verbose:
                    print(f"[Retrying in {delay:.1f}s...]")
                await asyncio.sleep(delay)
                delay *= backoff_factor

                # Re-warm servers between retries (helps if some failed)
                if pre_warm and hasattr(options, 'mcp_servers') and options.mcp_servers:
                    if verbose:
                        print("[Re-warming MCP servers before retry...]")
                    mcp_config = options.mcp_servers
                    if isinstance(mcp_config, dict):
                        warmer = MCPServerWarmer(mcp_config, timeout_per_server=120.0)
                        await warmer.warm_servers(verbose=verbose)
            else:
                if verbose:
                    print(f"[All {max_retries} attempts failed]")

    raise Exception(f"Failed to initialize after {max_retries} attempts. Last error: {last_error}")


class RobustClaudeSDKClient:
    """
    Wrapper around ClaudeSDKClient that handles cold-start initialization.

    Usage:
        async with RobustClaudeSDKClient(options) as client:
            await client.query("Hello")
            async for msg in client.receive_messages():
                print(msg)
    """

    def __init__(
        self,
        options: Any,
        max_retries: int = 5,
        initial_delay: float = 3.0,
        backoff_factor: float = 1.5,
        pre_warm: bool = True,
        patch_timeout: bool = True,
        verbose: bool = True
    ):
        """
        Args:
            options: ClaudeAgentOptions instance
            max_retries: Maximum initialization attempts
            initial_delay: Delay before first retry
            backoff_factor: Multiply delay after each retry
            pre_warm: Pre-warm MCP servers before first attempt
            patch_timeout: Patch SDK timeout from 60s to 180s
            verbose: Print progress messages
        """
        self.options = options
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.backoff_factor = backoff_factor
        self.pre_warm = pre_warm
        self.patch_timeout = patch_timeout
        self.verbose = verbose
        self._client: Any = None

    async def __aenter__(self) -> Any:
        """Initialize with retry and return the underlying client."""
        self._client = await initialize_with_retry(
            options=self.options,
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            backoff_factor=self.backoff_factor,
            pre_warm=self.pre_warm,
            patch_timeout=self.patch_timeout,
            verbose=self.verbose
        )
        return self._client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Disconnect the client."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass  # Ignore disconnect errors


def load_mcp_config(config_path: str | Path) -> dict[str, Any]:
    """Load MCP configuration from JSON file."""
    with open(config_path) as f:
        config = json.load(f)
    return config.get('mcpServers', {})


# Convenience function to test MCP server connectivity
async def test_mcp_servers(config_path: str | Path, verbose: bool = True) -> dict[str, bool]:
    """
    Test which MCP servers can be started and respond to initialize.

    Returns:
        Dict mapping server name to success status
    """
    mcp_config = load_mcp_config(config_path)
    warmer = MCPServerWarmer(mcp_config)
    await warmer.warm_servers(verbose=verbose)

    results = {}
    for result in warmer.results:
        results[result.name] = result.success

    return results


# Quick diagnostic function
async def diagnose_mcp_startup(config_path: str | Path) -> None:
    """
    Run diagnostics on MCP server startup times.

    Prints detailed timing information for each server.
    """
    print("=" * 60)
    print("MCP Server Startup Diagnostics")
    print("=" * 60)

    mcp_config = load_mcp_config(config_path)
    warmer = MCPServerWarmer(mcp_config, timeout_per_server=60.0)

    start = time.time()
    await warmer.warm_servers(verbose=True)
    total_time = time.time() - start

    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)

    # Sort by duration
    sorted_results = sorted(warmer.results, key=lambda r: r.duration_seconds, reverse=True)

    print(f"\n{'Server':<25} {'Status':<10} {'Duration':<10}")
    print("-" * 45)
    for r in sorted_results:
        status = "OK" if r.success else "FAILED"
        print(f"{r.name:<25} {status:<10} {r.duration_seconds:.1f}s")

    success_count = sum(1 for r in warmer.results if r.success)
    print(f"\nTotal: {success_count}/{len(warmer.results)} servers OK")
    print(f"Parallel warmup time: {total_time:.1f}s")
    print(f"Sum of individual times: {sum(r.duration_seconds for r in warmer.results):.1f}s")
    print("=" * 60)
