"""
End-to-end test of the run lifecycle without the SDK or a live model.

Simulates what a real session does to its run directory — agents writing into
their own trees, a stray file landing in the wrong place, a trace being recorded,
claims being filed — and asserts the resulting directory is organised, attributed
and internally consistent.

This is the test that actually pins reviewer comment R2.5: it fails if a run
stops being self-describing.

Run:  python tests/test_run_lifecycle.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.claims import ClaimSet, validate_claims          # noqa: E402
from src.utils.provenance import build_provenance               # noqa: E402
from src.utils.run_index import scan_runs, update_index         # noqa: E402
from src.utils.run_manifest import (                            # noqa: E402
    CSO_DIR, RunManifest, diff_snapshots, snapshot_dir,
)
from src.utils.run_report import render_audit_html, render_readme   # noqa: E402
from src.utils.trace_logger import TraceLogger                  # noqa: E402


class TestRunLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name) / "runs"
        self.query = "Safety risks of targeting IL-33 in asthma"
        self.run = RunManifest.create(self.runs, query=self.query)
        self.trace = TraceLogger()
        self.snap = snapshot_dir(self.run.run_dir)

    def tearDown(self):
        self.tmp.cleanup()

    # ── helpers mirroring what gradio_cso_app does ───────────────────

    def _tool_write(self, agent, rel, content, tool="Write"):
        """One agent tool call that writes a file, captured as the app captures it."""
        tuid = f"toolu_{agent[:6]}_{Path(rel).stem[:10]}"
        path = self.run.run_dir / "work" / agent / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        self.trace.tool_start(tuid, tool, {"file_path": str(path)}, agent=agent)
        path.write_text(content)
        self.trace.tool_end(tuid, tool, {"file_path": str(path)}, "ok", agent=agent)
        self._capture(agent, tuid)
        return path, tuid

    def _capture(self, agent, tuid):
        after = snapshot_dir(self.run.run_dir)
        for rel in diff_snapshots(self.snap, after):
            parts = Path(rel).parts
            owner = parts[1] if len(parts) >= 2 and parts[0] == "work" else agent
            self.run.add_artifact(self.run.run_dir / rel,
                                  produced_by=owner, tool_use_id=tuid)
        self.snap = after

    def _simulate(self):
        """A two-specialist run with a stray file and an out-of-band write."""
        self.trace.agent_start("a1", "single-cell-analyst")
        self.run.agent_dir("single-cell-analyst")
        script, script_tuid = self._tool_write(
            "single-cell-analyst", "code/scripts/01_expression.py",
            'import pandas as pd\n'
            'df.to_csv(f"{workspace}/il33_celltype_expression.csv")\n'
            'plt.savefig(f"{workspace}/il33_celltype.png")\n',
        )
        # The script runs and writes its own outputs — no tool call names them.
        for name, body in (("il33_celltype_expression.csv", "cell_type,mean\nmast cell,2.4\n"),
                           ("il33_celltype.png", "\x89PNG-not-really")):
            sub = "results/tables" if name.endswith(".csv") else "results/figures"
            p = self.run.run_dir / "work" / "single-cell-analyst" / sub / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        self.trace.agent_stop("a1", "single-cell-analyst", conversation=[
            {"role": "user", "content": "Analyse IL-33 expression by cell type"},
            {"role": "assistant", "tool_calls": [{"id": script_tuid, "name": "Write"}]},
        ])

        self.trace.agent_start("a2", "fda-safety-officer")
        self.run.agent_dir("fda-safety-officer")
        _, safety_tuid = self._tool_write(
            "fda-safety-officer", "results/reports/safety.md", "# Safety\nNo black box.\n")
        self.trace.agent_stop("a2", "fda-safety-officer", conversation=[
            {"role": "user", "content": "Assess IL-33 safety"},
            {"role": "assistant", "tool_calls": [{"id": safety_tuid, "name": "Write"}]},
        ])

        # A stray file dropped in the run root, as a misbehaving agent would.
        (self.run.run_dir / "stray_output.csv").write_text("a,b\n1,2\n")

        (self.run.run_dir / "logs").mkdir(exist_ok=True)
        self.trace.write_jsonl(self.run.run_dir / "logs" / "trace.jsonl")

    # ── tests ────────────────────────────────────────────────────────

    def test_artifacts_land_in_per_agent_trees(self):
        """R2.5: no more one flat pile — every artifact has an owning agent."""
        self._simulate()
        self.run.scan()
        by_agent = self.run.artifacts_by_agent()
        self.assertIn("single-cell-analyst", by_agent)
        self.assertIn("fda-safety-officer", by_agent)
        paths = {e["path"] for e in by_agent["single-cell-analyst"]}
        self.assertIn("work/single-cell-analyst/code/scripts/01_expression.py", paths)
        self.assertIn("work/fda-safety-officer/results/reports/safety.md",
                      {e["path"] for e in by_agent["fda-safety-officer"]})

    def test_stray_root_file_is_still_captured(self):
        """Organisation must not depend on the model obeying its prompt."""
        self._simulate()
        self.run.scan()
        self.assertIn("stray_output.csv", self.run.data["artifacts"])

    def test_script_outputs_are_attributed_to_their_author(self):
        """Files written *by* an agent's script, not by a tool call."""
        self._simulate()
        self.run.scan()
        prov = build_provenance(self.run.run_dir / "logs" / "trace.jsonl")
        code = [self.run.run_dir / p for p, e in self.run.data["artifacts"].items()
                if e["kind"] == "code"]
        prov.index_script_outputs(code)
        hit = prov.attribute_by_script("il33_celltype_expression.csv")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["agent"], "single-cell-analyst")

    def test_provenance_attributes_every_tool_call(self):
        self._simulate()
        prov = build_provenance(self.run.run_dir / "logs" / "trace.jsonl")
        self.assertEqual(prov.specialist_types(),
                         ["fda-safety-officer", "single-cell-analyst"])
        for rec in prov.calls.values():
            self.assertNotEqual(rec["agent"], CSO_DIR, rec)

    def test_claims_link_bidirectionally_and_reject_fabrication(self):
        self._simulate()
        self.run.scan()
        prov = build_provenance(self.run.run_dir / "logs" / "trace.jsonl")
        table = "work/single-cell-analyst/results/tables/il33_celltype_expression.csv"

        r = validate_claims([{
            "id": "C1",
            "text": "IL1RL1 is highly expressed in lung mast cells",
            "confidence": "strong",
            "evidence": [{"kind": "table", "path": table, "note": "row: mast cell"}],
        }], self.run, prov)
        self.assertTrue(r.ok, r.errors)

        cs = ClaimSet(r.claims)
        cs.link_into_manifest(self.run)
        self.assertEqual(self.run.data["artifacts"][table]["cited_by"], ["C1"])

        bad = validate_claims([{
            "id": "C2", "text": "invented",
            "evidence": [{"kind": "table", "path": "never_written.csv"}],
        }], self.run, prov)
        self.assertFalse(bad.ok)

    def test_full_finalization_produces_a_self_describing_run(self):
        """The whole point: open the directory and understand the run."""
        self._simulate()
        self.run.scan()
        prov = build_provenance(self.run.run_dir / "logs" / "trace.jsonl")
        table = "work/single-cell-analyst/results/tables/il33_celltype_expression.csv"
        r = validate_claims([{
            "id": "C1", "text": "IL1RL1 is high in mast cells", "confidence": "strong",
            "evidence": [{"kind": "table", "path": table}],
        }], self.run, prov)
        cs = ClaimSet(r.claims)
        cs.link_into_manifest(self.run)
        cs.write(self.run.run_dir / "evidence" / "claims.json")
        prov.write(self.run.run_dir / "evidence" / "provenance.json")
        self.run.finalize()
        self.run.write()
        (self.run.run_dir / "README.md").write_text(render_readme(self.run, prov, cs))
        (self.run.run_dir / "audit.html").write_text(render_audit_html(self.run, prov, cs))

        for f in ("MANIFEST.json", "README.md", "audit.html",
                  "evidence/claims.json", "evidence/provenance.json",
                  "logs/trace.jsonl"):
            self.assertTrue((self.run.run_dir / f).exists(), f)

        readme = (self.run.run_dir / "README.md").read_text()
        self.assertIn(self.query, readme)
        self.assertIn("single-cell-analyst", readme)
        self.assertIn("C1", readme)

        html = (self.run.run_dir / "audit.html").read_text()
        self.assertIn("IL1RL1 is high in mast cells", html)
        self.assertNotIn("http://", html)     # self-contained
        self.assertNotIn("https://", html)

        self.assertEqual(self.run.verify(), [])

    def test_index_lists_the_run(self):
        self._simulate()
        self.run.scan()
        self.run.finalize()
        self.run.write()
        rows = update_index(self.runs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["query"], self.query)
        self.assertGreater(rows[0]["n_artifacts"], 0)
        self.assertIn("single-cell-analyst", rows[0]["agents"])
        self.assertTrue((self.runs / "INDEX.md").exists())
        self.assertIn(self.query, (self.runs / "INDEX.md").read_text())

    def test_run_id_carries_the_query(self):
        self.assertIn("safety-risks-of-targeting", self.run.run_id)

    def test_manifest_is_valid_json_and_complete(self):
        self._simulate()
        self.run.scan()
        self.run.write()
        data = json.loads((self.run.run_dir / "MANIFEST.json").read_text())
        self.assertEqual(data["query"], self.query)
        for key, e in data["artifacts"].items():
            self.assertEqual(len(e["sha256"]), 64, key)
            self.assertTrue(e["produced_by"], key)
            self.assertIn("kind", e)


if __name__ == "__main__":
    unittest.main(verbosity=2)
