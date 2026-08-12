"""
Regression tests for bugs found by actually running the system.

These passed every unit test and only showed up on a live run, which is why
they get a file of their own — they are the kind that come back quietly.

1. The end-of-run sweep recorded the run's own bookkeeping (`logs/trace.jsonl`,
   `logs/cost_report.json`) as artifacts "produced by the CSO". Circular, and it
   padded every manifest with files no reviewer wants to audit.

2. Invoking `run_vbt.py` without `run.sh` produced "Not logged in · Please run
   /login" from the SDK, which says nothing about the real cause: `.env` had not
   been loaded, so `ANTHROPIC_API_KEY` was unset.

3. `runs/INDEX.md` linked each row as ``[run_id](run_id/README.md)`` — but
   ``run_id`` came from *inside* `MANIFEST.json`, not the directory's actual
   name. Copying a recorded run directory into `runs/<other-name>/` (exactly what
   the test fixture below does) left the manifest's internal id unchanged, so
   the generated link pointed at a directory that does not exist. Found by
   building the Past Runs "Report" link feature and clicking it.

Run:  python tests/test_regressions.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.utils.run_manifest import (  # noqa: E402
    RESERVED_DIRS, RunManifest, diff_snapshots, snapshot_dir,
)
from src.utils.run_report import render_audit_html, render_readme  # noqa: E402


class TestBookkeepingIsNotAnArtifact(unittest.TestCase):
    """Bug 1: an artifact is something an agent produced, not the run's own ledger."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = RunManifest.create(Path(self.tmp.name) / "runs", query="q")
        # Exactly what the harness writes during a real run.
        (self.m.run_dir / "logs" / "trace.jsonl").write_text('{"type":"tool_start"}\n')
        (self.m.run_dir / "logs" / "cost_report.json").write_text('{"turns":[]}')
        (self.m.run_dir / "logs" / "transcript.md").write_text("# session")
        (self.m.run_dir / "inputs" / "query.txt").write_text("q")
        (self.m.run_dir / "evidence" / "claims.json").write_text('{"claims":[]}')
        (self.m.run_dir / "report" / "FINAL_REPORT.md").write_text("# report")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_records_no_harness_files(self):
        self.m.scan()
        self.assertEqual(self.m.data["artifacts"], {},
                         "harness bookkeeping must never be recorded as an artifact")

    def test_every_reserved_dir_is_excluded(self):
        for d in RESERVED_DIRS:
            p = self.m.run_dir / d / "stray_harness_like.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}")
        self.m.scan()
        for key in self.m.data["artifacts"]:
            self.assertNotIn(Path(key).parts[0], RESERVED_DIRS, key)

    def test_agent_output_is_still_recorded(self):
        """The fix must not throw out what we actually want."""
        d = self.m.agent_dir("genomics-analyst")
        (d / "results" / "tables" / "gwas.csv").write_text("rsid,p\nrs1,1e-8\n")
        self.m.scan()
        self.assertIn("work/genomics-analyst/results/tables/gwas.csv",
                      self.m.data["artifacts"])

    def test_stray_file_at_run_root_is_still_recorded(self):
        (self.m.run_dir / "loose_output.csv").write_text("a,b\n1,2\n")
        self.m.scan()
        self.assertIn("loose_output.csv", self.m.data["artifacts"])

    def test_agent_subdirectory_named_logs_is_not_excluded(self):
        """Only the *top-level* reserved dirs are the harness's."""
        d = self.m.agent_dir("single-cell-analyst")
        p = d / "logs" / "qc.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("ok")
        self.m.scan()
        self.assertIn("work/single-cell-analyst/logs/qc.log", self.m.data["artifacts"])

    def test_snapshot_ignores_harness_dirs_so_the_hook_does_not_fire(self):
        """The live capture hook diffs snapshots; it must not react to log writes."""
        before = snapshot_dir(self.m.run_dir)
        (self.m.run_dir / "logs" / "trace.jsonl").write_text('{"type":"tool_end"}\n' * 50)
        after = snapshot_dir(self.m.run_dir)
        self.assertEqual(diff_snapshots(before, after), [])

    def test_snapshot_still_sees_agent_writes(self):
        d = self.m.agent_dir("genomics-analyst")
        before = snapshot_dir(self.m.run_dir)
        (d / "results" / "tables" / "new.csv").write_text("x")
        after = snapshot_dir(self.m.run_dir)
        self.assertIn("work/genomics-analyst/results/tables/new.csv",
                      diff_snapshots(before, after))

    def test_misplaced_agent_file_is_reported_not_silently_dropped(self):
        """Excluding a directory must not become a way to lose an artifact."""
        p = self.m.run_dir / "report" / "my_analysis.csv"
        p.write_text("a,b\n1,2\n")
        self.m.scan()
        self.assertNotIn("report/my_analysis.csv", self.m.data["artifacts"])
        self.assertIn("report/my_analysis.csv", self.m.data.get("misplaced_files", []))

    def test_misplaced_files_surface_in_both_reports(self):
        (self.m.run_dir / "logs" / "agent_wrote_this.csv").write_text("x")
        self.m.scan()
        md = render_readme(self.m)
        self.assertIn("Misplaced files", md)
        self.assertIn("logs/agent_wrote_this.csv", md)
        html = render_audit_html(self.m)
        self.assertIn("Misplaced files", html)

    def test_no_misplaced_section_when_clean(self):
        self.m.scan()
        self.assertNotIn("Misplaced files", render_readme(self.m))


class TestApiKeyDiagnostic(unittest.TestCase):
    """Bug 2: a missing API key must say so, not surface as 'Not logged in'."""

    def _run(self, args, env):
        return subprocess.run(
            [sys.executable, str(REPO / "run_vbt.py")] + args,
            capture_output=True, text=True, timeout=120, cwd=str(REPO), env=env,
        )

    def _clean_env(self, **over):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env.update(over)
        return env

    def _run_vbt_module(self):
        sys.path.insert(0, str(REPO))
        import importlib
        return importlib.import_module("run_vbt")

    def test_missing_key_exits_with_an_actionable_message(self):
        """The whole point of the fix: name the actual cause."""
        rv = self._run_vbt_module()
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        import contextlib, io
        err = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                missing = Path(tmp) / "no-such.env"
                with contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as cm:
                        rv._require_api_key(env_file=missing)
                self.assertEqual(cm.exception.code, 2)
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved
        msg = err.getvalue()
        self.assertIn("ANTHROPIC_API_KEY", msg)
        self.assertIn("run.sh", msg)
        self.assertIn("Not logged in", msg,
                      "the message should connect itself to the symptom users see")

    def test_blank_key_in_dotenv_is_treated_as_missing(self):
        rv = self._run_vbt_module()
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        import contextlib, io
        try:
            with tempfile.TemporaryDirectory() as tmp:
                blank = Path(tmp) / ".env"
                blank.write_text("ANTHROPIC_API_KEY=\n")
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        rv._require_api_key(env_file=blank)
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved

    def test_key_already_in_environment_is_accepted(self):
        rv = self._run_vbt_module()
        saved = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        try:
            rv._require_api_key(env_file=Path("/nonexistent"))  # must not raise
        finally:
            if saved is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = saved

    def test_key_is_picked_up_from_dotenv(self):
        env_file = REPO / ".env"
        # Mirror the loader's own condition: a line that *starts with* the key
        # and carries a non-empty value. A substring test also matches a
        # commented-out placeholder — an ordinary thing to keep in a .env —
        # and then this errors out instead of skipping.
        if not env_file.exists() or not any(
            line.startswith("ANTHROPIC_API_KEY=") and line.split("=", 1)[1].strip()
            for line in env_file.read_text().splitlines()
        ):
            self.skipTest("no .env in this checkout, or it defines no key")
        sys.path.insert(0, str(REPO))
        import importlib
        rv = importlib.import_module("run_vbt")
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            rv._require_api_key()
            self.assertTrue(os.environ.get("ANTHROPIC_API_KEY"),
                            "the key in .env should have been loaded")
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved

    def test_verify_and_list_do_not_require_a_key(self):
        """Audit commands must keep working with no credentials at all."""
        with tempfile.TemporaryDirectory() as tmp:
            env = self._clean_env(VBT_RUNS_DIR=tmp)
            r = self._run(["list"], env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("ANTHROPIC_API_KEY", r.stderr)


class TestRunIndexLinksTheActualDirectory(unittest.TestCase):
    """A run copied into a differently-named directory must still link right."""

    def test_index_link_uses_directory_name_not_manifest_run_id(self):
        from src.utils.run_index import render_index_md, render_index_html

        row = {
            "run_id": "20260730-131417-original-internal-id",
            "path": "/runs/some-renamed-copy",
            "query": "q", "created": "2026-07-30T00:00:00", "agents": [],
            "n_artifacts": 0, "n_claims": 0, "cost_usd": None, "has_audit": True,
        }
        md = render_index_md([row])
        self.assertIn("(some-renamed-copy/README.md)", md)
        self.assertNotIn("(20260730-131417-original-internal-id/README.md)", md)

        html = render_index_html([row])
        self.assertIn("/file=/runs/some-renamed-copy/audit.html", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
