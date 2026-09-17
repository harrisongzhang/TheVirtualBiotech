"""Shared lifecycle checks for the optional web and batch entry points."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pyarrow as pa
import pyarrow.parquet as pq
from claude_agent_sdk import AssistantMessage, ResultMessage, ThinkingBlock

import gradio_cso_app as app
import run_vbt
from src.config.datasets import OPEN_TARGETS_DATASETS
from src.data.readiness import DataReadinessError
from src.utils.runtime_paths import RuntimePaths
from src.utils.verify import verify_run
try:
    from tests.test_interactive_session import ScriptedResearchClient
except ModuleNotFoundError as error:
    if error.name not in ("tests", "tests.test_interactive_session"):
        raise
    from test_interactive_session import ScriptedResearchClient


class EarlyDispatchClient(ScriptedResearchClient):
    async def query(self, prompt):
        await super().query(prompt)
        await self.emit_hook(
            "SubagentStart", agent_id="early-agent", agent_type="chief-of-staff",
        )

    async def receive_response(self):
        async for message in super().receive_response():
            if isinstance(message, ResultMessage):
                await self.emit_hook(
                    "SubagentStop", agent_id="early-agent", agent_type="chief-of-staff",
                )
            yield message


class WebSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "reference"
        for name in OPEN_TARGETS_DATASETS:
            directory = self.data / name
            directory.mkdir(parents=True)
            pq.write_table(pa.table({"id": ["ENSG_PCSK9"]}), directory / "part.parquet")
        self.enterContext(patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "test-key-no-network",
            "OPEN_TARGETS_DATA_PATH": str(self.data),
            "CLAUDE_CONFIG_DIR": str(self.root / "runtime-config"),
        }))
        self.enterContext(patch.object(app, "RUNS_DIR", self.root / "runs"))
        self.enterContext(patch.object(app, "session_manager", app.SessionManager()))
        self.enterContext(patch.object(app, "RuntimePaths", side_effect=lambda *args, **kwargs:
            RuntimePaths(*args, temp_parent=self.root, **kwargs)))
        self.clients = []
        self.factory = self.enterContext(patch.object(app, "RobustClaudeSDKClient", side_effect=self.make_client))
        self.stdout = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.stdout))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def make_client(self, **kwargs):
        client = EarlyDispatchClient(**kwargs)
        self.clients.append(client)
        return client

    async def message(self, text, history=None, session_id="web-test"):
        last = None
        async for output in app.process_message(text, history or [], session_id):
            last = output
        return last

    async def test_multi_turn_web_uses_shared_audit_and_captures_early_dispatches(self):
        first = await self.message("Evaluate PCSK9.")
        session = app.session_manager.sessions["web-test"]
        client = session.client
        self.assertTrue(verify_run(session.workspace_dir)["ok"])
        second = await self.message("Compare its evidence with LPA.", first[0])
        self.assertIs(session.client, client)
        self.assertEqual(client.prompts, ["Evaluate PCSK9.", "Compare its evidence with LPA."])
        self.factory.assert_called_once()
        self.assertEqual(len(session.turns), 2)
        self.assertEqual(session.turns[0]["agents_dispatched"], ["chief-of-staff", "genomics-analyst"])
        self.assertEqual(session.turns[1]["agents_dispatched"], ["chief-of-staff", "genomics-analyst"])
        self.assertEqual(session.claim_set.stats()["n_claims"], 2)
        self.assertIn("mcp__genetics__query_gwas_associations", session.turns[1]["mcp_tools_used"])
        self.assertTrue(verify_run(session.workspace_dir)["ok"])
        self.assertIn("LPA", second[0][-1]["content"])
        self.assertTrue((self.root / "runs" / "INDEX.json").exists())
        final = (session.workspace_dir / "report/FINAL_REPORT.md").read_text()
        self.assertIn("Recorded PCSK9 evidence [[claim:C1]]", final)
        self.assertIn("Comparison with LPA for the same indication [[claim:C2]]", final)
        self.assertNotIn("cso-thinking", final)
        self.assertNotIn("Reasoning fixture", final)
        self.assertNotIn("reasoning-only", final)
        for turn in session.turns:
            self.assertEqual(len(turn["thinking_traces"]), 2)
            self.assertNotIn("Reasoning fixture", turn["response"])
        self.assertIn("<cso-thinking>", second[0][-1]["content"])
        self.assertIn("Reasoning fixture", second[0][-1]["content"])

    async def test_result_text_is_saved_when_only_thinking_blocks_were_streamed(self):
        session = app.session_manager.get_or_create_session("web-test")
        session.ensure_run("Hello.")
        await session.initialize()
        answer = "Hello. What would you like to investigate?"

        async def responses():
            yield AssistantMessage(content=[
                ThinkingBlock(thinking="Reasoning fixture.", signature="fixture"),
            ], model=session.client.options.model)
            yield ResultMessage(
                subtype="success", duration_ms=100, duration_api_ms=50, is_error=False,
                num_turns=1, session_id=session.client.options.session_id,
                total_cost_usd=0.01, result=answer,
            )

        with patch.object(session.client, "receive_response", responses):
            output = await self.message("Hello.")
        self.assertEqual(session.turns[-1]["response"], answer)
        self.assertEqual((session.workspace_dir / "report/FINAL_REPORT.md").read_text(), answer)
        self.assertIn(answer, output[0][-1]["content"])
        self.assertIn("Reasoning fixture", output[0][-1]["content"])

    async def test_sessions_have_private_child_environments(self):
        prior = os.environ.get("VBT_RUN_DIR")
        await self.message("Evaluate PCSK9.", session_id="first")
        await self.message("Evaluate PCSK9.", session_id="second")
        self.assertEqual(os.environ.get("VBT_RUN_DIR"), prior)
        first, second = (app.session_manager.sessions[name] for name in ("first", "second"))
        self.assertNotEqual(first.workspace_dir, second.workspace_dir)
        for session in (first, second):
            options = session.client.options
            self.assertEqual(options.env["VBT_RUN_DIR"], str(session.workspace_dir))
            self.assertEqual(options.session_id, session.runtime_paths.session_id)
            self.assertEqual(options.mcp_servers["provenance"]["env"]["VBT_RUN_DIR"], str(session.workspace_dir))

    async def test_missing_reference_data_stops_initial_and_followup_queries(self):
        first = await self.message("Evaluate PCSK9.")
        session = app.session_manager.sessions["web-test"]
        (self.data / "credible_set" / "part.parquet").unlink()
        with self.assertRaises(DataReadinessError):
            await self.message("Compare its evidence with LPA.", first[0])
        self.assertEqual(session.client.prompts, ["Evaluate PCSK9."])
        with self.assertRaises(DataReadinessError):
            await self.message("Evaluate LPA.", session_id="not-started")
        self.factory.assert_called_once()
        last = None
        async for output in app.async_process_message("Evaluate LPA.", [], "visible-error"):
            last = output
        self.assertIn("Reference data is not ready", last[0][-1]["content"])
        self.assertIn("doctor.py --skip-api-key", last[0][-1]["content"])
        client = session.client
        async for output in app.async_process_message(
            "Compare its evidence with LPA.", first[0], "web-test",
        ):
            last = output
        self.assertIn("doctor.py --skip-api-key", last[0][-1]["content"])
        self.assertIs(session.client, client)
        self.assertTrue(session.is_initialized)
        self.assertFalse(session._needs_client_reset)

    async def test_failed_source_remains_visible_in_web_and_audit(self):
        session = app.session_manager.get_or_create_session("web-test")
        session.ensure_run("Evaluate PCSK9.")
        await session.initialize()
        session.client.fail_source = True
        output = await self.message("Evaluate PCSK9.")
        self.assertIn("Data/evidence warning", output[0][-1]["content"])
        self.assertIn("Evidence audit incomplete", output[1])
        verification = verify_run(session.workspace_dir)
        self.assertFalse(verification["ok"])
        self.assertIn("data_source_unavailable", {p["kind"] for p in verification["problems"]})

    async def test_finalization_refreshes_evidence_when_claim_count_is_unchanged(self):
        await self.message("Evaluate PCSK9.")
        session = app.session_manager.sessions["web-test"]
        original = app._evidence_outputs("web-test")
        path = session.workspace_dir / "evidence" / "claims.json"
        claims = json.loads(path.read_text())
        claims["claims"][0]["text"] = "Updated evidence description for the same claim."
        path.write_text(json.dumps(claims))
        app._finalize_run(session)
        updated = app._evidence_outputs("web-test")
        self.assertNotEqual(original, updated)
        self.assertIn("Updated evidence description", updated[0])

    async def test_closing_stream_preserves_partial_turn_and_releases_lock(self):
        stream = app.process_message("Evaluate PCSK9.", [], "web-test")
        await anext(stream)
        await anext(stream)
        session = app.session_manager.sessions["web-test"]
        session.abort_query()
        self.assertTrue(session.query_lock.locked())
        await stream.aclose()
        self.assertFalse(session.query_lock.locked())
        self.assertEqual(session.turns[-1]["status"], "interrupted")
        manifest = json.loads((session.workspace_dir / "MANIFEST.json").read_text())
        self.assertEqual(manifest["status"], "interrupted")
        self.assertFalse(verify_run(session.workspace_dir)["ok"])
        self.assertTrue(session._needs_client_reset)
        final = (session.workspace_dir / "report/FINAL_REPORT.md").read_text()
        self.assertNotIn("Reasoning fixture", final)
        self.assertNotIn("cso-thinking", final)
        self.assertIn("Turn interrupted", final)

    async def test_headless_runner_uses_one_client_and_returns_verified_run(self):
        with patch.object(run_vbt, "_app", return_value=app):
            result = await run_vbt._run_turns(
                ["Evaluate PCSK9.", "Compare its evidence with LPA."],
                "Sonnet 4.5 (default)", session_id="batch", quiet=False,
            )
        session = app.session_manager.sessions["batch"]
        self.assertEqual(result, session.workspace_dir)
        self.assertTrue(verify_run(result)["ok"])
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(self.clients[0].prompts, ["Evaluate PCSK9.", "Compare its evidence with LPA."])
        self.assertTrue(self.clients[0].disconnected)
        self.assertIsNone(session.client)
        self.assertIn("Recorded PCSK9 evidence [[claim:C1]]", self.stdout.getvalue())
        self.assertIn("Comparison with LPA for the same indication [[claim:C2]]", self.stdout.getvalue())
        self.assertNotIn("Reasoning fixture", self.stdout.getvalue())
        self.assertNotIn("cso-thinking", self.stdout.getvalue())

    async def test_headless_preflight_failure_returns_failure_without_client(self):
        (self.data / "credible_set" / "part.parquet").unlink()
        with patch.object(run_vbt, "_app", return_value=app):
            result = await run_vbt._run_turns(
                ["Evaluate PCSK9."], "Sonnet 4.5 (default)", session_id="batch", quiet=True,
            )
        self.assertIsNone(result)
        self.factory.assert_not_called()
        self.assertFalse(app.session_manager.sessions["batch"].is_initialized)

    async def test_headless_followup_preflight_failure_still_disconnects_client(self):
        def create(**kwargs):
            client = self.make_client(**kwargs)
            receive = client.receive_response

            async def responses():
                async for response in receive():
                    yield response
                (self.data / "credible_set" / "part.parquet").unlink()

            client.receive_response = responses
            return client

        self.factory.side_effect = create
        with patch.object(run_vbt, "_app", return_value=app):
            result = await run_vbt._run_turns(
                ["Evaluate PCSK9.", "Compare its evidence with LPA."],
                "Sonnet 4.5 (default)", session_id="batch", quiet=False,
            )
        self.assertIsNone(result)
        self.assertEqual(self.clients[0].prompts, ["Evaluate PCSK9."])
        self.assertTrue(self.clients[0].disconnected)
        self.assertIn("Reference data is not ready", self.stdout.getvalue())
        self.assertNotIn("Reasoning fixture", self.stdout.getvalue())
        self.assertNotIn("cso-thinking", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
