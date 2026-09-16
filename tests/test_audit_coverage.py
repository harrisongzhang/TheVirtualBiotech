"""Evidence completeness must not be inferred from unchanged artifact hashes."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.utils.claims import ClaimSet, validate_claims  # noqa: E402
from src.utils.provenance import Provenance  # noqa: E402
from src.utils.run_manifest import RunManifest  # noqa: E402
from src.utils.run_report import render_audit_html, render_readme  # noqa: E402
from src.utils.verify import format_report, verify_integrity  # noqa: E402
from tools.audit_run import audit_session  # noqa: E402


class TestEvidenceCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = RunManifest.create(
            self.root / "runs", query="Evaluate PCSK9", config={"audit_required": True}
        )
        self.run = self.manifest.run_dir
        self.claims_path = self.run / "evidence" / "claims.json"
        self.final_path = self.run / "report" / "FINAL_REPORT.md"
        self.final_path.write_text("The recorded association supports the target. [[claim:C1]]")
        self.table = self.manifest.agent_dir("genomics-analyst") / "results" / "tables" / "result.csv"
        self.table.write_text("target,score\nPCSK9,0.8\n")
        self.artifact = self.manifest.add_artifact(self.table, produced_by="genomics-analyst")
        self.raw_claim = {
            "id": "C1", "text": "The recorded association supports the target.",
            "evidence": [{"kind": "table", "path": self.artifact["path"]}],
        }
        result = validate_claims([self.raw_claim], self.manifest)
        self.claims = ClaimSet(result.claims)
        self.claims.link_into_manifest(self.manifest)
        self.claims.write(self.claims_path)
        self.manifest.finalize()
        self.manifest.write()
        (self.run / "README.md").write_text("# Run")

    def verify(self):
        return verify_integrity(self.run)

    def assertIncomplete(self, report, kind):
        self.assertTrue(report["integrity"]["ok"], report)
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["evidence"]["status"], "incomplete")
        self.assertIn(kind, {p["kind"] for p in report["problems"]})
        rendered = format_report(report)
        self.assertIn("INCOMPLETE", rendered)
        self.assertNotIn("\nPASS —", rendered)

    def test_complete_research_has_separate_integrity_and_coverage_checks(self):
        report = self.verify()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["integrity"]["ok"])
        self.assertEqual(report["evidence"]["status"], "complete")
        self.assertEqual(report["evidence"]["reference_count"], 1)
        self.assertIn("do not verify scientific correctness", format_report(report))

    def test_zero_claims_is_incomplete_despite_matching_hashes(self):
        ClaimSet().write(self.claims_path)
        report = self.verify()
        self.assertIncomplete(report, "no_claims")
        self.assertEqual(report["checks"]["artifacts"]["failed"], 0)
        self.assertEqual(report["checks"]["claims"]["total"], 0)
        self.assertIn("Claims: 0 filed", format_report(report))

    def test_missing_claims_file_is_incomplete(self):
        self.claims_path.unlink()
        self.assertIncomplete(self.verify(), "no_claims")

    def test_malformed_claims_file_is_reported_without_crashing(self):
        for value in ("{", '{"claims": "not a list"}', '{"claims":[null]}'):
            with self.subTest(value=value):
                self.claims_path.write_text(value)
                report = self.verify()
                self.assertFalse(report["ok"])
                self.assertEqual(report["status"], "incomplete")

    def test_legacy_list_claims_can_be_loaded_and_verified(self):
        self.claims_path.write_text(json.dumps(self.claims.claims))
        self.assertEqual(ClaimSet.load(self.claims_path).claims, self.claims.claims)
        self.assertTrue(self.verify()["ok"])

    def test_claim_set_rejects_invalid_container_type(self):
        self.claims_path.write_text('{"claims": "not a list"}')
        with self.assertRaisesRegex(ValueError, "claims must be a list"):
            ClaimSet.load(self.claims_path)

    def test_tampering_is_an_integrity_failure_even_with_valid_claim_links(self):
        self.table.write_text("target,score\nPCSK9,0.1\n")
        report = self.verify()
        self.assertFalse(report["integrity"]["ok"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "failed")
        self.assertIn("hash_mismatch", {p["kind"] for p in report["problems"]})

    def test_dangling_report_reference_is_incomplete(self):
        self.final_path.write_text("An unrecorded second finding. [[claim:C2]]")
        report = self.verify()
        self.assertIncomplete(report, "dangling_claim_references")
        self.assertEqual(report["evidence"]["dangling_references"], ["C2"])

    def test_filed_claims_without_report_references_are_incomplete(self):
        self.final_path.write_text("The recorded association supports the target.")
        self.assertIncomplete(self.verify(), "missing_claim_references")

    def test_missing_research_response_is_incomplete(self):
        self.final_path.unlink()
        self.assertIncomplete(self.verify(), "missing_final_report")

    def test_unresolvable_evidence_is_incomplete(self):
        del self.manifest.data["artifacts"][self.artifact["path"]]
        self.manifest.write()
        self.assertIncomplete(self.verify(), "claim_unresolvable")

    def test_capture_and_data_source_failures_are_not_hidden_by_valid_claims(self):
        self.manifest.data["config"].update({
            "audit_errors": ["Artifact capture failed: permission denied"],
            "data_source_errors": [{
                "tool_name": "mcp__opentargets__query", "error": "Dataset is missing",
                "tool_use_id": "toolu_1", "turn": 1,
            }],
        })
        self.manifest.write()
        report = self.verify()
        self.assertIncomplete(report, "audit_capture_error")
        self.assertIncomplete(report, "data_source_unavailable")
        self.assertIn("Dataset is missing", format_report(report))

    def test_legacy_research_without_explicit_flag_still_requires_claims(self):
        self.manifest.data["config"].pop("audit_required")
        self.manifest.write()
        ClaimSet().write(self.claims_path)
        self.assertIncomplete(self.verify(), "no_claims")

    def test_greeting_and_administrative_sessions_do_not_require_claims(self):
        for config in ({}, {"audit_required": False}):
            with self.subTest(config=config):
                manifest = RunManifest.create(self.root / "runs", config=config)
                manifest.record_execution([{"agent": "chief-of-staff"},
                                           {"agent": "scientific-reviewer"}])
                manifest.finalize()
                manifest.write()
                (manifest.run_dir / "README.md").write_text("# Greeting")
                (manifest.run_dir / "report" / "FINAL_REPORT.md").write_text(
                    "Hello. Tell me which target or indication you want to investigate."
                )
                report = verify_integrity(manifest.run_dir)
                self.assertTrue(report["ok"], report)
                self.assertEqual(report["evidence"]["status"], "not_required")
                self.assertIn("Claims: 0 filed", format_report(report))

    def test_dangling_refs_are_checked_even_if_claims_are_not_required(self):
        self.manifest.data["config"]["audit_required"] = False
        self.manifest.write()
        self.final_path.write_text("A cited finding. [[claim:missing]]")
        self.assertIncomplete(self.verify(), "dangling_claim_references")

    def test_external_citation_remains_identified_as_not_locally_verified(self):
        claim = dict(self.raw_claim, evidence=[{"kind": "citation", "pmid": "12345678"}])
        result = validate_claims([claim], self.manifest)
        ClaimSet(result.claims).write(self.claims_path)
        report = self.verify()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"]["claims"]["without_verified_evidence"], 1)
        self.assertIn("external citations", format_report(report))

    def test_both_run_reports_display_zero_claims_and_incomplete_audit(self):
        for render in (render_readme, render_audit_html):
            with self.subTest(render=render.__name__):
                output = render(self.manifest, claim_set=ClaimSet())
                self.assertIn("Claims", output)
                self.assertIn("Evidence audit incomplete", output)
                self.assertIn("no claims were filed", output)
                self.assertIn("scientific correctness", output)

    def test_report_does_not_describe_all_tool_errors_as_recovered(self):
        provenance = Provenance([
            {"type": "tool_error", "tool_name": "lookup", "tool_use_id": "bad",
             "error": "Missing dataset", "ts": "2026-09-16T12:00:00"},
        ])
        output = render_audit_html(self.manifest, provenance, self.claims)
        self.assertIn("recorded failures", output)
        self.assertNotIn("recovered in-run", output)

    def test_verify_cli_returns_nonzero_for_incomplete_research(self):
        ClaimSet().write(self.claims_path)
        process = subprocess.run(
            [sys.executable, str(REPO / "run_vbt.py"), "verify", str(self.run), "--json"],
            cwd=REPO, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertIncomplete(report, "no_claims")

    def test_reconstruction_preserves_empty_claim_ledger_without_inventing_reason(self):
        session = self.root / "historical-session"
        session.mkdir()
        (session / "result.csv").write_text("target,score\nPCSK9,0.8\n")
        report = audit_session(session, self.root / "audits", copy_artifacts=True)
        run = Path(report["run_dir"])
        self.assertEqual(ClaimSet.load(run / "evidence" / "claims.json").claims, [])
        readme = (run / "README.md").read_text()
        self.assertIn("Evidence audit incomplete", readme)
        self.assertNotIn("New runs record them automatically", readme)
        self.assertNotIn("mechanism did not exist", readme)
        self.assertIncomplete(verify_integrity(run), "no_claims")

    def test_default_reconstruction_preserves_original_artifacts_without_copying(self):
        session = self.root / "historical-session"
        session.mkdir()
        original = session / "result.csv"
        original.write_text("target,score\nPCSK9,0.8\n")
        report = audit_session(session, self.root / "audits")
        manifest = RunManifest.load(Path(report["run_dir"]))
        self.assertEqual(report["artifacts"], 1)
        self.assertEqual(list(manifest.data["artifacts"]), [str(original)])
        self.assertEqual(manifest.verify(), [])
        self.assertIncomplete(verify_integrity(manifest.run_dir), "no_claims")


class TestFailedToolEvidence(unittest.TestCase):
    def setUp(self):
        self.provenance = Provenance([
            {"type": "tool_start", "tool_name": "query", "tool_use_id": "failed"},
            {"type": "tool_end", "tool_name": "query", "tool_use_id": "failed",
             "is_error": True},
        ])
        self.claim = {
            "id": "C1", "text": "There are nine approved therapies.",
            "evidence": [{"kind": "tool_call", "tool_use_id": "failed"}],
        }

    def test_failed_tool_cannot_support_a_live_claim(self):
        result = validate_claims([self.claim], provenance=self.provenance)
        self.assertFalse(result.ok)
        self.assertEqual(result.claims, [])
        self.assertTrue(any("failed tool call" in error for error in result.errors))

    def test_historical_failed_pointer_is_retained_as_unverified(self):
        result = validate_claims([self.claim], provenance=self.provenance, strict=False)
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(any("failed tool call" in warning for warning in result.warnings))
        self.assertEqual(result.claims[0]["n_verified"], 0)
        self.assertFalse(result.claims[0]["evidence"][0]["verified"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
