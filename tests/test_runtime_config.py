"""Exercise configuration lookup in the bundled runtime without model requests."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import claude_agent_sdk

from src.utils.runtime_paths import RuntimePaths


@unittest.skipUnless(sys.platform.startswith("linux"), "Runtime process checks require Linux")
class RuntimeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        name = "claude.exe" if os.name == "nt" else "claude"
        cls.cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
        if not cls.cli.is_file():
            raise unittest.SkipTest("Bundled runtime is not installed")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vbt-config-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.test_home = self.root / "home"
        self.test_home.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        # auth status reads local configuration and does not validate the key
        # remotely. Use an isolated child environment with no real credentials.
        self.env = {
            "HOME": str(self.test_home),
            "PATH": os.environ.get("PATH", ""),
            "ANTHROPIC_API_KEY": "test-runtime-config-key",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
        }

    def runtime(self):
        with patch("src.utils.runtime_paths.Path.home", return_value=self.test_home):
            return RuntimePaths(self.workspace, env=self.env, temp_parent=self.root)

    def auth_status(self, runtime, **overrides):
        return subprocess.run(
            [str(self.cli), "auth", "status", "--json"],
            cwd=self.workspace, env={**self.env, **runtime.sdk_env, **overrides},
            capture_output=True, text=True, timeout=20,
        )

    def backup(self, config_dir):
        backup = config_dir / "backups" / ".claude.json.backup.1000000000000"
        backup.parent.mkdir(parents=True)
        backup.write_text('{}\n')
        return backup

    def assert_api_key_ready(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["loggedIn"])
        self.assertEqual(json.loads(result.stdout)["authMethod"], "api_key")
        self.assertEqual(result.stderr, "")

    def test_fresh_api_key_install_needs_no_global_config(self):
        runtime = self.runtime()
        self.assertFalse(runtime.user_config_path.exists())
        self.assert_api_key_ready(self.auth_status(runtime))
        self.assertFalse((runtime.config_dir / ".claude.json").exists())

    def test_existing_default_config_is_used_without_spurious_backup_warning(self):
        config = self.test_home / ".claude.json"
        original = b'{"hasCompletedOnboarding":true,"projects":{}}\n'
        config.write_bytes(original)
        backup = self.backup(self.test_home / ".claude")
        runtime = self.runtime()

        self.assertEqual(config.read_bytes(), original)
        self.assert_api_key_ready(self.auth_status(runtime))
        # The runtime adds its own migration markers; existing values survive.
        saved = json.loads(config.read_text())
        for key, value in json.loads(original).items():
            self.assertEqual(saved[key], value)
        self.assertEqual(backup.read_bytes(), b'{}\n')
        self.assertFalse((runtime.config_dir / ".claude.json").exists())

    def test_explicit_config_override_is_preserved(self):
        custom = self.root / "custom-config"
        self.env["CLAUDE_CONFIG_DIR"] = str(custom)
        self.backup(custom)
        original = b'{"hasCompletedOnboarding":true,"projects":{}}\n'
        (custom / ".claude.json").write_bytes(original)
        # An invalid default must not interfere with the selected custom file.
        (self.test_home / ".claude.json").write_text('{broken')
        runtime = self.runtime()

        self.assertEqual(runtime.sdk_env["CLAUDE_CONFIG_DIR"], str(custom))
        self.assertEqual((custom / ".claude.json").read_bytes(), original)
        self.assert_api_key_ready(self.auth_status(runtime))
        saved = json.loads((custom / ".claude.json").read_text())
        for key, value in json.loads(original).items():
            self.assertEqual(saved[key], value)

    def test_malformed_config_errors_remain_visible(self):
        config = self.test_home / ".claude.json"
        config.write_text('{broken')
        self.backup(self.test_home / ".claude")
        result = self.auth_status(self.runtime())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configuration error", result.stderr)
        self.assertIn("is corrupted", result.stderr)

    def test_genuinely_missing_config_recovery_warning_remains_visible(self):
        backup = self.backup(self.test_home / ".claude")
        result = self.auth_status(self.runtime())

        self.assertIn("Claude configuration file not found", result.stderr)
        self.assertIn(str(backup), result.stderr)
        self.assertIn("You can manually restore it", result.stderr)


if __name__ == "__main__":
    unittest.main()
