"""
Tests for the audit spine: run_manifest, provenance, claims.

Stdlib only — these must run without the biotech conda environment, because the
audit tooling has to be usable on any machine that can read a run directory.

The provenance tests assert against a *real* recorded session rather than a
fixture, so they pin the behaviour that matters: that agent attribution actually
works on the traces this system produces.

Run:  python -m pytest tests/test_audit_spine.py -v
      python tests/test_audit_spine.py          # no pytest required
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.claims import (  # noqa: E402
    ClaimSet, check_refs_resolve, find_claim_refs, validate_claims,
)
from src.utils.provenance import (  # noqa: E402
    build_provenance, extract_written_paths,
)
from src.utils.run_manifest import (  # noqa: E402
    RunManifest, diff_snapshots, new_run_id, slugify, snapshot_dir,
)

#: A real recorded session: "Safety risks of targeting IL-33 in asthma".
#: 4 specialists, 117 tool calls, artifacts written flat into the session root.
#: Set VBT_TEST_SESSION to a recorded session dir to run the provenance tests
#: that assert against real traces; otherwise those tests skip automatically.
REAL_SESSION = Path(os.environ.get("VBT_TEST_SESSION", "/nonexistent"))


class TestRunManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_id_is_sortable_and_readable(self):
        rid = new_run_id("Safety risks of targeting IL-33 in asthma")
        self.assertRegex(rid, r"^\d{8}-\d{6}-[a-z0-9-]+-[0-9a-f]{8}$")
        self.assertIn("safety-risks", rid)
        self.assertEqual(slugify("IL-33 / asthma!!"), "il-33-asthma")
        self.assertEqual(slugify(""), "run")

    def test_create_builds_skeleton(self):
        m = RunManifest.create(self.root, query="test query")
        for sub in ("inputs", "work", "evidence", "logs", "report"):
            self.assertTrue((m.run_dir / sub).is_dir(), sub)
        self.assertTrue((m.run_dir / "MANIFEST.json").exists())

    def test_agent_dir_is_per_agent_not_flat(self):
        """The core fix for R2.5: each specialist gets its own tree."""
        m = RunManifest.create(self.root)
        a = m.agent_dir("single-cell-analyst")
        b = m.agent_dir("genomics-analyst")
        self.assertNotEqual(a, b)
        self.assertTrue((a / "results" / "figures").is_dir())
        self.assertTrue((a / "data" / "processed").is_dir())
        self.assertTrue((a / "code" / "scripts").is_dir())
        self.assertIn("single-cell-analyst", m.data["agents"])

    def test_add_artifact_records_hash_and_attribution(self):
        m = RunManifest.create(self.root)
        f = m.agent_dir("genomics-analyst") / "results" / "tables" / "gwas.csv"
        f.write_text("rsid,pval\nrs1,1e-8\n")
        e = m.add_artifact(f, produced_by="genomics-analyst", tool_use_id="toolu_1")
        self.assertEqual(e["produced_by"], "genomics-analyst")
        self.assertEqual(e["tool_use_id"], "toolu_1")
        self.assertEqual(e["kind"], "table")
        self.assertEqual(len(e["sha256"]), 64)
        self.assertEqual(e["path"], "work/genomics-analyst/results/tables/gwas.csv")

    def test_add_artifact_ignores_missing_file(self):
        m = RunManifest.create(self.root)
        self.assertIsNone(m.add_artifact(m.run_dir / "nope.csv"))

    def test_reregistering_updates_hash_keeps_first_seen(self):
        m = RunManifest.create(self.root)
        f = m.agent_dir("a") / "results" / "tables" / "t.csv"
        f.write_text("v1")
        first = dict(m.add_artifact(f, produced_by="a"))
        f.write_text("v2-longer")
        second = m.add_artifact(f, produced_by="a")
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["produced_at"], second["produced_at"])
        self.assertEqual(len(m.data["artifacts"]), 1)

    def test_scan_attributes_by_directory(self):
        m = RunManifest.create(self.root)
        d = m.agent_dir("fda-safety-officer")
        (d / "results" / "reports" / "safety.md").write_text("# report")
        added = m.scan()
        paths = {e["path"]: e for e in added}
        key = "work/fda-safety-officer/results/reports/safety.md"
        self.assertIn(key, paths)
        self.assertEqual(paths[key]["produced_by"], "fda-safety-officer")

    def test_scan_skips_dotdirs_and_bookkeeping(self):
        m = RunManifest.create(self.root)
        (m.run_dir / ".claude" / "skills").mkdir(parents=True)
        (m.run_dir / ".claude" / "skills" / "s.md").write_text("skill")
        (m.run_dir / "README.md").write_text("generated")
        for e in m.scan():
            self.assertNotIn(".claude", e["path"])
            self.assertNotEqual(e["path"], "README.md")

    def test_verify_detects_tampering_and_deletion(self):
        m = RunManifest.create(self.root)
        f = m.agent_dir("a") / "results" / "tables" / "t.csv"
        f.write_text("original")
        m.add_artifact(f, produced_by="a")
        self.assertEqual(m.verify(), [])

        f.write_text("tampered")
        problems = m.verify()
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["problem"], "hash_mismatch")

        f.unlink()
        self.assertEqual(m.verify()[0]["problem"], "missing")

    def test_roundtrip_load(self):
        m = RunManifest.create(self.root, query="q")
        f = m.agent_dir("a") / "results" / "tables" / "t.csv"
        f.write_text("x")
        m.add_artifact(f, produced_by="a")
        m.finalize()
        m.write()

        m2 = RunManifest.load(m.run_dir)
        self.assertEqual(m2.run_id, m.run_id)
        self.assertEqual(m2.data["status"], "completed")
        self.assertEqual(len(m2.data["artifacts"]), 1)
        self.assertEqual(m2.verify(), [])

    def test_snapshot_diff_detects_new_and_changed(self):
        m = RunManifest.create(self.root)
        d = m.agent_dir("a")
        before = snapshot_dir(m.run_dir)
        (d / "results" / "tables" / "new.csv").write_text("data")
        after = snapshot_dir(m.run_dir)
        changed = diff_snapshots(before, after)
        self.assertIn("work/a/results/tables/new.csv", changed)


class TestProvenanceUnit(unittest.TestCase):
    def test_extract_write_tool_paths(self):
        self.assertEqual(
            extract_written_paths("Write", {"file_path": "/w/out.csv"}), ["/w/out.csv"]
        )
        self.assertEqual(
            extract_written_paths("NotebookEdit", {"notebook_path": "/w/n.ipynb"}),
            ["/w/n.ipynb"],
        )

    def test_extract_bash_redirection_and_flags(self):
        self.assertIn("out.csv", extract_written_paths("Bash", {"command": "cmd > out.csv"}))
        self.assertIn("f.png", extract_written_paths("Bash", {"command": "p --output f.png"}))

    def test_bash_numeric_comparison_is_not_a_path(self):
        """`awk '$3>0.5'` must not be mistaken for a file named 0.5."""
        got = extract_written_paths("Bash", {"command": "awk '$3>0.5' a.tsv > b.csv"})
        self.assertIn("b.csv", got)
        self.assertNotIn("0.5", got)

    def test_bash_python_invocation_links_the_script(self):
        got = extract_written_paths("Bash", {"command": "python analysis.py --flag"})
        self.assertIn("analysis.py", got)

    def test_tool_input_accepts_repr_string(self):
        """Older traces stored tool_input as str(dict); it must still parse."""
        self.assertEqual(
            extract_written_paths("Write", "{'file_path': '/w/a.csv'}"), ["/w/a.csv"]
        )

    def test_empty_input_is_safe(self):
        self.assertEqual(extract_written_paths("Bash", None), [])
        self.assertEqual(extract_written_paths("", {}), [])


@unittest.skipUnless(
    (REAL_SESSION / "trace.jsonl").exists(),
    f"recorded session not readable: {REAL_SESSION}",
)
class TestProvenanceAgainstRealSession(unittest.TestCase):
    """Pins attribution behaviour against a real recorded run."""

    @classmethod
    def setUpClass(cls):
        cls.prov = build_provenance(REAL_SESSION / "trace.jsonl")
        cls.prov.index_script_outputs(sorted(REAL_SESSION.glob("*.py")))

    def test_finds_all_four_specialists(self):
        self.assertEqual(
            self.prov.specialist_types(),
            ["bio-pathways-ppi-analyst", "fda-safety-officer",
             "scientific-reviewer", "single-cell-analyst"],
        )

    def test_attributes_tool_calls_to_specialists(self):
        s = self.prov.summary()
        self.assertEqual(s["n_tool_calls"], 117)
        # 107 was measured; assert >= so an improvement doesn't fail the suite.
        self.assertGreaterEqual(s["n_attributed_to_specialist"], 107)
        self.assertEqual(
            s["n_attributed_to_specialist"] + s["n_cso_tool_calls"], s["n_tool_calls"]
        )

    def test_captures_delegation_prompts(self):
        prompts = [a.get("delegation_prompt") for a in self.prov.agents.values()]
        self.assertTrue(all(prompts), "every sub-agent should have a delegation prompt")

    def test_timeline_is_ordered(self):
        ts = [r["ts"] for r in self.prov.timeline()]
        self.assertEqual(ts, sorted(ts))

    def test_script_outputs_resolve_to_producing_line(self):
        """The evidence link an auditor actually wants: artifact → code line."""
        hit = self.prov.attribute_by_script("il33_bulk_expression.csv")
        self.assertIsNotNone(hit)
        self.assertEqual(Path(hit["script"]).name, "il33_safety_bulk_analysis.py")
        self.assertEqual(hit["agent"], "single-cell-analyst")
        self.assertIn("to_csv", hit["statement"])

    def test_every_written_artifact_is_attributable(self):
        """No analysis artifact in this session should be left unattributed."""
        unattributed = []
        skip = {"environment_full.yml", "trace.jsonl", "cost_report.json",
                "transcript.md", "singlecell_query.log"}
        for f in sorted(REAL_SESSION.iterdir()):
            if not f.is_file() or f.name.startswith(".") or f.name in skip:
                continue
            agent, _ = self.prov.attribute_path(f)
            if not agent:
                hit = self.prov.attribute_by_script(f)
                agent = hit.get("agent") if hit else None
            if not agent:
                agent, conf = self.prov.attribute_by_mtime(f.stat().st_mtime)
            if not agent:
                unattributed.append(f.name)
        self.assertEqual(unattributed, [])


class TestClaimsValidation(unittest.TestCase):
    """The validator is the reason these evidence links mean anything."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = RunManifest.create(Path(self.tmp.name))
        d = self.m.agent_dir("single-cell-analyst")
        self.table = d / "results" / "tables" / "celltype.csv"
        self.table.write_text("cell_type,mean\nmast cell,2.4\n")
        self.m.add_artifact(self.table, produced_by="single-cell-analyst",
                            tool_use_id="toolu_real")
        self.prov = build_provenance(Path(self.tmp.name) / "nonexistent.jsonl")
        self.prov.calls["toolu_real"] = {
            "tool_use_id": "toolu_real", "tool_name": "mcp__single_cell__get_expr",
            "agent": "single-cell-analyst", "started_at": "2026-07-20T14:41:03",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _claim(self, **over):
        c = {
            "id": "C1",
            "text": "IL1RL1 is high in lung mast cells",
            "confidence": "strong",
            "evidence": [{"kind": "table",
                          "path": "work/single-cell-analyst/results/tables/celltype.csv"}],
        }
        c.update(over)
        return c

    def test_valid_claim_is_accepted_and_marked_verified(self):
        r = validate_claims([self._claim()], self.m, self.prov)
        self.assertTrue(r.ok, r.errors)
        ev = r.claims[0]["evidence"][0]
        self.assertTrue(ev["verified"])
        self.assertEqual(ev["produced_by"], "single-cell-analyst")
        self.assertEqual(len(ev["sha256"]), 64)
        self.assertEqual(r.claims[0]["agent"], "single-cell-analyst")

    def test_nonexistent_artifact_is_rejected(self):
        """The central guarantee: an evidence pointer cannot be invented."""
        bad = self._claim(evidence=[{"kind": "table", "path": "made_up_results.csv"}])
        r = validate_claims([bad], self.m, self.prov)
        self.assertFalse(r.ok)
        self.assertTrue(any("not a registered artifact" in e for e in r.errors))

    def test_nonexistent_tool_call_is_rejected(self):
        bad = self._claim(evidence=[{"kind": "tool_call", "tool_use_id": "toolu_fake"}])
        r = validate_claims([bad], self.m, self.prov)
        self.assertFalse(r.ok)
        self.assertTrue(any("does not appear in this run" in e for e in r.errors))

    def test_real_tool_call_is_resolved(self):
        c = self._claim(evidence=[{"kind": "tool_call", "tool_use_id": "toolu_real"}])
        r = validate_claims([c], self.m, self.prov)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.claims[0]["evidence"][0]["tool_name"],
                         "mcp__single_cell__get_expr")

    def test_claim_without_evidence_is_rejected(self):
        r = validate_claims([self._claim(evidence=[])], self.m, self.prov)
        self.assertFalse(r.ok)
        self.assertTrue(any("at least one piece of evidence" in e for e in r.errors))

    def test_basename_reference_resolves(self):
        """Agents cite bare basenames; that should work when unambiguous."""
        c = self._claim(evidence=[{"kind": "table", "path": "celltype.csv"}])
        r = validate_claims([c], self.m, self.prov)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.claims[0]["evidence"][0]["path"],
                         "work/single-cell-analyst/results/tables/celltype.csv")

    def test_duplicate_and_malformed_ids_rejected(self):
        r = validate_claims([self._claim(), self._claim()], self.m, self.prov)
        self.assertTrue(any("duplicate id" in e for e in r.errors))
        r2 = validate_claims([self._claim(id="bad id!")], self.m, self.prov)
        self.assertFalse(r2.ok)

    def test_external_citation_allowed_but_unverified(self):
        """We must not claim to have checked a PMID we never fetched."""
        c = self._claim(evidence=[{"kind": "citation", "pmid": "12345678"}])
        r = validate_claims([c], self.m, self.prov)
        self.assertTrue(r.ok, r.errors)
        self.assertFalse(r.claims[0]["evidence"][0]["verified"])
        self.assertTrue(any("not locally verifiable" in w for w in r.warnings))

    def test_nonstrict_downgrades_errors_for_retrofit(self):
        bad = self._claim(evidence=[{"kind": "table", "path": "missing.csv"}])
        r = validate_claims([bad], self.m, self.prov, strict=False)
        self.assertTrue(r.ok)
        self.assertTrue(r.warnings)
        self.assertFalse(r.claims[0]["evidence"][0]["verified"])

    def test_hash_mismatch_is_caught_by_manifest_verify(self):
        self.table.write_text("tampered after the claim was filed")
        self.assertEqual(self.m.verify()[0]["problem"], "hash_mismatch")


class TestClaimSet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = RunManifest.create(Path(self.tmp.name))
        d = self.m.agent_dir("a")
        f = d / "results" / "tables" / "t.csv"
        f.write_text("x")
        self.m.add_artifact(f, produced_by="a")
        self.key = "work/a/results/tables/t.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def _cs(self):
        r = validate_claims(
            [{"id": "C1", "text": "t", "evidence": [{"kind": "table", "path": self.key}]}],
            self.m, None,
        )
        return ClaimSet(r.claims)

    def test_link_into_manifest_is_bidirectional(self):
        cs = self._cs()
        cs.link_into_manifest(self.m)
        self.assertEqual(self.m.data["artifacts"][self.key]["cited_by"], ["C1"])

    def test_add_replaces_same_id(self):
        cs = self._cs()
        cs.add([{"id": "C1", "text": "revised", "evidence": [], "n_verified": 0,
                 "confidence": "weak", "agent": None}])
        self.assertEqual(len(cs.claims), 1)
        self.assertEqual(cs.by_id("C1")["text"], "revised")

    def test_write_and_load_roundtrip(self):
        cs = self._cs()
        p = cs.write(Path(self.tmp.name) / "claims.json")
        loaded = ClaimSet.load(p)
        self.assertEqual(len(loaded.claims), 1)
        self.assertEqual(loaded.by_id("C1")["text"], "t")
        self.assertEqual(json.loads(p.read_text())["stats"]["n_claims"], 1)

    def test_load_missing_file_is_empty(self):
        self.assertEqual(ClaimSet.load(Path(self.tmp.name) / "nope.json").claims, [])


class TestClaimRefs(unittest.TestCase):
    def test_find_refs_in_prose(self):
        text = "IL1RL1 is high[[claim:C3]] and safe[[claim:C4]]."
        self.assertEqual(find_claim_refs(text), ["C3", "C4"])

    def test_dangling_ref_is_reported(self):
        cs = ClaimSet([{"id": "C3", "text": "t", "evidence": [], "n_verified": 0}])
        self.assertEqual(check_refs_resolve("a[[claim:C3]] b[[claim:C9]]", cs), ["C9"])

    def test_no_refs_is_empty(self):
        self.assertEqual(find_claim_refs("plain prose"), [])
        self.assertEqual(find_claim_refs(None), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
