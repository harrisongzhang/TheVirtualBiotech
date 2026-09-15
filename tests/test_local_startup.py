"""Local entry-point regressions; no model requests or public share tunnels.

Run with the application environment: python tests/test_local_startup.py
Application-specific checks skip when only the stdlib audit tools are installed.
"""

import asyncio
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import run_vbt

HAS_DOTENV = importlib.util.find_spec("dotenv") is not None
HAS_APP = HAS_DOTENV and all(
    importlib.util.find_spec(name) is not None
    for name in ("gradio", "claude_agent_sdk", "httpx")
)


class TestAuditStartup(unittest.TestCase):
    def test_list_works_without_site_packages_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, VBT_RUNS_DIR=tmp, ANTHROPIC_API_KEY="")
            result = subprocess.run(
                [sys.executable, "-S", str(REPO / "run_vbt.py"), "list"],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ANTHROPIC_API_KEY", result.stderr)

    def test_missing_dotenv_dependency_has_an_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text('ANTHROPIC_API_KEY="file-key"\n')
            code = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import run_vbt; run_vbt._require_api_key(sys.argv[2])"
            )
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            result = subprocess.run(
                [sys.executable, "-S", "-c", code, str(REPO), str(env_file)],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("python-dotenv", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("file-key", result.stderr)


@unittest.skipUnless(HAS_DOTENV, "requires python-dotenv")
class TestApiKeyLoading(unittest.TestCase):
    def test_quoted_exported_and_commented_dotenv_values(self):
        examples = [
            ('ANTHROPIC_API_KEY="test=double"\n', "test=double"),
            ("ANTHROPIC_API_KEY='test=single'\n", "test=single"),
            ('export ANTHROPIC_API_KEY="test-export" # comment\n', "test-export"),
            ('ANTHROPIC_API_KEY=test-unquoted # comment\n', "test-unquoted"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            for contents, expected in examples:
                with self.subTest(contents=contents), patch.dict(os.environ, {}, clear=True):
                    env_file.write_text(contents)
                    run_vbt._require_api_key(env_file)
                    self.assertEqual(os.environ["ANTHROPIC_API_KEY"], expected)

    def test_exported_key_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text('ANTHROPIC_API_KEY="file-key"\n')
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "exported-key"}, clear=True):
                run_vbt._require_api_key(env_file)
                self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "exported-key")

    def test_empty_and_whitespace_values_fail_without_echoing_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            for value in ('""', "''", '"   "'):
                with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                    env_file.write_text(f"ANTHROPIC_API_KEY={value}\n")
                    error = io.StringIO()
                    with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as caught:
                        run_vbt._require_api_key(env_file)
                    self.assertEqual(caught.exception.code, 2)
                    self.assertIn("ANTHROPIC_API_KEY", error.getvalue())

    def test_explicitly_empty_environment_is_not_replaced_by_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text('ANTHROPIC_API_KEY="file-key"\n')
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=True):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    run_vbt._require_api_key(env_file)
                self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "")


@unittest.skipUnless(HAS_DOTENV and os.name == "posix" and shutil.which("bash"),
                     "requires python-dotenv and Bash on a POSIX host")
class TestShellStartup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.caller = self.repo / "caller"
        self.caller.mkdir()
        for name in ("run.sh", "run_vbt.py"):
            shutil.copy2(REPO / name, self.repo / name)
        (self.repo / "activate.local.sh").write_text(
            f"export PATH={shlex.quote(str(Path(sys.executable).parent))}:\"$PATH\"\n"
        )
        (self.repo / "setup_mcp.py").write_text("")
        (self.repo / "mcp_config.json").write_text('{"mcpServers":{"provenance":{}}}')
        # Keep these checks focused on the wrapper's environment diagnostics.
        for name in ("gradio", "claude_agent_sdk", "fastmcp", "pandas"):
            (self.repo / f"{name}.py").write_text("")
        (self.repo / "gradio_cso_app.py").write_text(
            "import json, os\n"
            "from run_vbt import _require_api_key\n"
            "def main(share=False):\n"
            "    _require_api_key()\n"
            "    print(json.dumps({'share': share, 'key_matches': "
            "os.environ['ANTHROPIC_API_KEY'] == os.environ['EXPECTED_TEST_KEY']}))\n"
            "if __name__ == '__main__': main()\n"
        )
        self.env = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_API_KEY", "BIOTECH_APP_PASSWORD", "PYTHONPATH")
        }
        self.env.update(VBT_RUNS_DIR=str(self.repo / "runs"),
                        CLAUDE_CONFIG_DIR=str(self.repo / "config"))

    def run_shell(self, *args):
        return subprocess.run(
            ["bash", str(self.repo / "run.sh"), *args], cwd=self.caller, env=self.env,
            capture_output=True, text=True, timeout=30,
        )

    def test_normal_and_shared_launch_preserve_exported_key_and_hide_password(self):
        (self.repo / ".env").write_text('ANTHROPIC_API_KEY="file-key"\n')
        self.env.update(ANTHROPIC_API_KEY="exported-key", EXPECTED_TEST_KEY="exported-key",
                        BIOTECH_APP_PASSWORD="private-test-password")
        for args in (("web",), ("web", "--share")):
            with self.subTest(args=args):
                result = self.run_shell(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 {"share": "--share" in args, "key_matches": True})
                self.assertNotIn("private-test-password", result.stdout + result.stderr)

    def test_launch_loads_export_prefixed_key_with_equals(self):
        (self.repo / ".env").write_text('export ANTHROPIC_API_KEY="test=key" # comment\n')
        self.env["EXPECTED_TEST_KEY"] = "test=key"
        result = self.run_shell("web")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["key_matches"])

    def test_doctor_accepts_environment_only_and_quoted_file_credentials(self):
        for contents, exported in ((None, "exported-key"),
                                   ('export ANTHROPIC_API_KEY="file-key"\n', None)):
            with self.subTest(contents=contents):
                if contents is not None:
                    (self.repo / ".env").write_text(contents)
                self.env.pop("ANTHROPIC_API_KEY", None)
                if exported:
                    self.env["ANTHROPIC_API_KEY"] = exported
                result = self.run_shell("doctor")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("[ok]   ANTHROPIC_API_KEY", result.stdout)
                self.assertNotIn("exported-key", result.stdout + result.stderr)
                self.assertNotIn("file-key", result.stdout + result.stderr)

    def test_doctor_rejects_blank_quoted_credentials(self):
        (self.repo / ".env").write_text('ANTHROPIC_API_KEY=""\n')
        result = self.run_shell("doctor")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] no non-empty ANTHROPIC_API_KEY", result.stdout)


@unittest.skipUnless(HAS_APP, "requires the application environment")
class TestApplicationStartup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        with patch.dict(os.environ, {"VBT_RUNS_DIR": cls.tmp.name,
                                     "GRADIO_ANALYTICS_ENABLED": "False"}):
            import gradio_cso_app
            import run
        cls.app = gradio_cso_app
        cls.cli = run

    def test_dotenv_loaded_before_settings_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "checkout"
            repo.mkdir()
            for name in ("gradio_cso_app.py", "run.py"):
                shutil.copy2(REPO / name, repo / name)
            shutil.copytree(REPO / "src", repo / "src",
                            ignore=shutil.ignore_patterns("__pycache__"))
            (repo / ".env").write_text(
                'export ANTHROPIC_API_KEY="file-key"\n'
                'BIOTECH_APP_PASSWORD="file-password"\n'
            )
            code = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import importlib, os, json; app = importlib.import_module(sys.argv[2]); "
                "print(json.dumps([getattr(app, 'APP_PASSWORD', os.environ.get('BIOTECH_APP_PASSWORD')), "
                "os.environ['ANTHROPIC_API_KEY']]))"
            )
            for exported in (False, True):
                with self.subTest(exported=exported):
                    env = {k: v for k, v in os.environ.items()
                           if k not in ("ANTHROPIC_API_KEY", "BIOTECH_APP_PASSWORD")}
                    env.update(VBT_RUNS_DIR=str(repo / "runs"), GRADIO_ANALYTICS_ENABLED="False")
                    expected = ["file-password", "file-key"]
                    if exported:
                        env.update(BIOTECH_APP_PASSWORD="exported-password", ANTHROPIC_API_KEY="exported-key")
                        expected = ["exported-password", "exported-key"]
                    for module_name in ("gradio_cso_app", "run"):
                        with self.subTest(entrypoint=module_name):
                            result = subprocess.run(
                                [sys.executable, "-c", code, str(repo), module_name], cwd=tmp, env=env,
                                capture_output=True, text=True, timeout=60,
                            )
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(json.loads(result.stdout), expected)

    def test_launch_requires_an_explicit_password(self):
        for share in (False, True):
            with self.subTest(share=share), patch.object(self.app, "APP_PASSWORD", ""):
                with patch.object(self.app, "create_interface") as create:
                    with self.assertRaisesRegex(SystemExit, "BIOTECH_APP_PASSWORD"):
                        self.app.main(share=share)
                    create.assert_not_called()
                self.assertFalse(self.app.check_password(""))
                self.assertFalse(self.app.check_password("drug"))

    def test_normal_and_shared_launch_use_native_auth_without_logging_password(self):
        for share in (False, True):
            with self.subTest(share=share), patch.object(self.app, "APP_PASSWORD", "test-pässword"):
                demo = Mock()
                with patch.object(self.app, "create_interface", return_value=(demo, "css", "js", "theme")) as create:
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                        self.app.main(share=share)
                create.assert_called_once_with(authenticated_by_server=True)
                options = demo.launch.call_args.kwargs
                self.assertEqual(options["share"], share)
                self.assertTrue(options["auth"]("any-user", "test-pässword"))
                self.assertFalse(options["auth"]("any-user", "wrong"))
                self.assertIn(str(self.app.RUNS_DIR), options["allowed_paths"])
                self.assertEqual((options["css"], options["js"], options["theme"]), ("css", "js", "theme"))
                self.assertNotIn("test-pässword", output.getvalue())

    def test_real_gradio_routes_require_login_for_research_and_downloads(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import gradio as gr

        with tempfile.TemporaryDirectory() as tmp, patch.object(self.app, "APP_PASSWORD", "test-password"):
            artifact = Path(tmp) / "result.txt"
            artifact.write_text("research artifact")
            with patch.object(self.app, "RUNS_DIR", Path(tmp)), \
                    patch.object(gr.Blocks, "launch", autospec=True) as launch, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.app.main()
                blocks = launch.call_args.args[0]
                options = launch.call_args.kwargs
                mounted = gr.mount_gradio_app(
                    FastAPI(), blocks, path="/", auth=options["auth"],
                    auth_message=options["auth_message"],
                    allowed_paths=options["allowed_paths"], blocked_paths=options["blocked_paths"],
                )
            self.addCleanup(blocks.close, verbose=False)
            with patch.object(self.app, "RobustClaudeSDKClient") as sdk, TestClient(mounted) as client:
                self.assertIn('"auth_required":true', client.get("/").text.replace(" ", ""))
                self.assertEqual(client.get("/config").status_code, 401)
                self.assertEqual(client.post("/gradio_api/queue/join", json={
                    "data": [], "fn_index": 0, "session_hash": "unauthenticated",
                }).status_code, 401)
                download = f"/gradio_api/file={artifact}"
                self.assertEqual(client.get(download).status_code, 401)
                self.assertEqual(client.post("/login", data={
                    "username": "researcher", "password": "incorrect",
                }).status_code, 400)
                self.assertEqual(client.post("/login", data={
                    "username": "researcher", "password": "test-password",
                }).status_code, 200)
                config = client.get("/config")
                self.assertEqual(config.status_code, 200)
                columns = {tuple(c["props"].get("elem_classes", [])): c["props"]
                           for c in config.json()["components"] if c["type"] == "column"}
                self.assertTrue(columns[("main-screen-container",)]["visible"])
                self.assertFalse(columns[("login-container",)]["visible"])
                self.assertEqual(client.get(download).text, "research artifact")
                sdk.assert_not_called()

    def test_research_sessions_reject_missing_key_before_sdk_setup(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), tempfile.TemporaryDirectory() as tmp:
            with patch.object(self.cli, "SESSIONS_DIR", Path(tmp)):
                sessions = ((self.app, self.app.CSOSession("missing-key")),
                            (self.cli, self.cli.Session()))
            for module, session in sessions:
                with self.subTest(module=module.__name__), patch.object(module, "RobustClaudeSDKClient") as sdk:
                    with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY"):
                        asyncio.run(session.initialize())
                    sdk.assert_not_called()

    def test_interactive_cli_help_does_not_require_a_model_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(REPO / "run.py"), "--help"],
                env=dict(os.environ, ANTHROPIC_API_KEY=""), cwd=tmp,
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--model", result.stdout)


if __name__ == "__main__":
    unittest.main()
