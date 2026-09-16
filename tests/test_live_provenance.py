"""Exercise live provenance tools across process and workspace boundaries."""

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.mcp_servers.provenance_mcp import tools  # noqa: E402
from src.utils.claims import ClaimSet, validate_claims  # noqa: E402
from src.utils.run_manifest import RunManifest, sha256_file  # noqa: E402
from src.utils.run_storage import write_json_atomic  # noqa: E402
from src.utils.trace_logger import TraceLogger  # noqa: E402
from src.utils.verify import verify_integrity  # noqa: E402


def _claim(index, path):
    return {"id": f"C{index}", "text": f"Recorded finding {index}.",
            "evidence": [{"kind": "table", "path": path}]}


def _plan():
    return [{"id": "s1", "agent": "genomics-analyst", "task": "Assess genetic evidence",
             "depends_on": []}]


def _concurrent_tool_worker(operation, run_dir, cwd, paths, barrier, results):
    """A distinct MCP-like process with an independent module/manifest state."""
    os.environ["VBT_RUN_DIR"] = str(run_dir)
    os.chdir(cwd)
    try:
        barrier.wait(timeout=15)
        for index, path in enumerate(paths):
            if operation == "register":
                result = tools.register_artifact(path, f"Description {index}")
            elif operation == "claim":
                result = tools.record_claims([_claim(index, path)])
            else:
                result = tools.write_plan(_plan(), goal=f"Goal {index}")
            if not result.get("ok"):
                raise AssertionError(result)
        results.put({"operation": operation, "ok": True})
    except Exception as exc:
        results.put({"operation": operation, "ok": False, "error": repr(exc)})


class TestLiveProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = RunManifest.create(self.root / "runs", query="Evaluate PCSK9")
        self.run = self.manifest.run_dir
        self.agent = self.manifest.agent_dir("genomics-analyst")
        self.other_cwd = self.root / "server-cwd"
        self.other_cwd.mkdir()
        self.env = patch.dict(os.environ, {"VBT_RUN_DIR": str(self.run)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _table(self, name="result.csv", content="target,score\nPCSK9,0.8\n"):
        path = self.agent / "results" / "tables" / name
        path.write_text(content)
        return path, str(path.relative_to(self.run))

    def _child(self, source, *args):
        process = subprocess.run(
            [sys.executable, "-c", "import sys\nsys.path.insert(0, sys.argv[1])\n" + source,
             str(REPO), *map(str, args)],
            cwd=self.other_cwd, env=dict(os.environ), capture_output=True,
            text=True, timeout=20,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_pre_turn_plan_resolves_bound_run_from_unrelated_process_cwd(self):
        result = self._child(
            "import json\nfrom src.mcp_servers.provenance_mcp.tools import write_plan\n"
            "print(json.dumps(write_plan(json.loads(sys.argv[2]), goal='Assess PCSK9')))\n",
            json.dumps(_plan()),
        )
        self.assertTrue(result["ok"], result)
        manifest = RunManifest.load(self.run)
        self.assertEqual(manifest.data["plan"]["goal"], "Assess PCSK9")
        self.assertEqual(json.loads((self.run / "inputs" / "plan.json").read_text()),
                         manifest.data["plan"])

    def test_list_discovers_child_output_without_registration(self):
        path, key = self._table()
        self.assertEqual(RunManifest.load(self.run).data["artifacts"], {})
        result = tools.list_artifacts(agent="genomics-analyst", kind="table")
        self.assertTrue(result["ok"], result)
        self.assertEqual([entry["path"] for entry in result["artifacts"]], [key])
        self.assertEqual(RunManifest.load(self.run).data["artifacts"][key]["sha256"],
                         sha256_file(path))

    def test_claims_discover_and_link_unregistered_output_before_turn_end(self):
        path, key = self._table()
        result = tools.record_claims([_claim(1, key)])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["claims"][0]["n_verified"], 1)
        manifest = RunManifest.load(self.run)
        self.assertEqual(manifest.data["status"], "in_progress")
        self.assertEqual(manifest.data["artifacts"][key]["cited_by"], ["C1"])
        self.assertEqual(manifest.data["artifacts"][key]["sha256"], sha256_file(path))
        self.assertEqual(ClaimSet.load(self.run / "evidence" / "claims.json").by_id("C1")
                         ["evidence"][0]["produced_by"], "genomics-analyst")

    def test_completed_tool_is_citable_in_other_process_before_turn_end(self):
        trace = TraceLogger(self.run / "logs" / "trace.jsonl")
        trace.tool_start("toolu_done", "query", {"target": "PCSK9"}, agent="genomics-analyst")
        trace.tool_end("toolu_done", "query", {"target": "PCSK9"},
                       {"success": True, "score": 0.8}, agent="genomics-analyst")
        claim = {"id": "C1", "text": "Recorded association.",
                 "evidence": [{"kind": "tool_call", "tool_use_id": "toolu_done"}]}
        result = self._child(
            "import json\nfrom src.mcp_servers.provenance_mcp.tools import record_claims\n"
            "print(json.dumps(record_claims(json.loads(sys.argv[2]))))\n", json.dumps([claim])
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["claims"][0]["n_verified"], 1)
        self.assertEqual(RunManifest.load(self.run).data["status"], "in_progress")

    def test_started_tool_without_result_cannot_support_a_claim(self):
        trace = TraceLogger(self.run / "logs" / "trace.jsonl")
        trace.tool_start("toolu_pending", "query", {})
        result = tools.record_claims([{
            "id": "C1", "text": "An answer that has not arrived.",
            "evidence": [{"kind": "tool_call", "tool_use_id": "toolu_pending"}],
        }])
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("unfinished" in error for error in result["errors"]))
        self.assertFalse((self.run / "evidence" / "claims.json").exists())

    def test_failed_data_result_cannot_be_cited_as_successful_evidence(self):
        trace = TraceLogger(self.run / "logs" / "trace.jsonl")
        trace.tool_start("toolu_failed", "query", {})
        trace.tool_end("toolu_failed", "query", {},
                       {"success": False, "error": "Missing parquet dataset"})
        result = tools.record_claims([{
            "id": "C1", "text": "Nine approved therapies.",
            "evidence": [{"kind": "tool_call", "tool_use_id": "toolu_failed"}],
        }])
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("failed tool call" in error for error in result["errors"]))

    def test_explicit_other_run_cannot_override_the_bound_run(self):
        other = RunManifest.create(self.root / "runs", query="Another session")
        output = other.agent_dir("genomics-analyst") / "results" / "tables" / "other.csv"
        output.write_text("score\n0.1\n")
        before = (other.run_dir / "MANIFEST.json").read_bytes()
        operations = [
            lambda: tools.write_plan(_plan(), run_dir=str(other.run_dir)),
            lambda: tools.list_artifacts(run_dir=str(other.run_dir)),
            lambda: tools.register_artifact(str(output), "Other session", str(other.run_dir)),
            lambda: tools.record_claims([_claim(1, str(output))], str(other.run_dir)),
        ]
        for operation in operations:
            self.assertFalse(operation()["ok"])
        self.assertEqual((other.run_dir / "MANIFEST.json").read_bytes(), before)
        self.assertFalse((other.run_dir / "evidence" / "claims.json").exists())

    def test_refresh_updates_changed_output_without_losing_metadata(self):
        path, key = self._table()
        manifest = RunManifest.load(self.run)
        initial = dict(manifest.add_artifact(
            path, produced_by="genomics-analyst", tool_use_id="toolu_writer",
            description="Genetic evidence table",
        ))
        manifest.data["artifacts"][key]["cited_by"] = ["C1"]
        manifest.write()
        path.write_text("target,score,source\nPCSK9,0.9,updated\n")
        changed = manifest.scan(refresh_changed=True)
        self.assertEqual([entry["path"] for entry in changed], [key])
        updated = manifest.data["artifacts"][key]
        self.assertNotEqual(updated["sha256"], initial["sha256"])
        self.assertEqual(updated["sha256"], sha256_file(path))
        for field in ("produced_at", "tool_use_id", "description", "produced_by"):
            self.assertEqual(updated[field], initial[field])
        self.assertEqual(updated["cited_by"], ["C1"])
        self.assertEqual(manifest.scan(refresh_changed=True), [])

    def test_registration_of_rewritten_file_refreshes_the_hash(self):
        path, key = self._table()
        self.assertTrue(tools.register_artifact(key, "Original")["ok"])
        original = RunManifest.load(self.run).data["artifacts"][key]["sha256"]
        path.write_text("target,score\nPCSK9,0.95\n")
        self.assertTrue(tools.register_artifact(key, "Updated")["ok"])
        entry = RunManifest.load(self.run).data["artifacts"][key]
        self.assertNotEqual(entry["sha256"], original)
        self.assertEqual(entry["sha256"], sha256_file(path))
        self.assertEqual(entry["description"], "Updated")

    def test_later_turn_cannot_silently_revalidate_claims_against_revised_output(self):
        path, key = self._table()
        self.assertTrue(tools.record_claims([_claim(1, key)])["ok"])
        claims_path = self.run / "evidence" / "claims.json"
        original_claim = ClaimSet.load(claims_path).by_id("C1")
        original_hash = original_claim["evidence"][0]["sha256"]
        (self.run / "README.md").write_text("# Run")
        (self.run / "report" / "FINAL_REPORT.md").write_text("Recorded finding. [[claim:C1]]")
        # The fixture represents the completed first turn, including its saved
        # lifecycle status. An in-progress run is deliberately not verifiable.
        manifest = RunManifest.load(self.run)
        manifest.finalize(status="completed")
        manifest.write()
        self.assertTrue(verify_integrity(self.run)["ok"])

        # The second turn rewrites the same output. Capturing that version is
        # correct, but must leave the first turn's filed evidence version intact.
        path.write_text("target,score\nPCSK9,0.95\n")
        self.assertTrue(tools.list_artifacts()["ok"])
        current_hash = RunManifest.load(self.run).data["artifacts"][key]["sha256"]
        self.assertNotEqual(current_hash, original_hash)
        report = verify_integrity(self.run)
        self.assertTrue(report["integrity"]["ok"], report)
        self.assertEqual(report["evidence"]["status"], "incomplete")
        self.assertFalse(report["ok"])
        self.assertTrue(any("changed since this claim was filed" in problem["detail"]
                            for problem in report["problems"]))
        self.assertFalse(tools.record_claims([original_claim])["ok"])
        self.assertEqual(ClaimSet.load(claims_path).by_id("C1")["evidence"][0]["sha256"],
                         original_hash)

        revised = _claim(1, key)
        revised["text"] = "Revised finding after reviewing the updated output."
        self.assertTrue(tools.record_claims([revised])["ok"])
        self.assertEqual(ClaimSet.load(claims_path).by_id("C1")["evidence"][0]["sha256"],
                         current_hash)
        self.assertTrue(verify_integrity(self.run)["ok"])

    def test_reconstruction_retains_stale_claim_hash_as_unverified(self):
        path, key = self._table()
        self.assertTrue(tools.record_claims([_claim(1, key)])["ok"])
        original = ClaimSet.load(self.run / "evidence" / "claims.json").by_id("C1")
        path.write_text("target,score\nPCSK9,0.95\n")
        self.assertTrue(tools.list_artifacts()["ok"])
        result = validate_claims([original], RunManifest.load(self.run), strict=False)
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(any("changed since this claim was filed" in warning
                            for warning in result.warnings))
        evidence = result.claims[0]["evidence"][0]
        self.assertEqual(evidence["sha256"], original["evidence"][0]["sha256"])
        self.assertFalse(evidence["verified"])

    def test_new_missing_or_outside_artifacts_are_not_registered_or_citable(self):
        outside = self.root / "private.csv"
        outside.write_text("value\nprivate\n")
        link = self.agent / "results" / "tables" / "outside.csv"
        link.symlink_to(outside)
        for path in ("missing.csv", str(outside), str(link.relative_to(self.run))):
            with self.subTest(path=path):
                self.assertFalse(tools.register_artifact(path, "Invalid")["ok"])
                self.assertFalse(tools.record_claims([_claim(1, path)])["ok"])
        self.assertEqual(RunManifest.load(self.run).data["artifacts"], {})

    def test_deleted_registered_evidence_is_rejected_without_erasing_history(self):
        path, key = self._table()
        self.assertTrue(tools.register_artifact(key, "Evidence")["ok"])
        path.unlink()
        result = tools.record_claims([_claim(1, key)])
        self.assertFalse(result["ok"], result)
        self.assertIn(key, RunManifest.load(self.run).data["artifacts"])
        self.assertFalse((self.run / "evidence" / "claims.json").exists())

    def test_registered_path_replaced_by_outside_symlink_is_rejected(self):
        path, key = self._table()
        self.assertTrue(tools.register_artifact(key, "Evidence")["ok"])
        outside = self.root / "outside.csv"
        outside.write_text(path.read_text())
        path.unlink()
        path.symlink_to(outside)
        result = tools.record_claims([_claim(1, key)])
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("outside" in error for error in result["errors"]))
        self.assertIn(key, RunManifest.load(self.run).data["artifacts"])

    def test_hash_change_with_preserved_size_and_timestamp_is_rejected(self):
        path, key = self._table()
        self.assertTrue(tools.register_artifact(key, "Evidence")["ok"])
        original_stat = path.stat()
        path.write_text(path.read_text().replace("0.8", "0.1"))
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        result = tools.record_claims([_claim(1, key)])
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("changed" in error for error in result["errors"]))

    def test_concurrent_processes_preserve_claims_descriptions_and_plan(self):
        paths = [self._table(f"result_{index}.csv")[1] for index in range(16)]
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(3)
        results = context.Queue()
        processes = [context.Process(
            target=_concurrent_tool_worker,
            args=(operation, str(self.run), str(self.other_cwd), paths, barrier, results),
        ) for operation in ("register", "claim", "plan")]
        stop_reading = threading.Event()
        read_errors = []

        def read_records():
            while not stop_reading.is_set():
                for relative in ("MANIFEST.json", "evidence/claims.json", "inputs/plan.json"):
                    path = self.run / relative
                    if path.exists():
                        try:
                            json.loads(path.read_text())
                        except (OSError, ValueError) as exc:
                            read_errors.append((relative, repr(exc)))
                stop_reading.wait(0.002)

        reader = threading.Thread(target=read_records)
        reader.start()
        try:
            for process in processes:
                process.start()
            deadline = time.monotonic() + 25
            for process in processes:
                process.join(timeout=max(0, deadline - time.monotonic()))
                self.assertFalse(process.is_alive(), "Concurrent provenance write timed out")
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=3) for _ in processes]
            self.assertTrue(all(item["ok"] for item in outcomes), outcomes)
        finally:
            stop_reading.set()
            reader.join(timeout=3)
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=3)
            results.close()
            results.join_thread()

        self.assertEqual(read_errors, [], "Readers must never observe partially written JSON")
        manifest = RunManifest.load(self.run)
        self.assertEqual(set(manifest.data["artifacts"]), set(paths))
        claims = ClaimSet.load(self.run / "evidence" / "claims.json")
        self.assertEqual({claim["id"] for claim in claims.claims},
                         {f"C{index}" for index in range(len(paths))})
        for index, path in enumerate(paths):
            entry = manifest.data["artifacts"][path]
            self.assertEqual(entry["description"], f"Description {index}")
            self.assertEqual(entry["cited_by"], [f"C{index}"])
        self.assertEqual(manifest.data["plan"]["goal"], f"Goal {len(paths) - 1}")
        self.assertEqual(json.loads((self.run / "inputs" / "plan.json").read_text()),
                         manifest.data["plan"])

    def test_failed_atomic_update_keeps_the_previous_record(self):
        path = self.root / "ledger.json"
        write_json_atomic(path, {"value": "original"})
        with patch("src.utils.run_storage.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                write_json_atomic(path, {"value": "replacement"})
        self.assertEqual(json.loads(path.read_text()), {"value": "original"})
        self.assertEqual(list(self.root.glob(".ledger.json.*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
