"""Preflight and MCP wire errors, without model calls or external data sources."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.config.datasets import OPEN_TARGETS_DATASETS
from src.data.readiness import DataReadinessError, require_reference_data
from src.utils.tool_errors import tool_result_error
from tools.download_open_targets import BASE, RELEASE

HAS_MCP = all(importlib.util.find_spec(name) is not None
              for name in ("fastmcp", "pandas", "pyarrow", "dotenv"))


class TestResearchPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.entries = {}
        for name in OPEN_TARGETS_DATASETS:
            path = self.root / name / "part.parquet"
            path.parent.mkdir()
            path.write_bytes(b"PAR1test\x04\x00\x00\x00PAR1")
            self.entries[f"{name}/part.parquet"] = {"bytes": path.stat().st_size}
        self.manifest = self.root / ".download-manifest.json"
        self.manifest.write_text(json.dumps({
            "release": RELEASE, "base": BASE, "complete": True,
            "expected_files": len(self.entries), "files": self.entries,
        }))

    def test_live_preflight_uses_current_environment_and_rechecks_followups(self):
        with patch.dict(os.environ, {"OPEN_TARGETS_DATA_PATH": str(self.root)}):
            self.assertEqual(len(require_reference_data()), 38)
            inventory = json.loads(self.manifest.read_text())
            inventory["complete"] = False
            self.manifest.write_text(json.dumps(inventory))
            with self.assertRaisesRegex(DataReadinessError, "manifest is incomplete"):
                require_reference_data()
        with patch.dict(os.environ, {"OPEN_TARGETS_DATA_PATH": ""}):
            with self.assertRaisesRegex(DataReadinessError, "not been sent to the model"):
                require_reference_data()

    def test_missing_shard_cannot_pass_just_because_every_dataset_exists(self):
        inventory = json.loads(self.manifest.read_text())
        inventory["files"]["known_drug/missing.parquet"] = {"bytes": 16}
        inventory["expected_files"] += 1
        self.manifest.write_text(json.dumps(inventory))
        with self.assertRaisesRegex(DataReadinessError, "manifest inventory"):
            require_reference_data(str(self.root))

    def test_bad_manifest_and_changed_files_are_actionable(self):
        self.manifest.write_text("{unfinished")
        with self.assertRaisesRegex(DataReadinessError, "doctor.py --skip-api-key"):
            require_reference_data(str(self.root))
        self.manifest.unlink()
        (self.root / "target/part.parquet").write_bytes(b"partial")
        with self.assertRaisesRegex(DataReadinessError, "truncated Parquet"):
            require_reference_data(str(self.root))

    def test_inventory_check_needs_no_model_or_dataframe_dependencies(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from src.data.readiness import require_reference_data; "
            "assert len(require_reference_data(sys.argv[2])) == 38; "
            "assert not {'pyarrow', 'pandas', 'claude_agent_sdk', 'fastmcp'} & set(sys.modules)"
        )
        result = subprocess.run([sys.executable, "-S", "-c", code, str(REPO), str(self.root)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestToolFailureClassification(unittest.TestCase):
    def test_operational_failures_with_legacy_envelopes(self):
        failures = [
            {"success": False, "error": "Dataset 'known_drug' not found at /reference"},
            {"found": False, "error": "Dataset 'target' not found at /reference"},
            {"error": "Failed to list tissues: missing expression dataset", "tissues": []},
            {"ok": False, "errors": ["No evidence survived validation"]},
            {"success": False, "error": "Network error: connection refused"},
            {"success": False, "error": "Must provide target_id or drug_id"},
            {"success": False},
        ]
        for payload in failures:
            with self.subTest(payload=payload):
                error = tool_result_error(payload)
                self.assertTrue(error)
                self.assertEqual(error, tool_result_error(json.dumps(payload)))
                self.assertEqual(error, tool_result_error([
                    {"type": "text", "text": json.dumps(payload)},
                ]))
                self.assertEqual(error, tool_result_error({
                    "type": "tool_result", "is_error": False,
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                }))

    def test_zero_results_and_unknown_entities_are_not_source_failures(self):
        empty_results = [
            {"success": True, "count": 0, "drugs": []},
            {"found": False, "error": "Target ENSG_UNRECOGNIZED not found"},
            {"success": False, "error": "Drug CHEMBL_UNRECOGNIZED not found"},
            {"success": False, "error": "Disease EFO_UNRECOGNIZED not found"},
            {"found": False, "error": "Gene not found in expression dataset"},
            {"success": False, "error": "rsID 'rs_missing' not found in database"},
            {"success": False, "error": "No samples found"},
            {"n_cells_total": 0, "error": "No cells found for filter: disease == 'x'"},
            {"success": True, "records": [{"error": "a column in a data row"}]},
        ]
        for payload in empty_results:
            with self.subTest(payload=payload):
                self.assertIsNone(tool_result_error(payload))

    def test_transport_error_flags_and_structured_content_are_preserved(self):
        self.assertEqual(tool_result_error({"isError": True, "content": "Unavailable"}),
                         "Unavailable")
        self.assertEqual(tool_result_error({
            "isError": False, "structuredContent": {"success": False, "error": "Timeout"},
        }), "Timeout")
        self.assertIsNone(tool_result_error("A narrative about an error in an earlier study"))


@unittest.skipUnless(HAS_MCP, "requires the application data and MCP dependencies")
class TestRegisteredToolSchemas(unittest.TestCase):
    def test_every_registered_tool_retains_its_parameters_and_success_schema(self):
        from fastmcp.tools import Tool
        from tools.doctor import MCP_SERVERS

        async def check():
            total = 0
            for name in MCP_SERVERS:
                module = importlib.import_module(f"src.mcp_servers.{name}_mcp.server")
                for registered in await module.mcp.list_tools():
                    original = Tool.from_function(getattr(module, registered.name))
                    with self.subTest(server=name, tool=registered.name):
                        self.assertEqual(registered.parameters, original.parameters)
                        self.assertEqual(registered.output_schema, original.output_schema)
                        self.assertEqual(registered.description, original.description)
                    total += 1
            self.assertEqual(total, 105)

        asyncio.run(asyncio.wait_for(check(), timeout=30))

    def test_async_tools_also_expose_failures(self):
        from fastmcp import FastMCP
        from fastmcp.exceptions import ToolError
        from src.mcp_servers.registration import register_tool

        async def unavailable() -> dict:
            return {"success": False, "error": "Network timeout"}

        # Calling the returned function isolates asynchronous wrapping without
        # adding a second transport test or a network dependency.
        wrapped = register_tool(FastMCP("Async error regression"), unavailable)
        with self.assertRaisesRegex(ToolError, "Network timeout"):
            asyncio.run(asyncio.wait_for(wrapped(), timeout=5))


@unittest.skipUnless(HAS_MCP and os.name == "posix", "requires MCP and POSIX pipes")
class TestDrugToolWireResults(unittest.TestCase):
    def test_missing_data_recovery_empty_matches_and_validation_over_stdio(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryFile(mode="w+") as errors:
            root = Path(tmp)
            (root / "target").mkdir()
            pq.write_table(pa.table({"id": ["ENSG_PCSK9"]}), root / "target/part.parquet")
            env = dict(os.environ, OPEN_TARGETS_DATA_PATH=str(root), PRELOAD_MCP_DATA="0",
                       ANTHROPIC_API_KEY="", PYTHONUNBUFFERED="1")
            process = subprocess.Popen(
                [sys.executable, str(REPO / "src/mcp_servers/drug_mcp/server.py")],
                cwd=REPO, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=errors,
            )
            sequence = 0
            pending = b""
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)

            def send(message):
                process.stdin.write((json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode())
                process.stdin.flush()

            def request(method, params):
                nonlocal sequence, pending
                sequence += 1
                send({"id": sequence, "method": method, "params": params})
                deadline = time.monotonic() + 20
                while True:
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        message = json.loads(line)
                        if message.get("id") == sequence:
                            self.assertNotIn("error", message, message)
                            return message["result"]
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        errors.seek(0)
                        self.fail(f"Timed out waiting for MCP {method}: {errors.read()[-4000:]}")
                    block = os.read(process.stdout.fileno(), 65536)
                    if not block:
                        errors.seek(0)
                        self.fail(f"MCP server exited: {errors.read()[-4000:]}")
                    pending += block

            def call(name, arguments):
                return request("tools/call", {"name": name, "arguments": arguments})

            try:
                request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                       "clientInfo": {"name": "installation-regression", "version": "1"}})
                send({"method": "notifications/initialized"})
                self.assertEqual(len(request("tools/list", {})["tools"]), 9)

                missing = call("search_known_drugs", {"target_id": "ENSG_PCSK9"})
                self.assertTrue(missing["isError"], missing)
                self.assertIn("Dataset 'known_drug' not found", tool_result_error(missing))
                self.assertIn("did not produce evidence", json.dumps(missing))

                (root / "known_drug").mkdir()
                pq.write_table(pa.table({
                    "drugId": ["CHEMBL_EXAMPLE"], "targetId": ["ENSG_PCSK9"],
                    "diseaseId": ["EFO_LDL"], "phase": [4.0],
                }), root / "known_drug/part.parquet")
                recovered = call("search_known_drugs", {"target_id": "ENSG_PCSK9"})
                self.assertFalse(recovered["isError"], recovered)
                self.assertEqual(recovered["structuredContent"]["count"], 1)
                self.assertEqual(recovered["structuredContent"]["drugs"][0]["drugId"], "CHEMBL_EXAMPLE")

                empty = call("search_known_drugs", {"target_id": "ENSG_UNKNOWN"})
                self.assertFalse(empty["isError"], empty)
                self.assertEqual(empty["structuredContent"]["drugs"], [])
                unknown = call("get_target_tractability", {"target_id": "ENSG_UNKNOWN"})
                self.assertFalse(unknown["isError"], unknown)
                self.assertFalse(unknown["structuredContent"]["found"])

                (root / "pharmacogenomics").mkdir()
                pq.write_table(pa.table({"targetId": ["ENSG_PCSK9"]}),
                               root / "pharmacogenomics/part.parquet")
                invalid = call("get_pharmacogenomics", {})
                self.assertTrue(invalid["isError"], invalid)
                self.assertIn("Must provide target_id or drug_id", tool_result_error(invalid))
            finally:
                selector.close()
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()


if __name__ == "__main__":
    unittest.main()
