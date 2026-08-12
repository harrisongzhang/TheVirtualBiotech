"""
Tests for the workflow manager (plan-then-verify) and run verification.

Together these cover the two halves of reviewer comment R2.5 that are not about
file layout: that the *sequence* of analyses is declared and checkable, and that
a finished run can be verified rather than taken on trust.

Run:  python tests/test_plan_and_verify.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.claims import ClaimSet, validate_claims        # noqa: E402
from src.utils.plan_runner import (                           # noqa: E402
    reconcile, render_plan_md, validate_plan,
)
from src.utils.run_manifest import RunManifest                # noqa: E402
from src.utils.verify import (                                # noqa: E402
    format_report, rerun_scripts, verify_integrity, verify_run,
)


def _steps():
    return [
        {"id": "s1", "agent": "single-cell-analyst", "task": "expression",
         "depends_on": []},
        {"id": "s2", "agent": "bio-pathways-ppi-analyst", "task": "pathways",
         "depends_on": []},
        {"id": "s3", "agent": "fda-safety-officer", "task": "safety",
         "depends_on": ["s1"]},
        {"id": "s4", "agent": "scientific-reviewer", "task": "review",
         "depends_on": ["s1", "s2", "s3"]},
    ]


class TestPlanValidation(unittest.TestCase):
    def test_valid_plan_yields_an_execution_order(self):
        r = validate_plan(_steps(), goal="Assess IL-33")
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.order, ["s1", "s2", "s3", "s4"])
        self.assertEqual(r.plan["goal"], "Assess IL-33")

    def test_parallel_groups_expose_intended_concurrency(self):
        r = validate_plan(_steps())
        self.assertEqual(r.plan["parallel_groups"], [["s1", "s2"], ["s3"], ["s4"]])

    def test_cycle_is_rejected(self):
        """A plan that cannot execute must not be recordable."""
        r = validate_plan([
            {"id": "a", "agent": "x", "task": "t", "depends_on": ["b"]},
            {"id": "b", "agent": "y", "task": "t", "depends_on": ["a"]},
        ])
        self.assertFalse(r.ok)
        self.assertIn("cycle", r.errors[0])

    def test_self_dependency_is_rejected(self):
        r = validate_plan([{"id": "a", "agent": "x", "task": "t", "depends_on": ["a"]}])
        self.assertFalse(r.ok)
        self.assertIn("itself", r.errors[0])

    def test_unknown_dependency_is_rejected(self):
        r = validate_plan([{"id": "a", "agent": "x", "task": "t",
                            "depends_on": ["ghost"]}])
        self.assertFalse(r.ok)
        self.assertIn("unknown step", r.errors[0])

    def test_duplicate_id_is_rejected(self):
        r = validate_plan([{"id": "a", "agent": "x", "task": "t"},
                           {"id": "a", "agent": "y", "task": "t"}])
        self.assertFalse(r.ok)
        self.assertIn("duplicate", r.errors[0])

    def test_missing_agent_is_rejected(self):
        r = validate_plan([{"id": "a", "task": "t"}])
        self.assertFalse(r.ok)

    def test_empty_plan_is_rejected(self):
        self.assertFalse(validate_plan([]).ok)

    def test_missing_task_is_a_warning_not_an_error(self):
        r = validate_plan([{"id": "a", "agent": "x", "depends_on": []}])
        self.assertTrue(r.ok)
        self.assertTrue(r.warnings)

    def test_accepts_wrapped_dict_form(self):
        r = validate_plan({"goal": "g", "steps": _steps()})
        self.assertTrue(r.ok)
        self.assertEqual(r.plan["goal"], "g")


class TestReconciliation(unittest.TestCase):
    """Deviation is reported, never treated as failure."""

    def setUp(self):
        self.plan = validate_plan(_steps(), goal="Assess IL-33").plan

    def test_exact_match_reports_no_deviation(self):
        execution = [{"agent": s["agent"]} for s in _steps()]
        rep = reconcile(self.plan, execution)
        self.assertEqual(rep["deviations"], [])
        self.assertIn("no deviations", rep["summary"])

    def test_skipped_step_is_reported(self):
        rep = reconcile(self.plan, [{"agent": "single-cell-analyst"}])
        kinds = {d["kind"] for d in rep["deviations"]}
        self.assertIn("not_run", kinds)

    def test_unplanned_agent_is_reported_once(self):
        execution = [{"agent": s["agent"]} for s in _steps()]
        execution += [{"agent": "medchem-pharmacologist"}] * 2
        rep = reconcile(self.plan, execution)
        unplanned = [d for d in rep["deviations"] if d["kind"] == "unplanned"]
        self.assertEqual(len(unplanned), 1)

    def test_out_of_order_dispatch_is_reported(self):
        """s3 depends on s1, so running it first is a real ordering violation."""
        execution = [{"agent": "fda-safety-officer"}, {"agent": "single-cell-analyst"},
                     {"agent": "bio-pathways-ppi-analyst"},
                     {"agent": "scientific-reviewer"}]
        rep = reconcile(self.plan, execution)
        self.assertIn("out_of_order", {d["kind"] for d in rep["deviations"]})

    def test_missing_declared_output_is_reported(self):
        tmp = tempfile.TemporaryDirectory()
        m = RunManifest.create(Path(tmp.name) / "runs")
        plan = validate_plan([{"id": "s1", "agent": "a", "task": "t",
                               "depends_on": [],
                               "expected_outputs": ["never_made.csv"]}]).plan
        rep = reconcile(plan, [{"agent": "a"}], m)
        self.assertIn("missing_output", {d["kind"] for d in rep["deviations"]})
        tmp.cleanup()

    def test_no_plan_is_stated_plainly(self):
        rep = reconcile(None, [{"agent": "a"}])
        self.assertIn("No plan", rep["summary"])
        self.assertEqual(rep["deviations"], [])

    def test_markdown_renders_plan_and_deviations(self):
        rep = reconcile(self.plan, [{"agent": "single-cell-analyst"}])
        md = render_plan_md(self.plan, rep)
        self.assertIn("Assess IL-33", md)
        self.assertIn("scientific-reviewer", md)
        self.assertIn("not_run", md)


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = RunManifest.create(Path(self.tmp.name) / "runs", query="q")
        d = self.m.agent_dir("single-cell-analyst")
        self.table = d / "results" / "tables" / "expr.csv"
        self.table.write_text("cell,mean\nmast,2.4\n")
        self.m.add_artifact(self.table, produced_by="single-cell-analyst")
        r = validate_claims([{
            "id": "C1", "text": "mast highest", "confidence": "strong",
            "evidence": [{"kind": "table",
                          "path": "work/single-cell-analyst/results/tables/expr.csv"}],
        }], self.m, None)
        cs = ClaimSet(r.claims)
        cs.link_into_manifest(self.m)
        cs.write(self.m.run_dir / "evidence" / "claims.json")
        self.m.finalize()
        self.m.write()
        (self.m.run_dir / "README.md").write_text("# run")

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_run_passes(self):
        rep = verify_integrity(self.m.run_dir)
        self.assertTrue(rep["ok"], rep["problems"])
        self.assertEqual(rep["checks"]["artifacts"]["failed"], 0)
        self.assertIn("PASS", format_report(rep))

    def test_tampered_artifact_fails(self):
        """The guarantee that makes a run directory trustworthy after the fact."""
        self.table.write_text("cell,mean\nmast,9.9\n")
        rep = verify_integrity(self.m.run_dir)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["problems"][0]["kind"], "hash_mismatch")
        self.assertIn("FAIL", format_report(rep))

    def test_deleted_artifact_fails(self):
        self.table.unlink()
        rep = verify_integrity(self.m.run_dir)
        self.assertFalse(rep["ok"])
        self.assertIn("missing", {p["kind"] for p in rep["problems"]})

    def test_claim_pointing_at_a_removed_artifact_fails(self):
        """Deleting evidence must invalidate the claim that rests on it."""
        self.table.unlink()
        del self.m.data["artifacts"]["work/single-cell-analyst/results/tables/expr.csv"]
        self.m.write()
        rep = verify_integrity(self.m.run_dir)
        self.assertFalse(rep["ok"])
        self.assertIn("claim_unresolvable", {p["kind"] for p in rep["problems"]})

    def test_directory_without_manifest_is_rejected(self):
        rep = verify_integrity(Path(self.tmp.name))
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["problems"][0]["kind"], "no_manifest")

    def test_rerun_reports_when_there_is_no_code(self):
        out = rerun_scripts(self.m.run_dir)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_scripts"], 0)
        self.assertIn("no Python analysis scripts", out["note"])

    def test_rerun_reproduces_a_deterministic_script(self):
        """The exact half of reproducibility: agent-written code is deterministic."""
        d = self.m.agent_dir("single-cell-analyst")
        script = d / "code" / "scripts" / "01_make.py"
        script.write_text(
            "from pathlib import Path\n"
            "p = Path(__file__).resolve().parents[2] / 'results' / 'tables'\n"
            "p.mkdir(parents=True, exist_ok=True)\n"
            "(p / 'derived.csv').write_text('a,b\\n1,2\\n')\n"
        )
        self.m.add_artifact(script, produced_by="single-cell-analyst")
        import subprocess
        subprocess.run([sys.executable, str(script)], check=True, capture_output=True)
        self.m.add_artifact(d / "results" / "tables" / "derived.csv",
                            produced_by="single-cell-analyst")
        self.m.write()

        rep = verify_run(self.m.run_dir, rerun=True)
        self.assertTrue(rep["ok"], rep["problems"])
        s = rep["rerun"]["scripts"][0]
        self.assertEqual(s["status"], "ok")
        self.assertIn("work/single-cell-analyst/results/tables/derived.csv",
                      s["outputs_matched"])
        self.assertEqual(s["outputs_differed"], [])

    def test_rerun_detects_a_failing_script(self):
        d = self.m.agent_dir("single-cell-analyst")
        script = d / "code" / "scripts" / "01_broken.py"
        script.write_text("raise SystemExit(3)\n")
        self.m.add_artifact(script, produced_by="single-cell-analyst")
        self.m.write()
        rep = verify_run(self.m.run_dir, rerun=True)
        self.assertFalse(rep["ok"])
        self.assertIn("rerun_failed", {p["kind"] for p in rep["problems"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
