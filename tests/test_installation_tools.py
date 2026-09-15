"""Reference validation and streamed Tahoe preparation, with no model requests."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config.datasets import OPEN_TARGETS_DATASETS
from src.config.models import model_argument
from tools import doctor
from tools.download_open_targets import BASE, RELEASE


class ReferenceChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.entries = {}
        for dataset in OPEN_TARGETS_DATASETS:
            path = self.root / dataset / "part.parquet"
            path.parent.mkdir()
            path.write_bytes(b"PAR1test\x04\x00\x00\x00PAR1")
            self.entries[f"{dataset}/part.parquet"] = {"bytes": path.stat().st_size}

    def manifest(self, **overrides):
        value = {"release": RELEASE, "base": BASE, "complete": True,
                 "expected_files": len(self.entries), "files": self.entries, **overrides}
        (self.root / ".download-manifest.json").write_text(json.dumps(value))

    def test_complete_layout_and_manifest_pass_without_loading_tables(self):
        self.manifest()
        self.assertEqual(set(doctor.reference_files(str(self.root))), set(OPEN_TARGETS_DATASETS))

    def test_missing_dataset_partial_transfer_and_bad_size_fail(self):
        path = self.root / "target/part.parquet"
        path.unlink()
        with self.assertRaisesRegex(ValueError, "Missing dataset"):
            doctor.reference_files(str(self.root))
        path.write_bytes(b"PAR1test\x04\x00\x00\x00PAR1")
        partial = self.root / "target/pending.parquet.part"
        partial.touch()
        with self.assertRaisesRegex(ValueError, "Partial downloads"):
            doctor.reference_files(str(self.root))
        partial.unlink()
        self.manifest()
        path.write_bytes(path.read_bytes() + b"extra")
        with self.assertRaisesRegex(ValueError, "File size differs"):
            doctor.reference_files(str(self.root))

    def test_incomplete_or_wrong_release_manifest_is_not_ready(self):
        for changes in ({"complete": False}, {"release": "wrong"}, {"expected_files": 999}):
            self.manifest(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                doctor.reference_files(str(self.root))

    def test_future_model_ids_are_preserved_without_a_model_catalog_request(self):
        self.assertEqual(model_argument("claude-future-5-20270101"), "claude-future-5-20270101")
        self.assertEqual(model_argument("Opus 4.6"), "claude-opus-4-6")

    def test_mcp_paths_must_exist_and_be_absolute(self):
        script = self.root / "server.py"
        script.touch()
        servers = {name: {"command": sys.executable, "args": [str(script)]}
                   for name in doctor.MCP_SERVERS}
        config = self.root / "mcp_config.json"
        config.write_text(json.dumps({"mcpServers": servers}))
        self.assertEqual(len(doctor.mcp_configuration(self.root)), 12)
        for command, args in (("python", [str(script)]),
                              (sys.executable, [str(self.root / "missing.py")]),
                              (sys.executable, ["server.py"])):
            servers["target"] = {"command": command, "args": args}
            config.write_text(json.dumps({"mcpServers": servers}))
            with self.subTest(command=command, args=args), self.assertRaisesRegex(ValueError, "setup_mcp.py"):
                doctor.mcp_configuration(self.root)


@unittest.skipUnless(importlib.util.find_spec("pyarrow"), "requires the application environment")
class TahoePreparation(unittest.TestCase):
    def setUp(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.shards = self.source / "metadata/pseudobulk_differential_expression"
        self.shards.mkdir(parents=True)
        # Include exact cutoffs, missing/non-finite values, and a negative FC.
        self.table = pa.table({
            "gene_name": [f"g{i}" for i in range(10)], "drug": ["drug"] * 10,
            "Cell_ID_DepMap": ["ACH-test"] * 10, "baseMean": [10.0] * 10,
            "padj": [0.01, 0.049, 0.05, 0.099, 0.10, None, float("nan"), -0.1, 0.01, 0.01],
            "log2FoldChange": [-0.6, 0.5, 0.8, 0.9, 1.0, 1.0, 1.0, 1.0, float("inf"), 0.6],
        })
        pq.write_table(self.table.slice(0, 5), self.shards / "part-0.parquet")
        pq.write_table(self.table.slice(5), self.shards / "part-1.parquet")
        for kind in ("gene", "drug", "cell_line", "sample"):
            pq.write_table(pa.table({"metadata": [kind]}), self.source / "metadata" / f"{kind}_metadata.parquet")

    def test_multiple_shards_batches_and_filter_boundaries(self):
        import pyarrow.parquet as pq
        from tools.prepare_tahoe import prepare
        destination = self.root / "prepared"
        with contextlib.redirect_stdout(io.StringIO()):
            result = prepare(self.source, destination, "test-revision", batch_size=2)
        self.assertEqual(result["rows"], {"source": 10, "permissive": 5, "significant": 3, "high_quality": 2})
        for name, expected in (("tahoe_permissive_padj010.parquet", {"g0", "g1", "g2", "g3", "g9"}),
                               ("pseudobulk_de_significant", {"g0", "g1", "g9"}),
                               ("pseudobulk_de_high_quality", {"g0", "g9"})):
            self.assertEqual(set(pq.read_table(destination / name)["gene_name"].to_pylist()), expected)
        self.assertEqual((destination / "metadata/gene_metadata.parquet").read_bytes(),
                         (self.source / "metadata/gene_metadata.parquet").read_bytes())
        self.assertEqual(json.loads((destination / "preparation_manifest.json").read_text())["source_revision"], "test-revision")

    def test_bad_source_schema_does_not_publish_partial_output(self):
        import pyarrow.parquet as pq
        from tools.prepare_tahoe import prepare
        pq.write_table(self.table.drop(["padj"]), self.shards / "part-1.parquet")
        destination = self.root / "prepared"
        with self.assertRaisesRegex(ValueError, "Inconsistent DE schema"):
            prepare(self.source, destination, "test-revision")
        self.assertFalse(destination.exists())

    def test_existing_destination_is_not_overwritten(self):
        from tools.prepare_tahoe import prepare
        destination = self.root / "existing"
        destination.mkdir()
        original = destination / "keep.txt"
        original.write_text("keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare(self.source, destination, "test-revision")
        self.assertEqual(original.read_text(), "keep")

    def test_interrupted_preparation_cleans_up_staged_outputs(self):
        from tools.prepare_tahoe import prepare
        destination = self.root / "prepared"
        with patch("tools.prepare_tahoe.shutil.copy2", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            prepare(self.source, destination, "test-revision")
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".prepared.preparing-*")), [])
        self.assertTrue((self.shards / "part-0.parquet").is_file())


if __name__ == "__main__":
    unittest.main()
