"""Regression coverage for persisted tool output in both permission layers."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from claude_agent_sdk._internal.sessions import _sanitize_path
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from src.utils.agent_hooks import (
    SecurityConfig,
    build_security_callback,
    create_bash_security_hook,
    create_file_read_security_hook,
    create_file_write_security_hook,
)
from src.utils.runtime_paths import RuntimePaths


class RuntimeSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.workspace = self.repo / "runs" / "current run"
        self.workspace.mkdir(parents=True)
        self.runtime = RuntimePaths(
            self.workspace,
            env={"CLAUDE_CONFIG_DIR": str(self.root / "runtime-config")},
            temp_parent=self.root,
        )
        self.project = _sanitize_path(str(self.workspace))
        self.spill_dir = (
            self.runtime.config_dir / "projects" / self.project
            / self.runtime.session_id / "tool-results"
        )
        self.spill_dir.mkdir(parents=True)
        self.spill = self.spill_dir / "toolu_large_result.json"
        self.spill.write_text('{"evidence": "' + "x" * 70000 + '"}')
        self.reference = self.root / "reference"
        self.reference.mkdir()
        self.reference_file = self.reference / "data.parquet"
        self.reference_file.touch()
        self.config = SecurityConfig(
            str(self.workspace),
            app_source_dir=str(self.repo),
            extra_read_dirs=[str(self.reference)],
            blocked_read_dirs=[str(self.repo / path) for path in (".env", "src", ".git")],
            runtime_paths=self.runtime,
        )

    async def assert_permission(self, allowed, tool, payload, *, cwd=None, config=None):
        config = config or self.config
        if tool == "Bash":
            hook = create_bash_security_hook(config)
        elif tool in ("Write", "Edit", "NotebookEdit"):
            hook = create_file_write_security_hook(config)
        else:
            hook = create_file_read_security_hook(config)
        hook_result = await hook({
            "tool_name": tool,
            "tool_input": payload,
            "cwd": str(cwd or self.workspace),
        }, "tool-1", {})
        denied = hook_result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        self.assertEqual(not denied, allowed, (tool, payload, hook_result))
        context = {"cwd": str(cwd)} if cwd else ToolPermissionContext(tool_use_id="tool-1")
        callback_result = await build_security_callback(config)(tool, payload, context)
        self.assertIsInstance(callback_result, PermissionResultAllow if allowed else PermissionResultDeny)
        if allowed:
            self.assertEqual(callback_result.updated_input, payload)

    def test_default_matches_runtime_home_and_does_not_use_scratch(self):
        with patch("src.utils.runtime_paths.Path.home", return_value=self.root / "home"):
            runtime = RuntimePaths(
                self.workspace, env={"SCRATCH": str(self.root / "scratch")}, temp_parent=self.root,
            )
        self.assertEqual(runtime.config_dir, self.root / "home" / ".claude")
        self.assertNotIn("CLAUDE_CONFIG_DIR", runtime.sdk_env)
        self.assertEqual(runtime.user_config_path, self.root / "home" / ".claude.json")
        self.assertEqual(runtime.sdk_env["CLAUDE_CODE_TMPDIR"], str(runtime.temp_dir))
        self.assertEqual(runtime.temp_dir.stat().st_mode & 0o777, 0o700)

    def test_runtime_settings_are_local_to_each_client(self):
        before = dict(os.environ)
        second = RuntimePaths(self.workspace, env={}, temp_parent=self.root)
        self.assertNotEqual(second.session_id, self.runtime.session_id)
        self.assertNotEqual(second.temp_dir, self.runtime.temp_dir)
        self.assertEqual(dict(os.environ), before)

    def test_empty_explicit_override_keeps_the_existing_normalized_path(self):
        with patch("src.utils.runtime_paths.Path.home", return_value=self.root / "home"):
            runtime = RuntimePaths(
                self.workspace, env={"CLAUDE_CONFIG_DIR": ""}, temp_parent=self.root,
            )
        self.assertEqual(runtime.sdk_env["CLAUDE_CONFIG_DIR"], str(runtime.config_dir))
        self.assertEqual(runtime.user_config_path, runtime.config_dir / ".claude.json")

    async def test_spill_read_glob_and_grep_work_in_both_layers(self):
        await self.assert_permission(True, "Read", {"file_path": str(self.spill)})
        await self.assert_permission(True, "Grep", {"path": str(self.spill), "pattern": "evidence"})
        await self.assert_permission(True, "Glob", {"path": str(self.spill_dir), "pattern": "*.json"})
        await self.assert_permission(True, "Glob", {"pattern": str(self.spill_dir / "*.json")})

    async def test_permissions_survive_the_actual_client_control_protocol(self):
        transport = SimpleNamespace(write=AsyncMock())
        query = Query(transport, is_streaming_mode=True, can_use_tool=build_security_callback(self.config))
        self.addCleanup(query._message_send.close)
        self.addCleanup(query._message_receive.close)
        for path, expected in (
            (self.spill, "allow"),
            (self.runtime.config_dir / ".credentials.json", "deny"),
        ):
            await query._handle_control_request({
                "type": "control_request",
                "request_id": "permission-1",
                "request": {
                    "subtype": "can_use_tool", "tool_name": "Read",
                    "input": {"file_path": str(path)},
                    "tool_use_id": "tool-1", "agent_id": "genetics-specialist",
                },
            })
            response = json.loads(transport.write.await_args.args[0])["response"]
            self.assertEqual(response["subtype"], "success", response)
            self.assertEqual(response["response"]["behavior"], expected)

    async def test_spill_bash_reads_can_save_selected_output_in_workspace(self):
        await self.assert_permission(True, "Bash", {"command": f"head -c 200 '{self.spill}'"})
        await self.assert_permission(True, "Bash", {
            "command": f"cat '{self.spill}' | grep evidence > excerpt.txt",
        })

    async def test_task_outputs_are_limited_to_the_same_session(self):
        user_dir = "claude" if os.name == "nt" else f"claude-{os.getuid()}"
        task_dir = self.runtime.temp_dir / user_dir / self.project / self.runtime.session_id / "tasks"
        task_dir.mkdir(parents=True)
        task_output = task_dir / "specialist.output"
        task_output.write_text("result")
        await self.assert_permission(True, "Read", {"file_path": str(task_output)})
        sibling = self.runtime.temp_dir / user_dir / self.project / str(uuid4()) / "tasks" / "other.output"
        await self.assert_permission(False, "Read", {"file_path": str(sibling)})
        await self.assert_permission(False, "Read", {"file_path": str(self.runtime.temp_dir / "secret.txt")})

    async def test_runtime_credentials_transcripts_and_other_sessions_stay_private(self):
        paths = [
            self.runtime.config_dir,
            self.runtime.config_dir / ".credentials.json",
            self.runtime.config_dir / "settings.json",
            self.spill_dir.parent,
            self.spill_dir.parent.parent / f"{self.runtime.session_id}.jsonl",
            self.spill_dir.parent.parent / str(uuid4()) / "tool-results" / "other.json",
            self.runtime.config_dir / "projects" / "another-project" / self.runtime.session_id / "tool-results" / "other.json",
            self.spill_dir / ".env",
        ]
        for path in paths:
            with self.subTest(path=path):
                await self.assert_permission(False, "Read", {"file_path": str(path)})
                await self.assert_permission(False, "Bash", {"command": f"cat '{path}'"})

    async def test_runtime_protection_overrides_broad_read_roots(self):
        broad_config = SecurityConfig(
            str(self.workspace), extra_read_dirs=[str(self.root)], runtime_paths=self.runtime,
        )
        await self.assert_permission(False, "Read", {
            "file_path": str(self.runtime.config_dir / ".credentials.json"),
        }, config=broad_config)
        await self.assert_permission(True, "Read", {"file_path": str(self.spill)}, config=broad_config)

    async def test_default_global_config_stays_private_with_a_broad_read_root(self):
        with patch("src.utils.runtime_paths.Path.home", return_value=self.root):
            runtime = RuntimePaths(self.workspace, env={}, temp_parent=self.root)
        config = SecurityConfig(
            str(self.workspace), extra_read_dirs=[str(self.root)], runtime_paths=runtime,
        )
        for tool, payload in (
            ("Read", {"file_path": str(self.root / ".claude.json")}),
            ("Bash", {"command": f"cat '{self.root / '.claude.json'}'"}),
        ):
            with self.subTest(tool=tool):
                await self.assert_permission(False, tool, payload, config=config)

    async def test_relative_workspace_paths_and_reference_data_still_work(self):
        (self.workspace / "result.txt").write_text("result")
        await self.assert_permission(True, "Read", {"file_path": "result.txt"})
        await self.assert_permission(True, "Write", {"file_path": "next-result.txt", "content": "result"})
        await self.assert_permission(True, "Read", {"file_path": str(self.reference_file)})
        await self.assert_permission(True, "Bash", {"command": "echo result > next-result.txt"})
        await self.assert_permission(True, "Bash", {
            "command": "python - <<'PY'\n# The target's evidence\nprint(5 > 3)\nPY",
        })

    async def test_source_keys_and_git_remain_blocked(self):
        for path in (self.repo / ".env", self.repo / "src" / "prompt.md", self.repo / ".git" / "config"):
            with self.subTest(path=path):
                await self.assert_permission(False, "Read", {"file_path": str(path)})
                await self.assert_permission(False, "Bash", {"command": f"cat '{path}'"})

    async def test_runtime_writes_denied_for_file_tools_and_shells(self):
        for tool in ("Write", "Edit", "NotebookEdit"):
            key = "notebook_path" if tool == "NotebookEdit" else "file_path"
            await self.assert_permission(False, tool, {key: str(self.spill)})
        commands = [
            f"echo overwrite > '{self.spill}'",
            f"cat result.txt >> '{self.spill}'",
            f"tee '{self.spill}'",
            f"touch '{self.spill}'",
            f"sed -i 's/x/y/' '{self.spill}'",
            f"cat '{self.spill}'\ntee '{self.spill}'",
            f"cat '{self.spill}' |& tee '{self.spill}'",
            f"cat '{self.spill}' >(tee '{self.spill}')",
            f'''python -c "open('{self.spill}', 'w').write('bad')"''',
            f"python - <<'PY'\nopen('{self.spill}', 'w').write('bad')\nPY",
        ]
        for command in commands:
            with self.subTest(command=command):
                await self.assert_permission(False, "Bash", {"command": command})

    async def test_callback_cwd_cannot_widen_write_permissions(self):
        await self.assert_permission(False, "Write", {
            "file_path": str(self.root / "outside.txt"), "content": "bad",
        }, cwd=self.root)

    async def test_traversal_and_symlinks_cannot_escape_output_directory(self):
        traversal = self.spill_dir / ".." / "private.json"
        await self.assert_permission(False, "Read", {"file_path": str(traversal)})
        await self.assert_permission(False, "Glob", {
            "path": str(self.spill_dir), "pattern": "../*.json",
        })
        await self.assert_permission(False, "Glob", {
            "path": str(self.workspace), "pattern": str(self.runtime.config_dir / "*.json"),
        })
        secret = self.runtime.config_dir / ".credentials.json"
        secret.write_text("private")
        spill_link = self.spill_dir / "linked.json"
        spill_link.symlink_to(secret)
        workspace_link = self.workspace / "linked.json"
        workspace_link.symlink_to(secret)
        for link in (spill_link, workspace_link):
            await self.assert_permission(False, "Read", {"file_path": str(link)})
            await self.assert_permission(False, "Bash", {"command": f"cat '{link}'"})
        loop = self.spill_dir / "loop.json"
        loop.symlink_to(loop)
        await self.assert_permission(False, "Read", {"file_path": str(loop)})
        await self.assert_permission(False, "Bash", {"command": f"cat '{loop}'"})


if __name__ == "__main__":
    unittest.main()
