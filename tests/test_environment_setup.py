"""Configuration and local reference checks that do not call a model API."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
HAS_DOTENV = importlib.util.find_spec("dotenv") is not None
HAS_DATA = all(importlib.util.find_spec(name) is not None for name in ("pandas", "pyarrow"))


@unittest.skipUnless(HAS_DOTENV, "python-dotenv is not installed")
class TestReferenceConfiguration(unittest.TestCase):
    def test_reference_validation_does_not_require_model_credentials(self):
        from src.config.env import Config
        with patch.object(Config, "ANTHROPIC_API_KEY", None), \
             patch.object(Config, "OPEN_TARGETS_PATH", "/reference/open_targets"):
            Config.validate()

    def test_reference_path_is_still_required(self):
        from src.config.env import Config
        with patch.object(Config, "OPEN_TARGETS_PATH", None):
            with self.assertRaisesRegex(ValueError, "OPEN_TARGETS_DATA_PATH"):
                Config.validate()

    def test_project_dotenv_is_used_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "project" / "src" / "config"
            package.mkdir(parents=True)
            (package.parent / "__init__.py").touch()
            (package / "__init__.py").touch()
            shutil.copyfile(REPO / "src" / "config" / "env.py", package / "env.py")
            (root / "project" / ".env").write_text(
                'OPEN_TARGETS_DATA_PATH="/reference/with spaces"\nANTHROPIC_API_KEY="file-key"\n'
            )
            other = root / "other"
            other.mkdir()
            (other / ".env").write_text('OPEN_TARGETS_DATA_PATH="/wrong/reference"\n')
            env = dict(os.environ)
            env.pop("OPEN_TARGETS_DATA_PATH", None)
            env["ANTHROPIC_API_KEY"] = "exported-key"
            env.pop("PYTHON_DOTENV_DISABLED", None)
            code = (
                "import json,sys; sys.path.insert(0,sys.argv[1]); "
                "from src.config.env import Config; "
                "print(json.dumps([Config.OPEN_TARGETS_PATH,Config.ANTHROPIC_API_KEY]))"
            )
            result = subprocess.run(
                [sys.executable, "-c", code, str(root / "project")],
                cwd=other, env=env, capture_output=True, text=True, timeout=30, check=True,
            )
            self.assertEqual(json.loads(result.stdout), ["/reference/with spaces", "exported-key"])

    @unittest.skipUnless(HAS_DATA, "Scientific data dependencies are not installed")
    def test_local_parquet_query_works_without_a_model_key(self):
        import pandas as pd
        import src.data.loader as loader
        from src.config.env import Config
        with tempfile.TemporaryDirectory() as temporary:
            dataset = Path(temporary) / "disease"
            dataset.mkdir()
            pd.DataFrame({"id": ["EFO_TEST"], "name": ["Test disease"]}).to_parquet(dataset / "part.parquet")
            with patch.object(Config, "ANTHROPIC_API_KEY", None), \
                 patch.object(Config, "OPEN_TARGETS_PATH", temporary), \
                 patch.object(loader, "_loader", None):
                frame = loader.get_data_loader(preload_all=False).get_dataset("disease")
                self.assertEqual(frame["id"].tolist(), ["EFO_TEST"])


if __name__ == "__main__":
    unittest.main()
