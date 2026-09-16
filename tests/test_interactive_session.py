"""Exercise the recommended conversational entry point without model requests."""

import asyncio
import contextlib
import io
import json
import os
import re
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from claude_agent_sdk._internal.sessions import _sanitize_path
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

import run as cli
from src.config.datasets import OPEN_TARGETS_DATASETS
from src.data.readiness import DataReadinessError
from src.mcp_servers.provenance_mcp.tools import record_claims, write_plan
from src.utils.runtime_paths import RuntimePaths
from src.utils.verify import verify_run


class ScriptedResearchClient:
    """Replace model traffic while exercising real runner hooks and audit tools."""

    def __init__(self, *, options, **kwargs):
        self.options = options
        self.prompts = []
        self.disconnected = False
        self.fail_source = False
        self.file_claims = True
        self.tool_results = []

    async def __aenter__(self):
        return self

    async def disconnect(self):
        self.disconnected = True

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def emit_hook(self, event, **data):
        payload = {
            "hook_event_name": event,
            "session_id": self.options.session_id,
            "cwd": self.options.cwd,
            **data,
        }
        for matcher in self.options.hooks.get(event, []):
            if matcher.matcher and not re.search(matcher.matcher, data.get("tool_name", "")):
                continue
            for hook in matcher.hooks:
                result = await hook(payload, data.get("tool_use_id"), {})
                if result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                    raise AssertionError(result)

    async def provenance_tool(self, name, tool_id, function, **arguments):
        await self.emit_hook("PreToolUse", tool_name=name, tool_use_id=tool_id, tool_input=arguments)
        # Model the environment inherited by the provenance child process. The
        # real tool must discover the run; no explicit run_dir bypass is used.
        with patch.dict(os.environ, self.options.mcp_servers["provenance"]["env"]):
            result = function(**arguments)
        self.tool_results.append(result)
        if not result.get("ok"):
            raise AssertionError(result)
        await self.emit_hook(
            "PostToolUse", tool_name=name, tool_use_id=tool_id,
            tool_input=arguments, tool_response=result,
        )

    async def receive_response(self):
        turn = len(self.prompts)
        await self.provenance_tool(
            "mcp__provenance__write_plan", f"plan-{turn}", write_plan,
            steps=[{"id": "genetics", "agent": "genomics-analyst",
                    "task": "Review genetic evidence", "depends_on": []}],
            goal=self.prompts[-1],
        )
        agent = f"genetics-{turn}"
        await self.emit_hook("SubagentStart", agent_id=agent, agent_type="genomics-analyst")
        root = Path(self.options.cwd)
        output = root / "work" / "genomics-analyst" / "results" / "tables" / f"evidence-{turn}.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("target,evidence\nPCSK9,observed\n")

        # A specialist can write files and call tools without parent-level tool
        # messages. Only SubagentStop and its recorded transcript expose these.
        tool_id = f"genetics-tool-{turn}"
        response = ({"success": False, "error": "Dataset 'credible_set' is unavailable"}
                    if self.fail_source else {"success": True, "associations": [{"target": "PCSK9"}]})
        transcript = Path(self.options.env["CLAUDE_CODE_TMPDIR"]) / f"agent-{turn}.jsonl"
        transcript.write_text("\n".join(json.dumps({"message": message}) for message in (
            {"role": "user", "content": "Evaluate PCSK9 genetic evidence and save supporting results."},
            {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id,
                "name": "mcp__genetics__query_gwas_associations", "input": {"gene_id": "ENSG_PCSK9"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id,
                "content": json.dumps(response), "is_error": False}]},
        )) + "\n")
        await self.emit_hook(
            "SubagentStop", agent_id=agent, agent_type="genomics-analyst",
            agent_transcript_path=str(transcript),
        )
        if self.file_claims and not self.fail_source:
            await self.provenance_tool(
                "mcp__provenance__record_claims", f"claims-{turn}", record_claims,
                claims=[{"id": f"C{turn}", "text": f"Recorded genetic evidence for turn {turn}.",
                    "agent": "genomics-analyst", "confidence": "moderate", "evidence": [
                        {"kind": "table", "path": output.relative_to(root).as_posix()},
                        {"kind": "tool_call", "tool_use_id": tool_id},
                    ]}],
            )
        response_text = (f"Recorded PCSK9 evidence [[claim:C{turn}]]." if turn == 1
                         else f"Comparison with LPA for the same indication [[claim:C{turn}]].")
        if self.fail_source or not self.file_claims:
            response_text = "A response without filed evidence."
        yield AssistantMessage(content=[TextBlock(text=response_text)], model=self.options.model)
        yield ResultMessage(
            subtype="success", duration_ms=100, duration_api_ms=50, is_error=False,
            num_turns=1, session_id=self.options.session_id, total_cost_usd=0.01 * turn,
            usage={"input_tokens": 10, "output_tokens": 10},
        )


class InteractiveSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "reference"
        table = pa.table({"id": ["ENSG_PCSK9"]})
        for dataset in OPEN_TARGETS_DATASETS:
            directory = self.data / dataset
            directory.mkdir(parents=True)
            pq.write_table(table, directory / "part.parquet")
        self.enterContext(patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "test-key-no-network",
            "OPEN_TARGETS_DATA_PATH": str(self.data),
            "CLAUDE_CONFIG_DIR": str(self.root / "runtime-config"),
        }))
        self.enterContext(patch.object(cli, "SESSIONS_DIR", self.root / "sessions"))
        self.enterContext(patch.object(cli, "RuntimePaths", side_effect=lambda *args, **kwargs:
            RuntimePaths(*args, temp_parent=self.root, **kwargs)))
        self.factory = self.enterContext(patch.object(cli, "RobustClaudeSDKClient", side_effect=ScriptedResearchClient))
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.stdout))
        self.enterContext(contextlib.redirect_stderr(self.stderr))

    def assert_saved_turns(self, session, count):
        root = session.session_dir
        self.assertEqual(session.workspace_dir, root)
        for name in ("MANIFEST.json", "evidence/claims.json", "evidence/provenance.json",
                     "logs/trace.jsonl", "logs/cost_report.json", "report/FINAL_REPORT.md", "audit.html"):
            self.assertTrue((root / name).is_file(), name)
        costs = json.loads((root / "logs/cost_report.json").read_text())
        self.assertEqual(costs["num_turns"], count)
        claims = json.loads((root / "evidence/claims.json").read_text())
        self.assertEqual(claims["stats"]["n_claims"], count)
        manifest = json.loads((root / "MANIFEST.json").read_text())
        for turn in range(1, count + 1):
            artifact = f"work/genomics-analyst/results/tables/evidence-{turn}.csv"
            self.assertIn(artifact, manifest["artifacts"])
            self.assertEqual(manifest["artifacts"][artifact]["produced_by"], "genomics-analyst")
        verification = verify_run(root)
        self.assertTrue(verification["ok"], verification["problems"])
        return costs

    async def test_two_turns_reuse_client_and_save_complete_audits_before_exit(self):
        session = cli.Session()
        await session.initialize()
        client = session.client
        first = "Evaluate PCSK9 as a target for lowering LDL cholesterol."
        followup = "How does its genetic evidence compare to LPA for the same indication?"
        await session.run_turn(first)
        first_costs = self.assert_saved_turns(session, 1)
        await session.run_turn(followup)
        second_costs = self.assert_saved_turns(session, 2)
        self.assertIs(session.client, client)
        self.factory.assert_called_once()
        self.assertEqual(client.prompts, [first, followup])
        self.assertEqual(first_costs["turns"][0]["agents_dispatched"], ["genomics-analyst"])
        self.assertEqual(second_costs["turns"][1]["agents_dispatched"], ["genomics-analyst"])
        self.assertIn("mcp__genetics__query_gwas_associations", second_costs["turns"][1]["mcp_tools_used"])
        self.assertEqual(second_costs["turns"][1]["subagent_traces"][0]["agent_id"], "genetics-2")
        self.assertFalse(client.disconnected)

    async def test_options_scope_runtime_and_mcp_processes_to_the_run(self):
        session = cli.Session()
        await session.initialize()
        options = session.client.options
        self.assertEqual(options.session_id, session.runtime_paths.session_id)
        self.assertEqual(options.env["CLAUDE_CONFIG_DIR"], str(session.runtime_paths.config_dir))
        self.assertEqual(options.env["CLAUDE_CODE_TMPDIR"], str(session.runtime_paths.temp_dir))
        self.assertEqual(options.env["VBT_RUN_DIR"], str(session.session_dir))
        for config in options.mcp_servers.values():
            self.assertEqual(config["env"]["VBT_RUN_DIR"], str(session.session_dir))
            self.assertTrue(Path(config["env"]["MCP_OUTPUT_DIR"]).is_relative_to(session.session_dir))
        self.assertIn("mcp__provenance__write_plan", options.allowed_tools)
        self.assertIn("mcp__provenance__record_claims", options.allowed_tools)
        for specialist in options.agents.values():
            if "Write" in specialist.tools:
                self.assertIn("mcp__provenance__register_artifact", specialist.tools)

    async def test_separate_sessions_cannot_overwrite_each_others_run_environment(self):
        prior = os.environ.get("VBT_RUN_DIR")
        first, second = cli.Session(), cli.Session()
        await first.initialize()
        await second.initialize()
        self.assertNotEqual(first.session_dir, second.session_dir)
        self.assertNotEqual(first.runtime_paths.session_id, second.runtime_paths.session_id)
        self.assertEqual(os.environ.get("VBT_RUN_DIR"), prior)
        for session in (first, second):
            configured = session.client.options.mcp_servers["provenance"]["env"]["VBT_RUN_DIR"]
            self.assertEqual(configured, str(session.session_dir))

    async def test_repl_preserves_multiline_and_followup_then_done_disconnects(self):
        session = cli.Session()
        inputs = iter(['"""', "Evaluate PCSK9.", "Include its genetic evidence.", '"""',
                       "How does its evidence compare to LPA?", "/done"])
        with patch("builtins.input", side_effect=lambda prompt: next(inputs)):
            await session.run_repl()
        self.assertEqual(session.client.prompts, [
            "Evaluate PCSK9.\nInclude its genetic evidence.",
            "How does its evidence compare to LPA?",
        ])
        self.assertTrue(session.client.disconnected)
        self.assert_saved_turns(session, 2)

    async def test_actual_cli_options_allow_only_this_sessions_runtime_outputs(self):
        session = cli.Session()
        await session.initialize()
        runtime = session.runtime_paths
        spill = (runtime.config_dir / "projects" / _sanitize_path(str(session.workspace_dir))
                 / runtime.session_id / "tool-results" / "large.json")
        spill.parent.mkdir(parents=True)
        spill.write_text('{"target": "PCSK9"}')
        payload = {"file_path": str(spill)}
        await session.client.emit_hook("PreToolUse", tool_name="Read", tool_use_id="spill-read", tool_input=payload)
        result = await session.client.options.can_use_tool("Read", payload, ToolPermissionContext())
        self.assertIsInstance(result, PermissionResultAllow)
        secret = {"file_path": str(runtime.config_dir / ".credentials.json")}
        with self.assertRaises(AssertionError):
            await session.client.emit_hook("PreToolUse", tool_name="Read", tool_use_id="secret-read", tool_input=secret)
        result = await session.client.options.can_use_tool("Read", secret, ToolPermissionContext())
        self.assertIsInstance(result, PermissionResultDeny)

    async def test_missing_data_prevents_client_start_and_billable_queries(self):
        (self.data / "credible_set" / "part.parquet").unlink()
        session = cli.Session()
        with self.assertRaises(DataReadinessError):
            await session.initialize()
        self.factory.assert_not_called()

    async def test_followup_rechecks_data_without_losing_conversation(self):
        session = cli.Session()
        await session.initialize()
        await session.run_turn("Evaluate PCSK9.")
        missing = self.data / "credible_set" / "part.parquet"
        contents = missing.read_bytes()
        missing.unlink()
        with self.assertRaises(DataReadinessError):
            await session.run_turn("Compare its evidence with LPA.")
        self.assertEqual(session.client.prompts, ["Evaluate PCSK9."])
        missing.write_bytes(contents)
        await session.run_turn("Compare its evidence with LPA.")
        self.assertEqual(len(session.client.prompts), 2)
        self.factory.assert_called_once()
        self.assert_saved_turns(session, 2)

    async def test_missing_claims_are_reported_as_incomplete_after_the_turn(self):
        session = cli.Session()
        await session.initialize()
        session.client.file_claims = False
        await session.run_turn("Evaluate PCSK9.")
        verification = verify_run(session.session_dir)
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["evidence"]["total"], 0)
        self.assertIn("no_claims", {problem["kind"] for problem in verification["problems"]})
        self.assertIn("incomplete", (self.stdout.getvalue() + self.stderr.getvalue()).lower())

    async def test_failed_specialist_source_is_visible_in_answer_and_audit(self):
        session = cli.Session()
        await session.initialize()
        session.client.fail_source = True
        answer = await session.run_turn("Evaluate PCSK9.")
        self.assertIn("Data/evidence warning", answer)
        final = (session.session_dir / "report/FINAL_REPORT.md").read_text()
        self.assertIn("mcp__genetics__query_gwas_associations", final)
        verification = verify_run(session.session_dir)
        self.assertFalse(verification["ok"])
        self.assertIn("data_source_unavailable", {problem["kind"] for problem in verification["problems"]})

    async def test_interrupted_followup_preserves_prior_evidence_and_partial_response(self):
        session = cli.Session()
        await session.initialize()
        await session.run_turn("Evaluate PCSK9.")
        client = session.client

        async def interrupted_response():
            yield AssistantMessage(content=[TextBlock(text="Partial comparison [[claim:C1]].")],
                                   model=client.options.model)
            raise asyncio.CancelledError()

        with patch.object(client, "receive_response", interrupted_response):
            with self.assertRaises(asyncio.CancelledError):
                await session.run_turn("Compare its evidence with LPA.")
        session.write_reports(quiet=True)
        costs = json.loads((session.session_dir / "logs/cost_report.json").read_text())
        self.assertEqual(costs["num_turns"], 2)
        self.assertEqual(costs["turns"][1]["status"], "interrupted")
        self.assertEqual(costs["turns"][1]["agents_dispatched"], [])
        self.assertEqual(costs["turns"][1]["subagent_traces"], [])
        self.assertIn("Partial comparison", (session.session_dir / "logs/transcript.md").read_text())
        verification = verify_run(session.session_dir)
        self.assertTrue(verification["integrity"]["ok"])
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["evidence"]["total"], 1)
        self.assertIs(session.client, client)

    async def test_incomplete_transport_stream_saves_a_failed_turn(self):
        session = cli.Session()
        await session.initialize()

        async def truncated_response():
            yield AssistantMessage(content=[TextBlock(text="Partial result.")],
                                   model=session.client.options.model)

        with patch.object(session.client, "receive_response", truncated_response):
            with self.assertRaisesRegex(RuntimeError, "before the turn completed"):
                await session.run_turn("Evaluate PCSK9.")
        self.assertEqual(session.turns[0]["status"], "interrupted")
        self.assertIn("Turn incomplete", session.turns[0]["response"])
        self.assertFalse(verify_run(session.session_dir)["ok"])

    async def test_report_write_failure_still_disconnects_and_restores_signal_handler(self):
        session = cli.Session()
        handler = signal.getsignal(signal.SIGINT)
        with patch("builtins.input", return_value="/done"), \
                patch.object(session, "write_reports", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                await session.run_repl()
        self.assertTrue(session.client.disconnected)
        self.assertIs(signal.getsignal(signal.SIGINT), handler)


if __name__ == "__main__":
    unittest.main()
