"""Exercise live audit hooks, recovery, and turn boundaries without a model."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.utils.run_manifest import CSO_DIR, RunManifest, snapshot_dir
from src.utils.claims import ClaimSet
from src.utils.session_audit import SessionAudit, data_failure_notice, scoped_mcp_servers
from src.utils.trace_logger import TraceLogger, agents_in_events, tool_failures, unresolved_tool_failures
from src.utils.provenance import build_provenance
from src.mcp_servers.provenance_mcp.tools import record_claims, write_plan

HAS_RUNTIME = importlib.util.find_spec("claude_agent_sdk") is not None


class TestFailureRecovery(unittest.TestCase):
    def test_successful_retry_resolves_only_the_same_query(self):
        trace = TraceLogger()
        tool = "mcp__drug__search_known_drugs"
        inputs = {"target_id": "ENSG_PCSK9", "min_phase": 4}
        trace.tool_error("failed", tool, inputs, "Data unavailable")
        self.assertIn(tool, data_failure_notice(trace.events))
        trace.tool_end("other_target", tool, {"target_id": "ENSG_LPA", "min_phase": 4},
                       {"success": True, "count": 1})
        self.assertEqual(len(unresolved_tool_failures(trace.events)), 1)
        trace.tool_end("recovered", tool, {"min_phase": 4, "target_id": "ENSG_PCSK9"},
                       {"success": True, "count": 1})
        self.assertEqual(unresolved_tool_failures(trace.events), [])
        self.assertEqual(data_failure_notice(trace.events), "")
        self.assertEqual(len(tool_failures(trace.events)), 1, "Do not erase failed attempt history")

    def test_empty_successful_retry_and_start_only_arguments(self):
        trace = TraceLogger()
        tool = "mcp__drug__search_known_drugs"
        trace.tool_start("a", tool, {"target_id": "ENSG_UNKNOWN"})
        trace.tool_error("a", tool, None, "Data unavailable")
        trace.tool_start("b", tool, {"target_id": "ENSG_UNKNOWN"})
        trace.tool_end("b", tool, None, {"success": True, "count": 0, "drugs": []})
        self.assertEqual(unresolved_tool_failures(trace.events), [])

    def test_rejected_claim_is_not_mislabeled_as_a_data_source_outage(self):
        trace = TraceLogger()
        trace.tool_end("claim", "mcp__provenance__record_claims", {"claims": []},
                       {"ok": False, "errors": ["No evidence survived validation"]})
        self.assertEqual(len(tool_failures(trace.events)), 1)
        self.assertEqual(data_failure_notice(trace.events), "")


@unittest.skipUnless(HAS_RUNTIME, "requires the application hook types")
class TestLiveSessionAudit(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = RunManifest.create(self.root, query="Assess the genetic evidence")
        self.trace = TraceLogger()
        self.audit = SessionAudit(self.run, self.trace)
        self.audit.begin_turn("Assess the genetic evidence", 1)
        self.hooks = self.audit.build_hooks()
        self.bound = patch.dict(os.environ, {"VBT_RUN_DIR": str(self.run.run_dir)})
        self.bound.start()
        self.addCleanup(self.bound.stop)

    async def hook(self, name, **event):
        payload = {"hook_event_name": name, "cwd": str(self.run.run_dir), **event}
        for matcher in self.hooks[name]:
            for callback in matcher.hooks:
                result = await callback(payload, event.get("tool_use_id"), {})
                self.assertEqual(result, {}, result)

    def claim(self, identifier="C1", **evidence):
        return {"id": identifier, "text": "A recorded finding", "evidence": [evidence]}

    async def test_artifact_and_trace_can_be_cited_before_turn_finishes(self):
        self.audit.begin_turn("Assess PCSK9", 1)
        path = self.run.run_dir / "work/genomics-analyst/results/tables/evidence.csv"
        path.parent.mkdir(parents=True)
        await self.hook("PreToolUse", agent_type="genomics-analyst", agent_id="a1",
                        tool_use_id="write", tool_name="Write", tool_input={"file_path": str(path)})
        path.write_text("target,score\nPCSK9,0.9\n")
        await self.hook("PostToolUse", agent_type="genomics-analyst", agent_id="a1",
                        tool_use_id="write", tool_name="Write", tool_input={"file_path": str(path)},
                        tool_response="Written")
        self.assertIn(str(path.relative_to(self.run.run_dir)), RunManifest.load(self.run.run_dir).data["artifacts"])
        result = record_claims([
            self.claim(kind="table", path=str(path.relative_to(self.run.run_dir))),
            self.claim("C2", kind="tool_call", tool_use_id="write"),
        ])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["recorded"], 2)
        self.assertEqual(build_provenance(self.run.run_dir / "logs/trace.jsonl").agent_for("write"),
                         "genomics-analyst")

    async def test_main_thread_calls_do_not_belong_to_the_only_active_child(self):
        await self.hook("SubagentStart", agent_id="a1", agent_type="genomics-analyst")
        path = self.run.run_dir / "parent-note.txt"
        await self.hook("PreToolUse", tool_use_id="parent", tool_name="Write",
                        tool_input={"file_path": str(path)})
        path.write_text("Parent synthesis")
        await self.hook("PostToolUse", tool_use_id="parent", tool_name="Write",
                        tool_input={"file_path": str(path)}, tool_response="Written")
        parent_events = [e for e in self.trace.events if e.get("tool_use_id") == "parent"]
        self.assertTrue(all(e["agent"] == CSO_DIR for e in parent_events))
        self.assertEqual(RunManifest.load(self.run.run_dir).data["artifacts"]["parent-note.txt"]["produced_by"], CSO_DIR)

    async def test_parallel_shared_outputs_are_not_credited_to_the_first_hook(self):
        shared = self.run.run_dir / "work/_mcp/data/processed/shared.csv"
        other = self.run.run_dir / "work/second-analyst/results/tables/other.csv"
        for path in (shared, other):
            path.parent.mkdir(parents=True)
            path.write_text("x\n1\n")
        self.audit.capture("first-analyst", "first-call")
        artifacts = RunManifest.load(self.run.run_dir).data["artifacts"]
        shared_entry = artifacts[str(shared.relative_to(self.run.run_dir))]
        self.assertEqual(shared_entry["produced_by"], "_mcp")
        self.assertIsNone(shared_entry["tool_use_id"])
        other_entry = artifacts[str(other.relative_to(self.run.run_dir))]
        self.assertEqual(other_entry["produced_by"], "second-analyst")
        self.assertIsNone(other_entry["tool_use_id"])

    async def test_child_transcript_recovers_calls_and_errors_before_claim_validation(self):
        await self.hook("SubagentStart", agent_id="a1", agent_type="genomics-analyst")
        transcript = self.root / "child.jsonl"
        payload = [{"type": "text", "text": json.dumps({
            "success": False, "error": "Dataset 'known_drug' not found",
        })}]
        messages = [
            {"message": {"role": "user", "content": "Assess the clinical evidence"}},
            {"message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "failed", "name": "mcp__drug__search_known_drugs", "input": {}},
                {"type": "tool_use", "id": "ok", "name": "mcp__target__get_target_info", "input": {"target_id": "PCSK9"}},
            ]}},
            {"message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "failed", "content": payload, "is_error": False},
                {"type": "tool_result", "tool_use_id": "ok", "content": [{"type": "text", "text": '{"found":true}'}]},
            ]}},
        ]
        transcript.write_text("\n".join(json.dumps(m) for m in messages) + "\n")
        await self.hook("SubagentStop", agent_id="a1", agent_type="genomics-analyst",
                        agent_transcript_path=str(transcript))
        prov = build_provenance(self.run.run_dir / "logs/trace.jsonl")
        self.assertEqual(len(prov.calls), 2)
        self.assertTrue(prov.calls["failed"]["is_error"])
        self.assertFalse(record_claims([self.claim(kind="tool_call", tool_use_id="failed")])["ok"])
        self.assertTrue(record_claims([self.claim(kind="tool_call", tool_use_id="ok")])["ok"])
        # Repeated stop/import notifications must not duplicate the tool events.
        await self.hook("SubagentStop", agent_id="a1", agent_type="genomics-analyst",
                        agent_transcript_path=str(transcript))
        self.assertEqual(sum(e["type"] == "tool_end" for e in self.trace.events), 2)

    async def test_each_turn_preserves_prior_claims_plan_and_agent_boundaries(self):
        self.audit.begin_turn("Assess PCSK9", 1)
        plan_result = write_plan([{"id": "s1", "agent": "genomics-analyst", "task": "Assess genetics"}])
        self.assertTrue(plan_result["ok"], plan_result)
        await self.hook("SubagentStart", agent_id="a1", agent_type="genomics-analyst")
        await self.hook("PreToolUse", agent_id="a1", tool_use_id="first", tool_name="mcp__target__get_target_info",
                        tool_input={"target_id": "PCSK9"})
        await self.hook("PostToolUse", agent_id="a1", tool_use_id="first", tool_name="mcp__target__get_target_info",
                        tool_input={"target_id": "PCSK9"}, tool_response={"found": True})
        await self.hook("SubagentStop", agent_id="a1", agent_type="genomics-analyst")
        self.assertTrue(record_claims([self.claim(kind="tool_call", tool_use_id="first")])["ok"])
        turns = [{"turn": 1, "prompt": "Assess PCSK9", "response": "Recorded evidence [[claim:C1]]"}]
        _, coverage = self.audit.finish_turn(turns, 0)
        self.assertTrue(coverage["ok"], coverage)
        self.assertEqual(agents_in_events(self.trace.events), ["genomics-analyst"])

        boundary = len(self.trace.events)
        self.audit.begin_turn("Compare its evidence to LPA", 2)
        await self.hook("SubagentStart", agent_id="a2", agent_type="drug-discovery-scientist")
        await self.hook("SubagentStop", agent_id="a2", agent_type="drug-discovery-scientist")
        self.assertEqual(agents_in_events(self.trace.events_since(boundary)), ["drug-discovery-scientist"])
        turns.append({"turn": 2, "prompt": "Compare its evidence to LPA", "response": "Earlier evidence [[claim:C1]]"})
        _, coverage = self.audit.finish_turn(turns, 0)
        self.assertTrue(coverage["ok"], coverage)
        stored = RunManifest.load(self.run.run_dir)
        self.assertEqual(stored.data["plan"]["steps"][0]["id"], "s1")
        self.assertEqual(len(json.loads((self.run.run_dir / "evidence/claims.json").read_text())["claims"]), 1)
        self.assertEqual(stored.data["config"]["num_turns"], 2)

    async def test_rewritten_evidence_refreshes_badges_without_rewriting_claim_hashes(self):
        from src.utils.claim_ui import render_claim_refs

        path = self.run.run_dir / "work/genomics-analyst/results/tables/evidence.csv"
        path.parent.mkdir(parents=True)
        path.write_text("target,score\nPCSK9,0.9\n")
        relative = str(path.relative_to(self.run.run_dir))
        self.audit.capture("genomics-analyst")
        self.assertTrue(record_claims([self.claim(kind="table", path=relative)])["ok"])
        turns = [{"turn": 1, "prompt": "Assess PCSK9", "response": "Finding [[claim:C1]]"}]
        claims, coverage = self.audit.finish_turn(turns, 0)
        self.assertTrue(coverage["ok"], coverage)
        original_hash = claims.by_id("C1")["evidence"][0]["sha256"]
        self.assertEqual(claims.by_id("C1")["n_verified"], 1)

        self.audit.begin_turn("Review the updated evidence", 2)
        path.write_text("target,score\nPCSK9,0.1\n")
        turns.append({"turn": 2, "prompt": "Review the updated evidence", "response": "Earlier finding [[claim:C1]]"})
        claims, coverage = self.audit.finish_turn(turns, 0)
        self.assertFalse(coverage["ok"])
        claim = claims.by_id("C1")
        self.assertEqual(claim["n_verified"], 0)
        self.assertFalse(claim["evidence"][0]["verified"])
        self.assertEqual(claim["evidence"][0]["sha256"], original_hash)
        self.assertIn("unverified", render_claim_refs("Earlier finding [[claim:C1]]", claims))
        stored = ClaimSet.load(self.run.run_dir / "evidence/claims.json").by_id("C1")
        self.assertEqual(stored["n_verified"], 0)
        self.assertEqual(stored["evidence"][0]["sha256"], original_hash)
        self.assertIn("not on record", (self.run.run_dir / "audit.html").read_text())

        # An explicit refiling after reviewing the revision can update support.
        self.assertTrue(record_claims([self.claim(kind="table", path=relative)])["ok"])
        claims, coverage = self.audit.finish_turn(turns, 0)
        self.assertTrue(coverage["ok"], coverage)
        self.assertEqual(claims.by_id("C1")["n_verified"], 1)
        self.assertNotEqual(claims.by_id("C1")["evidence"][0]["sha256"], original_hash)

    async def test_interrupted_response_with_valid_evidence_never_passes_as_complete(self):
        from src.utils.verify import verify_integrity

        self.trace.tool_end("evidence", "mcp__target__get_target_info", {}, {"found": True})
        self.assertTrue(record_claims([self.claim(kind="tool_call", tool_use_id="evidence")])["ok"])
        turns = [{"turn": 1, "prompt": "Assess evidence", "response": "Partial finding [[claim:C1]]",
                  "status": "interrupted"}]
        _, coverage = self.audit.finish_turn(turns, 0, interrupted=True)
        self.assertFalse(coverage["ok"])
        self.assertTrue(any(p["kind"] == "interrupted_turn" for p in coverage["problems"]))
        self.assertFalse(verify_integrity(self.run.run_dir)["ok"])

        # A follow-up and final /done save retain the incomplete earlier turn.
        self.audit.begin_turn("Clarify the finding", 2)
        turns.append({"turn": 2, "prompt": "Clarify the finding", "response": "Clarification [[claim:C1]]",
                      "status": "completed"})
        for _ in range(2):
            _, coverage = self.audit.finish_turn(turns, 0)
            self.assertFalse(coverage["ok"])
            self.assertEqual(self.run.data["config"]["interrupted_turns"], [1])
            self.assertEqual(self.run.data["status"], "interrupted")
        checked = verify_integrity(self.run.run_dir)
        self.assertFalse(checked["ok"])
        self.assertTrue(checked["integrity"]["ok"])

    async def test_direct_main_thread_research_tools_require_claims(self):
        for tool in ("WebSearch", "WebFetch", "mcp__target__get_target_info"):
            with self.subTest(tool=tool):
                # Reset only the requirement between independent tool probes.
                self.audit._update(lambda current: current.data["config"].update(audit_required=False))
                await self.hook("PreToolUse", tool_use_id=tool, tool_name=tool, tool_input={})
                await self.hook("PostToolUse", tool_use_id=tool, tool_name=tool, tool_input={},
                                tool_response={"found": True})
                _, coverage = self.audit.finish_turn([
                    {"turn": 1, "prompt": "Assess evidence", "response": "A finding without filed claims"},
                ], 0)
                self.assertTrue(coverage["required"])
                self.assertTrue(any(p["kind"] == "no_claims" for p in coverage["problems"]))

    async def test_recovered_direct_tool_calls_require_claims_but_greetings_do_not(self):
        _, greeting = self.audit.finish_turn([
            {"turn": 1, "prompt": "Hello", "response": "Hello"},
        ], 0)
        self.assertEqual(greeting["status"], "not_required")
        self.audit.begin_turn("Assess evidence", 2)
        self.audit._import_conversation(CSO_DIR, [
            {"tool_calls": [{"id": "recovered", "name": "WebSearch", "input": {"query": "PCSK9 genetics"}}]},
            {"tool_results": [{"tool_use_id": "recovered", "content": "Search results"}]},
        ])
        _, coverage = self.audit.finish_turn([
            {"turn": 1, "prompt": "Hello", "response": "Hello"},
            {"turn": 2, "prompt": "Assess evidence", "response": "A researched finding"},
        ], 0)
        self.assertTrue(coverage["required"])
        self.assertFalse(coverage["ok"])

    async def first_research_turn(self):
        path = self.run.run_dir / "work/genomics-analyst/results/tables/evidence.csv"
        path.parent.mkdir(parents=True)
        path.write_text("target,score\nPCSK9,0.9\n")
        self.audit.capture("genomics-analyst")
        self.assertTrue(record_claims([self.claim(kind="table", path=str(path.relative_to(self.run.run_dir)))])["ok"])
        turns = [{"turn": 1, "prompt": "Assess PCSK9", "response": "Recorded finding [[claim:C1]]"}]
        _, coverage = self.audit.finish_turn(turns, 0)
        self.assertTrue(coverage["ok"], coverage)
        return turns

    async def test_uncited_research_followup_fails_despite_prior_valid_claims(self):
        from src.utils.verify import verify_integrity

        turns = await self.first_research_turn()
        self.audit.begin_turn("Compare to LPA", 2)
        await self.hook("PreToolUse", tool_name="WebSearch", tool_use_id="followup", tool_input={"query": "LPA"})
        await self.hook("PostToolUse", tool_name="WebSearch", tool_use_id="followup", tool_input={"query": "LPA"},
                        tool_response="Search results")
        turns.append({"turn": 2, "prompt": "Compare to LPA", "response": "New uncited findings"})
        _, coverage = self.audit.finish_turn(turns, 0)
        self.assertFalse(coverage["ok"])
        self.assertTrue(any(p["kind"] == "missing_turn_claim_references" and p["turn"] == 2
                            for p in coverage["problems"]))
        stored = json.loads((self.run.run_dir / "logs/cost_report.json").read_text())
        self.assertEqual([t["audit_required"] for t in stored["turns"]], [True, True])
        self.assertFalse(verify_integrity(self.run.run_dir)["ok"])

    async def test_greeting_after_research_does_not_require_new_citations(self):
        from src.utils.verify import verify_integrity

        turns = await self.first_research_turn()
        self.audit.begin_turn("Thank you", 2)
        turns.append({"turn": 2, "prompt": "Thank you", "response": "You're welcome"})
        _, coverage = self.audit.finish_turn(turns, 0)
        self.assertTrue(coverage["ok"], coverage)
        self.assertEqual(self.run.data["config"]["research_turns"], [1])
        self.assertFalse(turns[1]["audit_required"])
        self.assertTrue(verify_integrity(self.run.run_dir)["ok"])

    async def test_research_followup_can_reuse_a_valid_prior_claim(self):
        turns = await self.first_research_turn()
        self.audit.begin_turn("Review the previous analysis", 2)
        await self.hook("SubagentStart", agent_id="review", agent_type="genomics-analyst")
        await self.hook("SubagentStop", agent_id="review", agent_type="genomics-analyst")
        turns.append({"turn": 2, "prompt": "Review the previous analysis", "response": "The earlier finding remains supported [[claim:C1]]"})
        claims, coverage = self.audit.finish_turn(turns, 0)
        self.assertTrue(coverage["ok"], coverage)
        self.assertEqual(len(claims.claims), 1)
        self.assertEqual(self.run.data["config"]["research_turns"], [1, 2])

    async def test_new_analysis_files_make_followup_research_without_tool_hooks(self):
        turns = await self.first_research_turn()
        self.audit.begin_turn("Generate a comparison", 2)
        output = self.run.run_dir / "work/genomics-analyst/results/tables/comparison.csv"
        output.write_text("target,score\nLPA,0.8\n")
        turns.append({"turn": 2, "prompt": "Generate a comparison", "response": "Uncited comparison"})
        _, coverage = self.audit.finish_turn(turns, 0)
        self.assertFalse(coverage["ok"])
        self.assertTrue(turns[1]["audit_required"])

    async def test_in_progress_followup_cannot_pass_using_the_prior_saved_report(self):
        from src.utils.verify import verify_integrity

        await self.first_research_turn()
        self.audit.begin_turn("Start the next analysis", 2)
        report = verify_integrity(self.run.run_dir)
        self.assertFalse(report["ok"])
        self.assertTrue(any(p["kind"] == "unfinished_run" for p in report["problems"]))

    async def test_malformed_turn_metadata_reports_incomplete_without_crashing(self):
        from src.utils.verify import verify_integrity

        await self.first_research_turn()
        cost_path = self.run.run_dir / "logs/cost_report.json"
        cost_path.write_text(json.dumps({"turns": [{"turn": [1], "audit_required": True, "response": []}]}))
        report = verify_integrity(self.run.run_dir)
        self.assertFalse(report["ok"])
        self.assertTrue(any(p["kind"] == "invalid_turn_record" for p in report["problems"]))


class TestScientificFilenames(unittest.TestCase):
    def test_root_harness_aliases_are_excluded_but_nested_reports_are_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = RunManifest.create(Path(tmp))
            names = ("README.md", "run.sh", "environment.yml", "session_report.json", "trace.jsonl", "transcript.md")
            nested = run.run_dir / "work/genomics-analyst/results/reports"
            nested.mkdir(parents=True)
            for name in names:
                (run.run_dir / name).write_text("Harness bookkeeping")
                (nested / name).write_text("Scientific analysis output")
            for directory in (".private", ".cache", "__pycache__"):
                excluded = nested / directory / "cached.txt"
                excluded.parent.mkdir()
                excluded.write_text("Cache")
            expected = {str((nested / name).relative_to(run.run_dir)) for name in names}
            self.assertEqual(set(snapshot_dir(run.run_dir)), expected)
            run.scan()
            self.assertEqual(set(run.data["artifacts"]), expected)
            run.write()
            # Root aliases can change on later turns without becoming evidence.
            audit = SessionAudit(run, TraceLogger())
            for name in names:
                (run.run_dir / name).write_text("Updated bookkeeping")
            audit.capture()
            self.assertEqual(set(RunManifest.load(run.run_dir).data["artifacts"]), expected)


class TestScopedMCPEnvironment(unittest.TestCase):
    def test_runs_have_distinct_output_paths_without_mutating_global_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = {"drug": {"command": "python", "args": ["server.py"], "env": {"CUSTOM": "value"}}}
            before = dict(os.environ)
            left = scoped_mcp_servers(original, Path(tmp) / "first")
            right = scoped_mcp_servers(original, Path(tmp) / "second")
            self.assertEqual(os.environ, before)
            self.assertEqual(original["drug"]["env"], {"CUSTOM": "value"})
            self.assertNotEqual(left["drug"]["env"]["VBT_RUN_DIR"], right["drug"]["env"]["VBT_RUN_DIR"])
            self.assertNotEqual(left["drug"]["env"]["MCP_OUTPUT_DIR"], right["drug"]["env"]["MCP_OUTPUT_DIR"])
            self.assertTrue(Path(left["drug"]["env"]["MCP_OUTPUT_DIR"]).is_dir())


if __name__ == "__main__":
    unittest.main()
