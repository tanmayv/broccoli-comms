import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


_APP_PATH = Path(__file__).resolve().parent / "broccoli-comms.py"
_spec = importlib.util.spec_from_file_location("broccoli_comms_app", _APP_PATH)
broccoli_comms_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(broccoli_comms_app)


class TestBroccoliCommsApp(unittest.TestCase):
    def test_registry_agents_filters_shared_service_query(self):
        args = argparse.Namespace(json=True, name=None, hostname=None, status=None, logical_identity="agent-communicator", service_kind="shared_service")
        with mock.patch.object(broccoli_comms_app, "_registry_request", return_value=(200, {"agents": []}, None)) as request, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            broccoli_comms_app.registry_agents(args)
        request.assert_called_once_with("/agents?logical_identity=agent-communicator&service_kind=shared_service")
        self.assertEqual(json.loads(stdout.getvalue()), {"agents": []})

    def test_status_and_doctor_include_build_revision(self):
        with mock.patch.dict(os.environ, {"BROCCOLI_COMMS_VERSION": "0.1.0", "BROCCOLI_COMMS_REVISION": "abc1234"}, clear=False), \
             mock.patch.object(broccoli_comms_app, "VERSION", "0.1.0"), \
             mock.patch.object(broccoli_comms_app, "REVISION", "abc1234"), \
             mock.patch.object(broccoli_comms_app, "paths", return_value={"runtime": Path("/tmp/r"), "cache": Path("/tmp/c"), "config": Path("/tmp/cfg"), "tracker_socket": Path("/tmp/sock"), "config_json": Path("/tmp/config.json")}), \
             mock.patch.object(broccoli_comms_app, "can_connect", return_value=True), \
             mock.patch.object(broccoli_comms_app, "tmux_up", return_value=True), \
             mock.patch.object(broccoli_comms_app, "tmux_socket_label", return_value=None), \
             mock.patch.object(broccoli_comms_app, "load_config", return_value={"agents": {}}), \
             mock.patch.object(broccoli_comms_app, "managed_windows", return_value=[]), \
             mock.patch.object(broccoli_comms_app, "_resolve_executable", return_value="/bin/true"), \
             mock.patch.object(broccoli_comms_app, "_command_version", return_value="ok"), \
             mock.patch.object(broccoli_comms_app, "_check_writable_dir", return_value=(True, "writable")):
            self.assertEqual(broccoli_comms_app.status_payload()["build"]["display"], "0.1.0+abc1234")
            self.assertEqual(broccoli_comms_app.doctor_payload()["build"]["revision"], "abc1234")

    def test_trusted_memory_actor_rejects_spoofed_agent_name(self):
        with mock.patch.dict(os.environ, {"AGENT_NAME": "user"}, clear=False), mock.patch.object(broccoli_comms_app, "get_toml_config", return_value=[]), mock.patch.object(broccoli_comms_app, "tracker_rpc", return_value={"name": "evil"}):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.trusted_memory_actor_from_runtime()

    def test_trusted_memory_actor_rejects_agent_unsetting_name_but_verified_by_pid(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(broccoli_comms_app, "get_toml_config", return_value=[]), mock.patch.object(broccoli_comms_app, "tracker_rpc", return_value={"name": "evil"}):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.trusted_memory_actor_from_runtime()

    def test_trusted_memory_actor_rejects_only_agent_id_when_unverified(self):
        with mock.patch.dict(os.environ, {"AGENT_ID": "evil-id"}, clear=True), mock.patch.object(broccoli_comms_app, "get_toml_config", return_value=[]), mock.patch.object(broccoli_comms_app, "tracker_rpc", side_effect=RuntimeError("not identified")):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.trusted_memory_actor_from_runtime()

    def test_trusted_memory_actor_allows_configured_verified_agent(self):
        with mock.patch.dict(os.environ, {"AGENT_NAME": "spoofed"}, clear=False), mock.patch.object(broccoli_comms_app, "get_toml_config", return_value=["coordinator"]), mock.patch.object(broccoli_comms_app, "tracker_rpc", return_value={"name": "coordinator"}):
            self.assertEqual(broccoli_comms_app.trusted_memory_actor_from_runtime(), "coordinator")

    def test_trusted_memory_actor_allows_local_human_without_agent_identity(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(broccoli_comms_app, "tracker_rpc", side_effect=RuntimeError("not identified")):
            self.assertEqual(broccoli_comms_app.trusted_memory_actor_from_runtime(), "user")

    def test_trusted_memory_actor_rejects_unreachable_tracker_without_agent_identity(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(broccoli_comms_app, "tracker_rpc", return_value=None):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.trusted_memory_actor_from_runtime()

    def test_memory_propose_allows_unverified_agent_proposals(self):
        args = argparse.Namespace(type="fact", scope="global", subject_agent=None, title="T", body="B", source_task="task-1", trusted_manual=False, tag=None, idempotency_key=None, agent=None, instance=None, metadata_json=None, json=True)
        fake_kernel = mock.Mock()
        fake_kernel.memory.propose.return_value = {"memory": {"memory_id": "mem-1"}}
        with mock.patch.dict(os.environ, {"AGENT_NAME": "unverified-agent", "AGENT_ID": "agent-id"}, clear=True), mock.patch.object(broccoli_comms_app, "tracker_rpc", return_value=None), mock.patch.object(broccoli_comms_app, "learning_kernel", return_value=fake_kernel), mock.patch.object(broccoli_comms_app, "notify_memory_proposal", return_value={"sent": False}):
            broccoli_comms_app.memory_propose(args)
        self.assertEqual(fake_kernel.memory.propose.call_args.kwargs["proposed_by"], "unverified-agent")
        self.assertEqual(fake_kernel.memory.propose.call_args.kwargs["proposed_by_instance"], "agent-id")

    def test_memory_propose_uses_unverified_identity_for_immutable_check(self):
        args = argparse.Namespace(type="fact", scope="global", subject_agent=None, title="T", body="B", source_task="task-1", trusted_manual=False, tag=None, idempotency_key=None, agent=None, instance=None, metadata_json=None, json=True)
        fake_kernel = mock.Mock()
        fake_kernel.memory.propose.side_effect = ValueError("immutable/non-learning instance cannot propose memory")
        with mock.patch.dict(os.environ, {"AGENT_NAME": "immutable-agent", "AGENT_ID": "immutable-id"}, clear=True), mock.patch.object(broccoli_comms_app, "get_toml_config", return_value=["immutable-id"]), mock.patch.object(broccoli_comms_app, "learning_kernel", return_value=fake_kernel):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.memory_propose(args)
        self.assertTrue(fake_kernel.memory.propose.call_args.kwargs["non_learning"])
        self.assertEqual(fake_kernel.memory.propose.call_args.kwargs["proposed_by"], "immutable-agent")

    def test_trusted_manual_memory_provenance_uses_verified_actor(self):
        args = argparse.Namespace(type="habit", scope="global", subject_agent=None, title="T", body="B", source_task=None, trusted_manual=True, tag=None, idempotency_key=None, agent="spoofed", instance=None, metadata_json=None, json=True)
        captured = {}
        fake_kernel = mock.Mock()
        fake_kernel.memory.propose.side_effect = lambda **kw: captured.update(kw) or {"memory": {"memory_id": "mem-1"}}
        def fake_config(_section, key, default=None):
            return ["coordinator"] if key == "trusted_memory_actors" else []
        with mock.patch.object(broccoli_comms_app, "tracker_rpc", return_value={"name": "coordinator", "agent_id": "coord-id"}), mock.patch.object(broccoli_comms_app, "get_toml_config", side_effect=fake_config), mock.patch.object(broccoli_comms_app, "learning_kernel", return_value=fake_kernel):
            broccoli_comms_app.memory_propose(args)
        self.assertEqual(captured["proposed_by"], "coordinator")
        self.assertEqual(captured["created_by"] if "created_by" in captured else captured["trusted_actor"], "coordinator")
        self.assertEqual(captured["proposed_by_instance"], "coord-id")

    def test_memory_propose_edit_allows_unverified_agent_proposals(self):
        args = argparse.Namespace(memory_id="mem-active", type=None, scope=None, subject_agent=None, title=None, description=None, body="updated", source_task="task-1", trusted_manual=False, tag=["audit"], expected_version=2, metadata_json=None, agent=None, instance=None, json=True)
        fake_kernel = mock.Mock()
        fake_kernel.memory.propose_edit.return_value = {"memory": {"memory_id": "mem-proposal"}}
        with mock.patch.dict(os.environ, {"AGENT_NAME": "agent-a", "AGENT_ID": "id-a"}, clear=True), mock.patch.object(broccoli_comms_app, "learning_kernel", return_value=fake_kernel), mock.patch.object(broccoli_comms_app, "notify_memory_proposal", return_value={"sent": True}):
            broccoli_comms_app.memory_propose_edit(args)
        kwargs = fake_kernel.memory.propose_edit.call_args.kwargs
        self.assertEqual(kwargs["proposed_by"], "agent-a")
        self.assertEqual(kwargs["proposed_by_instance"], "id-a")
        self.assertEqual(kwargs["body"], "updated")
        self.assertEqual(kwargs["expected_version"], 2)

    def test_memory_propose_with_memory_id_routes_to_edit_proposal(self):
        args = argparse.Namespace(memory_id="mem-active", archive=False, reason=None, type=None, scope=None, subject_agent=None, title="New", description=None, body="updated", source_task="task-1", trusted_manual=False, tag=["audit"], idempotency_key=None, expected_version=2, metadata_json=None, agent=None, instance=None, json=True)
        fake_kernel = mock.Mock()
        fake_kernel.memory.propose_edit.return_value = {"memory": {"memory_id": "mem-proposal"}}
        with mock.patch.dict(os.environ, {"AGENT_NAME": "agent-a", "AGENT_ID": "id-a"}, clear=True), mock.patch.object(broccoli_comms_app, "learning_kernel", return_value=fake_kernel), mock.patch.object(broccoli_comms_app, "notify_memory_proposal", return_value={"sent": True}):
            broccoli_comms_app.memory_propose(args)
        kwargs = fake_kernel.memory.propose_edit.call_args.kwargs
        self.assertEqual(fake_kernel.memory.propose_edit.call_args.args, ("mem-active",))
        self.assertEqual(kwargs["title"], "New")
        self.assertEqual(kwargs["body"], "updated")
        self.assertEqual(kwargs["expected_version"], 2)
        self.assertEqual(kwargs["proposed_by"], "agent-a")

    def test_memory_propose_with_archive_routes_to_archive_proposal(self):
        args = argparse.Namespace(memory_id="mem-active", archive=True, reason="obsolete", type=None, scope=None, subject_agent=None, title=None, description=None, body=None, source_task="task-1", trusted_manual=False, tag=None, idempotency_key=None, expected_version=3, metadata_json=None, agent=None, instance=None, json=True)
        fake_kernel = mock.Mock()
        fake_kernel.memory.propose_archive.return_value = {"memory": {"memory_id": "mem-archive-proposal"}}
        with mock.patch.dict(os.environ, {"AGENT_NAME": "agent-a", "AGENT_ID": "id-a"}, clear=True), mock.patch.object(broccoli_comms_app, "learning_kernel", return_value=fake_kernel), mock.patch.object(broccoli_comms_app, "notify_memory_proposal", return_value={"sent": True}):
            broccoli_comms_app.memory_propose(args)
        kwargs = fake_kernel.memory.propose_archive.call_args.kwargs
        self.assertEqual(fake_kernel.memory.propose_archive.call_args.args, ("mem-active",))
        self.assertEqual(kwargs["reason"], "obsolete")
        self.assertEqual(kwargs["expected_version"], 3)
        self.assertEqual(kwargs["proposed_by"], "agent-a")

    def test_memory_decide_approve_and_reject_use_trusted_actor(self):
        fake_kernel = mock.Mock()
        fake_kernel.memory.approve.return_value = {"memory": {"memory_id": "mem-1"}}
        fake_kernel.memory.reject.return_value = {"memory": {"memory_id": "mem-2"}}
        with mock.patch.object(broccoli_comms_app, "learning_kernel", return_value=fake_kernel), mock.patch.object(broccoli_comms_app, "trusted_memory_actor_from_runtime", return_value="user"):
            broccoli_comms_app.memory_decide(argparse.Namespace(memory_id="mem-1", decision="approve", reason=None, expected_version=1, json=True))
            broccoli_comms_app.memory_decide(argparse.Namespace(memory_id="mem-2", decision="reject", reason="bad", expected_version=2, json=True))
        fake_kernel.memory.approve.assert_called_once_with("mem-1", expected_version=1, actor="user")
        fake_kernel.memory.reject.assert_called_once_with("mem-2", reason="bad", expected_version=2, actor="user")

    def test_base_env_strips_agent_identity_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            "BROCCOLI_COMMS_RUNTIME_DIR": os.path.join(tmp, "runtime"),
            "BROCCOLI_COMMS_CACHE_DIR": os.path.join(tmp, "cache"),
            "BROCCOLI_COMMS_CONFIG_DIR": os.path.join(tmp, "config"),
            "BROCCOLI_COMMS_DISABLE_CONFIG_REGISTRIES": "1",
            "AGENT_ID": "agent-id",
            "AGENT_NAME": "agent-name",
            "AGENT_UUID": "agent-id",
            "SUGGESTED_AGENT_NAME": "suggested",
            "BROCCOLI_COMMS_CLI": "/stale/broccoli-comms",
            "TMUX": "tmux-env",
            "TMUX_PANE": "%1",
            "PATH": "/bin",
        }, clear=False):
            env = broccoli_comms_app.base_env()

        self.assertNotIn("AGENT_ID", env)
        self.assertNotIn("AGENT_NAME", env)
        self.assertNotIn("AGENT_UUID", env)
        self.assertNotIn("SUGGESTED_AGENT_NAME", env)
        self.assertNotIn("BROCCOLI_COMMS_CLI", env)
        self.assertNotIn("TMUX", env)
        self.assertNotIn("TMUX_PANE", env)

    def test_base_env_can_preserve_agent_identity_for_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            "BROCCOLI_COMMS_RUNTIME_DIR": os.path.join(tmp, "runtime"),
            "BROCCOLI_COMMS_CACHE_DIR": os.path.join(tmp, "cache"),
            "BROCCOLI_COMMS_CONFIG_DIR": os.path.join(tmp, "config"),
            "BROCCOLI_COMMS_DISABLE_CONFIG_REGISTRIES": "1",
            "AGENT_ID": "agent-id",
            "AGENT_NAME": "agent-name",
            "AGENT_UUID": "agent-id",
            "SUGGESTED_AGENT_NAME": "suggested",
            "TMUX": "tmux-env",
            "TMUX_PANE": "%1",
            "PATH": "/bin",
        }, clear=False):
            env = broccoli_comms_app.base_env(preserve_agent_identity=True)

        self.assertEqual(env["AGENT_ID"], "agent-id")
        self.assertEqual(env["AGENT_NAME"], "agent-name")
        self.assertEqual(env["AGENT_UUID"], "agent-id")
        self.assertNotIn("SUGGESTED_AGENT_NAME", env)
        self.assertNotIn("TMUX", env)
        self.assertNotIn("TMUX_PANE", env)

    def test_executable_resolution_uses_environment_not_config(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": tmp,
            "BROCCOLI_COMMS_AGENT_TRACKER": "/env/agent-tracker",
            "BROCCOLI_COMMS_AGENT_TRACKER_CTL": "/env/agent-tracker-ctl.py",
            "BROCCOLI_COMMS_AGENT_WRAPPER": "/env/agent-wrapper",
            "BROCCOLI_COMMS_AGENT_REGISTRY": "/env/agent-registry",
            "BROCCOLI_COMMS_AGENT_COMMUNICATOR_TUI": "/env/agent-communicator",
        }, clear=False):
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text("""
[executables]
agent_tracker = "/config/agent-tracker"
agent_tracker_ctl_py = "/config/agent-tracker-ctl.py"
agent_wrapper = "/config/agent-wrapper"
agent_registry = "/config/agent-registry"
agent_communicator_tui = "/config/agent-communicator"
""")
            self.assertEqual(broccoli_comms_app.tracker_script(), "/env/agent-tracker")
            self.assertEqual(broccoli_comms_app.tracker_ctl_script(), "/env/agent-tracker-ctl.py")
            self.assertEqual(broccoli_comms_app.wrapper_path(), "/env/agent-wrapper")
            self.assertEqual(broccoli_comms_app.registry_script(), "/env/agent-registry")
            self.assertEqual(broccoli_comms_app.tui_path(), "/env/agent-communicator")

    def test_agent_tracker_passthrough_preserves_agent_identity(self):
        env = {"AGENT_ID": "agent-id"}
        with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
             mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
             mock.patch.object(broccoli_comms_app, "tracker_ctl_script", return_value="/ctl.py"), \
             mock.patch.object(broccoli_comms_app, "base_env", return_value=env) as base_env, \
             mock.patch.object(broccoli_comms_app.os, "execvpe") as execvpe:
            broccoli_comms_app.agent_tracker(argparse.Namespace(tracker_args=["send-message", "target", "hello"]))

        base_env.assert_called_once_with(preserve_agent_identity=True)
        execvpe.assert_called_once_with(
            broccoli_comms_app.sys.executable,
            [broccoli_comms_app.sys.executable, "/ctl.py", "send-message", "target", "hello"],
            env,
        )

    def test_run_requires_command_after_double_dash(self):
        with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
             mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
             mock.patch.object(broccoli_comms_app, "window_exists", return_value=False):
            with mock.patch.object(broccoli_comms_app, "tmux", side_effect=lambda *cmd, **kw: mock.Mock(returncode=0, stdout="", stderr="")):
                with self.assertRaises(SystemExit):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=None, scope=None, swarm=None, role=None, command=[]))

    def test_public_help_hides_legacy_launch_commands(self):
        output = io.StringIO()
        with mock.patch.object(broccoli_comms_app.sys, "argv", ["broccoli-comms", "--help"]), \
             mock.patch.object(broccoli_comms_app.sys, "stdout", output):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.main()
        text = output.getvalue()
        self.assertIn("run", text)
        self.assertNotIn("broccoli-comms track", text)
        self.assertNotIn("broccoli-comms agent add", text)

    def test_agent_subcommand_help_lists_public_actions_only(self):
        output = io.StringIO()
        with mock.patch.object(broccoli_comms_app.sys, "argv", ["broccoli-comms", "agent", "--help"]), \
             mock.patch.object(broccoli_comms_app.sys, "stdout", output):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.main()
        text = output.getvalue()
        self.assertIn("list", text)
        self.assertIn("edit", text)
        self.assertIn("restart", text)
        self.assertNotIn("usage: broccoli-comms agent add", text)

    def test_memory_help_describes_simplified_proposal_and_decision_workflows(self):
        output = io.StringIO()
        with mock.patch.object(broccoli_comms_app.sys, "argv", ["broccoli-comms", "memory", "--help"]), \
             mock.patch.object(broccoli_comms_app.sys, "stdout", output):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.main()
        text = output.getvalue()
        self.assertIn("Manage durable memory proposals, approvals, and active records", text)
        self.assertIn("Create a new proposal, edit proposal, or archive", text)
        self.assertIn("Approve or reject a pending proposal as a trusted", text)
        self.assertIn("Directly revoke an active memory", text)

        output = io.StringIO()
        with mock.patch.object(broccoli_comms_app.sys, "argv", ["broccoli-comms", "memory", "propose", "--help"]), \
             mock.patch.object(broccoli_comms_app.sys, "stdout", output):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.main()
        propose_help = output.getvalue()
        self.assertIn("Existing memory to edit", propose_help)
        self.assertIn("propose archiving/removing", propose_help)
        self.assertIn("Expected current target version", propose_help)

        output = io.StringIO()
        with mock.patch.object(broccoli_comms_app.sys, "argv", ["broccoli-comms", "memory", "decide", "--help"]), \
             mock.patch.object(broccoli_comms_app.sys, "stdout", output):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.main()
        decide_help = output.getvalue()
        self.assertIn("Pending proposal id to decide", decide_help)
        self.assertIn("Decision to apply to the pending proposal", decide_help)
        self.assertIn("Reason for rejecting the proposal", decide_help)

    def test_unknown_legacy_track_command_fails_fast(self):
        with mock.patch.object(broccoli_comms_app.sys, "argv", ["broccoli-comms", "track", "--name", "planner", "--", "pi"]):
            with self.assertRaises(SystemExit):
                broccoli_comms_app.main()

    def test_run_accepts_options_after_name_before_separator(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append((list(cmd), dict(kwargs)))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=None, scope=None, swarm=None, role=None, command=["--cwd", tmp, "--scope", "repo:test", "--", "sleep", "60"]))

        launched = [call for call, _ in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("task bootstrap", launched)
        self.assertIn("--scope repo:test", launched)
        self.assertIn("sleep 60", launched)
        self.assertNotIn("exec \\\"$@\\\"' _broccoli_agent_bootstrap --", launched)

    def test_run_launches_in_managed_window_with_bootstrap_wrapper(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append((list(cmd), dict(kwargs)))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope="repo:test", swarm=None, role=None, command=["pi", "--flag"]))

        new_window_calls = [call for call, _ in calls if call and call[0] == "new-window"]
        self.assertEqual(len(new_window_calls), 1)
        launched = new_window_calls[0][-1]
        self.assertNotIn("bootstrap.json", launched)
        self.assertNotIn("skills/planner", launched)
        self.assertIn("--write-context-dir", launched)
        self.assertIn("set -euo pipefail", launched)
        self.assertIn("AGENT_TRACKER_SOCKET", launched)
        self.assertIn("task bootstrap", launched)

    def test_run_includes_provider_hyphenated_auto_accept_flag_from_toml(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text('[providers.pi]\ncmd = "pi"\nauto-accept-flag = "--dangerously-skip-permissions"\n')
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, command=["pi"], json=True))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("--dangerously-skip-permissions", launched)

    def test_run_uses_jetski_context_layout_from_provider_alias(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text('[providers.jetski]\ncmd = "jetski"\nagentsDir = ".agents"\n')
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace") as workspace:
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, command=["jetski"], json=True))

        workspace.assert_called_once()
        self.assertEqual(workspace.call_args.kwargs["agents_dir"], ".agents")
        self.assertEqual(workspace.call_args.kwargs["context_layout"], "jetski")
        self.assertEqual(workspace.call_args.kwargs["agent_root_dir"], str(Path.home() / "agents-root"))
        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("BROCCOLI_AGENTS_DIR=.agents", launched)
        self.assertIn("BROCCOLI_COMMS_CONTEXT_LAYOUT=jetski", launched)

    def test_run_includes_provider_initial_message_prompt_flag_from_toml(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text('[providers.pi]\ncmd = "pi"\nprompt-flag-name = "--prompt"\ninitial-message = "Run/bootstrap with Broccoli Comms, then start assigned task."\n')
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, command=["pi"], json=True))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("--prompt", launched)
        self.assertIn("Run/bootstrap with Broccoli Comms, then start assigned task.", launched)
        self.assertNotIn("New message in inbox", launched)

    def test_run_treats_double_dash_prompt_flag_as_positional_initial_message(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text('[providers.pi]\ncmd = "pi"\nprompt-flag-name = "--"\ninitial-message = "Run/bootstrap positionally."\n')
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, command=["pi"], json=True))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("Run/bootstrap positionally.", launched)
        self.assertNotIn(" -- Run/bootstrap positionally.", launched)

    def test_run_omits_provider_initial_message_when_prompt_flag_missing(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text('[providers.pi]\ncmd = "pi"\ninitial-message = "Run/bootstrap exactly."\n')
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, command=["pi"], json=True))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertNotIn("Run/bootstrap exactly.", launched)

    def test_run_omits_empty_provider_auto_accept_flag_from_toml(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text('[providers.pi]\ncmd = "pi"\nauto-accept-flag = ""\n')
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, command=["pi"], json=True))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("_broccoli_agent_bootstrap pi", launched)
        self.assertNotIn("--dangerously-skip-permissions", launched)

    def test_reconcile_agents_includes_scope_bootstrap_wrapper(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            cfg_path = cfg_dir / "config.json"
            with cfg_path.open("w") as f:
                json.dump({
                    "agents": {
                        "planner": {
                            "cwd": tmp,
                            "command": "pi --flag",
                            "scope": "repo:demo",
                        }
                    }
                }, f)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "managed_track_env_assignments", return_value=[]), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux):
                    broccoli_comms_app.reconcile_agents({"planner"})

        new_window_calls = [call for call in calls if call and call[0] == "new-window"]
        self.assertEqual(len(new_window_calls), 1)
        launched = new_window_calls[0][-1]
        self.assertIn("task bootstrap", launched)
        self.assertIn("--scope repo:demo", launched)


    def test_agent_edit_updates_live_agent_config_and_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            cfg_path = cfg_dir / "config.json"
            with cfg_path.open("w") as f:
                json.dump({
                    "agents": {
                        "planner": {
                            "cwd": tmp,
                            "command": "pi --help",
                            "autostart": True,
                        }
                    }
                }, f)

            calls = []
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "window_exists", return_value=True), \
                     mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "kill_agent_window", side_effect=lambda name: calls.append(name) or True), \
                     mock.patch.object(broccoli_comms_app, "reconcile_agents", return_value=["reviewer"]):
                    broccoli_comms_app.agent_edit(argparse.Namespace(
                        name="planner",
                        rename="reviewer",
                        cwd=tmp,
                        scope="repo:test",
                        swarm=["s1"],
                        role=["main"],
                        command=["pi", "--role", "reviewer"],
                        autostart=None,
                    ))

            cfg = json.loads(cfg_path.read_text())
        self.assertEqual(calls, ["planner"])
        self.assertIn("reviewer", cfg["agents"])
        self.assertNotIn("planner", cfg["agents"])
        self.assertEqual(cfg["agents"]["reviewer"]["command"], "pi --role reviewer")
        self.assertEqual(cfg["agents"]["reviewer"]["scope"], "repo:test")
        self.assertEqual(cfg["agents"]["reviewer"]["swarms"], [{"name": "s1", "role": "main"}])

    def test_agent_edit_accepts_options_after_name_and_command_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            cfg_path = cfg_dir / "config.json"
            with cfg_path.open("w") as f:
                json.dump({"agents": {"planner": {"cwd": tmp, "command": "sleep 1", "autostart": True}}}, f)

            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "window_exists", return_value=True), \
                     mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "kill_agent_window", return_value=True), \
                     mock.patch.object(broccoli_comms_app, "reconcile_agents", return_value=["planner"]):
                    broccoli_comms_app.agent_edit(argparse.Namespace(
                        name="planner",
                        rename=None,
                        cwd=None,
                        scope=None,
                        swarm=None,
                        role=None,
                        command=["--cwd", tmp, "--command", "sleep 70", "--no-autostart"],
                        command_string=None,
                        autostart=None,
                    ))

            cfg = json.loads(cfg_path.read_text())
        self.assertEqual(cfg["agents"]["planner"]["command"], "sleep 70")
        self.assertIs(cfg["agents"]["planner"]["autostart"], False)

    def test_agent_edit_requires_live_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with self.assertRaises(SystemExit):
                    broccoli_comms_app.agent_edit(argparse.Namespace(
                        name="planner",
                        rename=None,
                        cwd=tmp,
                        scope=None,
                        swarm=None,
                        role=None,
                        command=["pi"],
                        autostart=None,
                    ))

    def test_run_with_host_publishes_remote_request_without_local_tmux(self):
        rpc_calls = []

        def fake_rpc(method, params=None, **_kwargs):
            rpc_calls.append((method, params or {}))
            if method == "list_trackers":
                return [{"hostname": "remote-host", "tracker_id": "tracker-remote"}]
            if method == "tracker_info":
                return {"tracker_id": "tracker-local"}
            if method == "publish_tracker_event":
                return {"success": True}
            if method == "wait_events":
                if params.get("timeout") == 0:
                    return {"events": [], "last_seq": 0}
                return {"events": [{"type": "remote_run_result", "request_id": "remote-run-fixed1234567", "ok": True}], "last_seq": 1}
            return None

        with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
             mock.patch.object(broccoli_comms_app, "ensure_tmux") as ensure_tmux, \
             mock.patch.object(broccoli_comms_app, "tmux") as tmux, \
             mock.patch.object(broccoli_comms_app.uuid, "uuid4", return_value=mock.Mock(hex="fixed1234567890")), \
             mock.patch.object(broccoli_comms_app, "tracker_rpc", side_effect=fake_rpc):
            broccoli_comms_app.run(argparse.Namespace(name="planner", host="remote-host", timeout=1, cwd=None, scope=None, swarm=None, role=None, command=[]))

        ensure_tmux.assert_not_called()
        tmux.assert_not_called()
        publish = [params for method, params in rpc_calls if method == "publish_tracker_event"][0]
        self.assertEqual(publish["target_tracker_id"], "tracker-remote")
        self.assertEqual(publish["event_type"], "remote_run_request")
        self.assertEqual(publish["payload"]["agent"], "planner")
        self.assertNotIn("cwd", publish["payload"])
        self.assertNotIn("command", publish["payload"])

    def test_run_with_host_forwards_optional_overrides(self):
        def fake_rpc(method, params=None, **_kwargs):
            if method == "list_trackers":
                return [{"hostname": "remote-host", "tracker_id": "tracker-remote"}]
            if method == "tracker_info":
                return {"tracker_id": "tracker-local"}
            if method == "publish_tracker_event":
                fake_rpc.publish = params
                return {"success": True}
            if method == "wait_events":
                return {"events": [{"type": "remote_run_result", "request_id": "remote-run-fixed1234567", "ok": True}], "last_seq": 1}
        fake_rpc.publish = None

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                 mock.patch.object(broccoli_comms_app.uuid, "uuid4", return_value=mock.Mock(hex="fixed1234567890")), \
                 mock.patch.object(broccoli_comms_app, "tracker_rpc", side_effect=fake_rpc):
                broccoli_comms_app.run(argparse.Namespace(name="planner", host="remote-host", timeout=1, cwd=tmp, scope="project:x", swarm=None, role=None, command=["pi", "--fast"]))

        payload = fake_rpc.publish["payload"]
        self.assertEqual(payload["cwd"], tmp)
        self.assertEqual(payload["scope"], "project:x")
        self.assertEqual(payload["command"], ["pi", "--fast"])

    def test_run_with_command_saves_agent_definition_for_future_list(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope="repo:test", swarm=["alpha"], role=["main"], host=None, command=["pi", "--fast"], json=True))

                cfg = json.loads((cfg_dir / "config.json").read_text())
                payload = broccoli_comms_app.agent_list_payload(argparse.Namespace(include_remote=False, configured_only=True, running_only=False, remote_only=False))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn(" --cwd ", launched)
        self.assertEqual(cfg["agents"]["planner"]["cwd"], tmp)
        self.assertEqual(cfg["agents"]["planner"]["command"], "pi --fast")
        self.assertEqual(cfg["agents"]["planner"]["scope"], "repo:test")
        self.assertEqual(cfg["agents"]["planner"]["swarms"], [{"name": "alpha", "role": "main"}])
        self.assertFalse(cfg["agents"]["planner"]["autostart"])
        self.assertIn("planner", payload["agents"])
        self.assertTrue(payload["agents"]["planner"]["is_configured"])

    def test_run_without_explicit_cwd_bootstraps_from_ephemeral_cwd(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app.os, "getcwd", return_value=tmp), \
                     mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=None, scope=None, swarm=None, role=None, host=None, command=["pi"], json=True))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertNotIn(" --cwd ", launched)

    def test_run_without_command_uses_saved_agent_definition(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {"planner": {"cwd": tmp, "command": "sleep 60", "scope": "repo:saved", "immutable": True}}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=None, scope=None, swarm=None, role=None, command=[], json=True))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("sleep 60", launched)
        self.assertIn("--scope repo:saved", launched)
        self.assertIn("BROCCOLI_COMMS_IMMUTABLE_INSTANCE=1", launched)

    def test_run_without_command_uses_default_agent_command_for_new_agent(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, default_agent_command="pi --fast", command=[], json=True))

            cfg = json.loads((cfg_dir / "config.json").read_text())

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("pi --fast", launched)
        self.assertEqual(cfg["agents"]["planner"]["command"], "pi --fast")

    def test_run_explicit_command_takes_precedence_over_saved_and_default_command(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {"planner": {"cwd": tmp, "command": "sleep 60"}}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, default_agent_command="pi --default", command=["pi", "--explicit"], json=True))

            cfg = json.loads((cfg_dir / "config.json").read_text())

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("pi --explicit", launched)
        self.assertNotIn("sleep 60", launched)
        self.assertNotIn("pi --default", launched)
        self.assertEqual(cfg["agents"]["planner"]["command"], "pi --explicit")

    def test_run_accepts_default_agent_command_after_name_before_separator(self):
        calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", return_value=f"{tmp}/agent-workspace"):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=None, scope=None, swarm=None, role=None, host=None, default_agent_command=None, command=["--cwd", tmp, "--default-agent-command", "pi --post-name"], json=True))

        launched = [call for call in calls if call and call[0] == "new-window"][0][-1]
        self.assertIn("pi --post-name", launched)

    def test_agent_list_merges_configured_running_and_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {"configured-only": {"cwd": tmp, "command": "pi"}}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "tmux_up", return_value=True), \
                     mock.patch.object(broccoli_comms_app, "can_connect", return_value=True), \
                     mock.patch.object(broccoli_comms_app, "managed_windows", return_value=[{"managed_agent": "running-only", "window_id": "%1"}]), \
                     mock.patch.object(broccoli_comms_app, "_tracker_agents_with_remote", return_value={"running-only": {"status": "idle"}, "host/remote": {"scope": "remote", "target_address": "host/remote"}}), \
                     mock.patch.object(broccoli_comms_app, "_remote_registry_agents", return_value={}):
                    payload = broccoli_comms_app.agent_list_payload(argparse.Namespace(include_remote=True, configured_only=False, running_only=False, remote_only=False))
        self.assertEqual(set(payload["agents"]), {"configured-only", "running-only", "host/remote"})
        self.assertTrue(payload["agents"]["configured-only"]["is_configured"])
        self.assertTrue(payload["agents"]["running-only"]["running"])
        self.assertTrue(payload["agents"]["host/remote"]["remote"])
        self.assertIn("current_task", payload["agents"]["configured-only"])

    def test_agent_list_exposes_current_task_from_durable_state_and_remote_tracker(self):
        fake_kernel = mock.Mock()
        fake_kernel.tasks.state_list.return_value = [{
            "task_id": "task-1",
            "agent": "configured-only",
            "instance_id": "configured-only@manual",
            "status": "working",
            "current_activity": "coding",
            "next_step": "run focused tests",
        }]
        fake_kernel.tasks.show.return_value = {"task_id": "task-1", "title": "Implement current task card", "status": "working", "next_step": "fallback next"}
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {"configured-only": {"cwd": tmp, "command": "pi"}}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "learning_kernel", return_value=fake_kernel), \
                     mock.patch.object(broccoli_comms_app, "tmux_up", return_value=True), \
                     mock.patch.object(broccoli_comms_app, "can_connect", return_value=True), \
                     mock.patch.object(broccoli_comms_app, "managed_windows", return_value=[]), \
                     mock.patch.object(broccoli_comms_app, "_tracker_agents_with_remote", return_value={"host/remote": {"scope": "remote", "target_address": "host/remote", "current_task": "Remote audit", "current_task_next_step": "ship remote result"}}), \
                     mock.patch.object(broccoli_comms_app, "_remote_registry_agents", return_value={}):
                    payload = broccoli_comms_app.agent_list_payload(argparse.Namespace(include_remote=True, configured_only=False, running_only=False, remote_only=False))
        local = payload["agents"]["configured-only"]
        remote = payload["agents"]["host/remote"]
        self.assertEqual(local["current_task"], "Implement current task card")
        self.assertEqual(local["current_task_id"], "task-1")
        self.assertEqual(local["current_task_next_step"], "run focused tests")
        self.assertEqual(remote["current_task"], "Remote audit")
        self.assertEqual(remote["current_task_next_step"], "ship remote result")

    def test_agent_copy_saves_immutable_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {"planner": {"cwd": tmp, "command": "pi --fast", "scope": "repo:test"}}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                broccoli_comms_app.agent_copy(argparse.Namespace(source="planner", new_name="planner-copy", immutable=True, replace=False, json=True))
                cfg = json.loads((cfg_dir / "config.json").read_text())
        self.assertTrue(cfg["agents"]["planner-copy"]["immutable"])
        self.assertTrue(cfg["agents"]["planner-copy"]["non_learning"])
        self.assertEqual(cfg["agents"]["planner-copy"]["command"], "pi --fast")

    def test_immutable_learning_instance_checks_saved_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.json").write_text(json.dumps({"agents": {"copied": {"cwd": tmp, "command": "pi", "immutable": True}}}))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                self.assertTrue(broccoli_comms_app.immutable_learning_instance("copied", None))

    def test_invalid_swarm_role_is_rejected(self):
        with self.assertRaises(SystemExit):
            broccoli_comms_app.parse_swarm_args(argparse.Namespace(swarm=["s1"], role=["worker"]))

    def test_repeated_swarm_role_pairs_are_normalized(self):
        swarms = broccoli_comms_app.parse_swarm_args(argparse.Namespace(
            swarm=["backend-fix", "review"],
            role=["main", "subagent"],
        ))
        self.assertEqual(swarms, [
            {"name": "backend-fix", "role": "main"},
            {"name": "review", "role": "subagent"},
        ])

    def test_normalize_config_accepts_top_level_swarms(self):
        cfg = broccoli_comms_app.normalize_config({
            "agents": {"planner": {"cwd": "/repo", "command": "pi"}},
            "swarms": {"backend-fix": {"members": [{"agent": "planner", "role": "main"}]}},
        })
        self.assertEqual(cfg["swarms"]["backend-fix"]["members"], [{"agent": "planner", "role": "main"}])

    def test_normalize_config_rejects_invalid_top_level_swarm_role(self):
        with self.assertRaises(ValueError):
            broccoli_comms_app.normalize_config({
                "agents": {"planner": {}},
                "swarms": {"backend-fix": {"members": [{"agent": "planner", "role": "worker"}]}},
            })

    def test_managed_launch_command_builds_wrapper_invocation(self):
        with mock.patch.object(broccoli_comms_app, "broccoli_comms_launcher_argv", return_value=["broccoli-comms"]), \
             mock.patch.object(broccoli_comms_app, "managed_track_env_assignments", return_value=[]), \
             mock.patch.object(broccoli_comms_app, "wrapper_path", return_value="/usr/bin/agent-wrapper"):
            command = broccoli_comms_app.managed_agent_launch_command(
                "planner",
                "/work tree",
                "pi --flag",
                [{"name": "backend-fix", "role": "main"}],
            )

        self.assertIn("/usr/bin/agent-wrapper", command)
        self.assertIn("SUGGESTED_AGENT_NAME=planner", command)
        self.assertIn('AGENT_SWARMS_JSON=\'[{"name":"backend-fix","role":"main"}]\'', command)
        self.assertIn("pi --flag", command)

    def test_managed_launch_command_wraps_command_when_scope_is_configured(self):
        with mock.patch.object(broccoli_comms_app, "broccoli_comms_launcher_argv", return_value=["broccoli-comms"]), \
             mock.patch.object(broccoli_comms_app, "managed_track_env_assignments", return_value=[]):
            command = broccoli_comms_app.managed_agent_launch_command(
                "planner",
                "/work tree",
                "pi --flag",
                [],
                launch_cwd="/tmp/ephemeral",
                scope="repo:test",
            )

        self.assertIn("task bootstrap", command)
        self.assertIn("--scope repo:test", command)
        self.assertIn("exec \"$@\"", command)

    def test_build_bootstrap_track_command_supports_multi_argv_launcher(self):
        with mock.patch.object(broccoli_comms_app, "broccoli_comms_launcher_argv", return_value=["/usr/bin/python3", "/path with spaces/broccoli-comms"]):
            command = broccoli_comms_app._build_bootstrap_track_command(
                "planner",
                "/src/tree",
                "repo:test",
                ["pi", "--flag"],
                "/tmp/agent-workspace",
            )

        self.assertEqual(command[:2], ["bash", "-lc"])
        self.assertIn("/usr/bin/python3", command[2])
        self.assertIn("'/path with spaces/broccoli-comms'", command[2])
        self.assertIn("--scope", command[2])
        self.assertIn("repo:test", command[2])

    def test_bootstrap_context_writes_agents_md_with_absolute_context_and_skill_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "agents_md": "# Agent Operating Contract\n",
                "memory": [
                    {"memory_id": "mem-skill", "type": "skill", "title": "Deploy Helper", "body": "secret detailed steps", "metadata": {"description": "Safely deploy things"}},
                    {"memory_id": "mem-habit", "type": "habit", "title": "Review", "body": "Run tests"},
                    {"memory_id": "mem-expert", "type": "expertise", "title": "System", "body": "Architecture"},
                ],
                "chain_summary": {"summary_id": "sum-1", "task_chain_id": "chain-1", "root_task_id": "root-1", "summary": "Completed previous chain."},
            }
            result = broccoli_comms_app.write_bootstrap_context_files(payload, tmp)
            agents = (Path(result["context_dir"]) / "AGENTS.md").read_text()
            context = Path(result["context_dir"])
            self.assertIn(str(context / "memory.md"), agents)
            self.assertNotIn(str(context / "habits.md"), agents)
            self.assertNotIn("habits.md", agents)
            self.assertIn(str(context / "expertise.md"), agents)
            self.assertIn("Deploy Helper", agents)
            self.assertIn("Safely deploy things", agents)
            self.assertIn("broccoli-comms memory show mem-skill --json", agents)
            self.assertIn(str(context / "skills" / "Deploy-Helper" / "SKILL.md"), agents)
            self.assertIn("Use `broccoli-comms memory ...` commands", agents)
            self.assertIn("Retained habits are mandatory operating instructions", agents)
            self.assertIn("Task/status/result changes auto-notify role-relevant participants", agents)
            self.assertIn("review/done -> reviewer/verifier", agents)
            self.assertIn("validated -> assignee plus coordinator/requester", agents)
            self.assertIn("Suppress self-notifications", agents)
            self.assertIn("combine all applicable roles/reasons", agents)
            self.assertIn("Ordinary single-task completion uses task/status/result flow", agents)
            self.assertIn("Reserve `task submit-completion` for task-chain", agents)
            self.assertIn("summarize-chain <task_chain_id>", agents)
            self.assertIn("post-validation summary", agents)
            memory = (context / "memory.md").read_text()
            self.assertIn("Latest task-chain summary", memory)
            self.assertIn("Completed previous chain.", memory)
            self.assertIn("Embedded retained habits from durable memory", agents)
            self.assertIn("Review", agents)
            self.assertIn("Run tests", agents)
            self.assertIn("before completion, validation, review handoff", agents)
            self.assertNotIn("source: `" + str(context / "habits.md") + "`", agents)
            self.assertNotIn("secret detailed steps", agents)

    def test_bootstrap_context_writes_jetski_agents_rules_and_skills_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "agents_md": "# Agent Operating Contract\n",
                "memory": [
                    {"memory_id": "mem-skill", "type": "skill", "title": "Deploy Helper", "body": "steps", "metadata": {"description": "Deploy safely"}},
                    {"memory_id": "mem-expert", "type": "expertise", "title": "System", "body": "Architecture"},
                ],
            }
            with mock.patch.dict(os.environ, {"BROCCOLI_AGENTS_DIR": ".agents", "BROCCOLI_COMMS_CONTEXT_LAYOUT": "jetski"}, clear=False):
                result = broccoli_comms_app.write_bootstrap_context_files(payload, tmp)
            context = Path(result["context_dir"])
            self.assertEqual(context, Path(tmp))
            self.assertTrue((context / "AGENTS.md").exists())
            self.assertTrue((context / ".agents" / "rules" / "memory.md").exists())
            self.assertTrue((context / ".agents" / "rules" / "expertise.md").exists())
            self.assertTrue((context / ".agents" / "skills" / "Deploy-Helper" / "SKILL.md").exists())
            agents = (context / "AGENTS.md").read_text()
            self.assertIn(str(context / ".agents" / "rules" / "memory.md"), agents)
            self.assertIn(str(context / ".agents" / "rules" / "expertise.md"), agents)
            self.assertIn(str(context / ".agents" / "skills" / "Deploy-Helper" / "SKILL.md"), agents)
            self.assertFalse((context / ".agents" / "AGENTS.md").exists())

    def test_bootstrap_agents_md_removes_habits_startup_read_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = broccoli_comms_app.agent_contract("a", "a@s1", tmp)
            agents = broccoli_comms_app._bootstrap_agents_md(base, Path(tmp), [], [{"memory_id": "mem-h", "type": "habit", "scope": "global", "title": "Always review", "body": "Send review handoff before done."}])
            self.assertNotIn("read generated `memory.md`, `habits.md`, and `expertise.md`", agents)
            self.assertNotIn("active records in `habits.md`", agents)
            self.assertNotIn("habits.md", agents)
            self.assertIn("read generated `memory.md` and `expertise.md`", agents)
            self.assertIn("Embedded retained habits from durable memory", agents)
            self.assertIn("Always review", agents)
            self.assertIn("Send review handoff before done.", agents)
            self.assertIn("mandatory operating instructions", agents)

    def test_bootstrap_does_not_fall_back_to_unrelated_latest_chain_summary(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}, clear=False), mock.patch.object(broccoli_comms_app, "duplicate_profile_instances", return_value=[]):
            k = broccoli_comms_app.learning_kernel()
            active = k.task_create(title="active", assigned_agent="a", status="ready")
            other = k.task_create(title="other", assigned_agent="b")
            k.state_set(other["task_id"], "b", task_chain_id="other-chain", root_task_id=other["task_id"], status="working")
            k.summarize_chain("other-chain", actor="b")

            args = argparse.Namespace(agent="a", scope=None, cwd=tmp, instance="a@s1", write_context_dir=None, json=True)
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                broccoli_comms_app.task_bootstrap(args)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["task"]["task_id"], active["task_id"])
            self.assertIsNone(payload.get("chain_summary"))

    def test_bootstrap_agents_md_shows_ephemeral_and_source_cwds(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as ctx, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}, clear=False), mock.patch.object(broccoli_comms_app, "duplicate_profile_instances", return_value=[]):
            args = argparse.Namespace(agent="a", scope=None, cwd=src, instance="a@s1", write_context_dir=ctx, json=True)
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                broccoli_comms_app.task_bootstrap(args)
            agents = (Path(ctx) / "AGENTS.md").read_text()
            self.assertIn(f"Ephemeral cwd: {ctx}", agents)
            self.assertIn(f"Launch/source cwd: {src}", agents)
            self.assertIn("For file/project queries not related to agent memory", agents)

    def test_default_skills_include_memory_audit_and_home_manager_install_paths(self):
        root = Path(__file__).resolve().parents[1]
        skill = (root / "skills" / "agent-memory-audit" / "SKILL.md").read_text()
        self.assertIn("memory propose MEMORY_ID", skill)
        self.assertIn("--archive", skill)
        self.assertIn("memory decide PROPOSAL_ID approve", skill)
        self.assertNotIn("There is no first-class", skill)
        readme = (root / "skills" / "README.md").read_text()
        self.assertIn("agent-memory-audit", readme)
        module = (root / "modules" / "home-manager.nix").read_text()
        self.assertIn("defaultSkills = mkOption", module)
        self.assertIn('".pi/agent/skills/agent-memory-audit/SKILL.md"', module)
        self.assertIn('".claude/skills/agent-memory-audit/SKILL.md"', module)
        self.assertIn('".gemini/skills/agent-memory-audit/SKILL.md"', module)
        self.assertIn('".agents/skills/agent-memory-audit/SKILL.md"', module)

    def test_home_manager_provider_defaults_set_launch_flags_and_agent_roots(self):
        text = (Path(__file__).resolve().parents[1] / "modules" / "home-manager.nix").read_text()
        for want in [
            "configTomlFormat = pkgs.formats.toml {}",
            "providerSpecType = lib.types.submodule",
            "providers = mkOption",
            'xdg.configFile."broccoli-comms/config.toml".source = configTomlFormat.generate',
            '"context-layout" = provider.contextLayout',
            '"agent-root-dir" = provider.agentRootDir',
            '"auto-accept-flag" = provider.autoAcceptFlag',
            '"prompt-flag-name" = provider.promptFlagName',
            '"initial-message" = provider.initialMessage',
            '"tmux-submit-key" = provider.tmuxSubmitKey',
            'cmd = "/google/bin/releases/jetski-devs/tools/cli"',
            'agentsDir = ".agents"',
            'contextLayout = "jetski"',
            'autoAcceptFlag = "--dangerously-skip-permissions"',
            'promptFlagName = "--prompt-interactive"',
            'defaultInitialMessage = "Check if any task are assigned to you, if yes, start working on them, else be on standby"',
            'remotePaneInput.enable = mkOption { type = types.bool; default = true;',
            'remote_pane_input_enabled = cfg.tracker.remotePaneInput.enable;',
            'BROCCOLI_COMMS_REMOTE_PANE_INPUT_REGISTRY_ENABLED = "1";',
            'cmd = "pi"',
            'cmd = "codex"',
            'autoAcceptFlag = "--dangerously-bypass-approvals-and-sandbox"',
            'cmd = "claude"',
            'agentRootDir = "${config.home.homeDirectory}/agents-root"',
            'agentRootDir = "${config.home.homeDirectory}/.agents-root"',
        ]:
            self.assertIn(want, text)
        self.assertNotIn('xdg.configFile."broccoli-comms/config.toml".text =', text)

    def test_run_passes_provider_agent_root_dir_to_workspace_builder(self):
        calls = []
        workspace_calls = []

        def fake_tmux(*cmd, **kwargs):
            calls.append(list(cmd))
            if cmd and cmd[0] == "new-window":
                return mock.Mock(returncode=0, stdout="%42\t%1", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        def fake_workspace(*args, **kwargs):
            workspace_calls.append((args, kwargs))
            return f"{tmp}/agent-workspace"

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text('[providers.claude]\ncmd = "claude"\nagent-root-dir = "~/.agents-root"\n')
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp}):
                with mock.patch.object(broccoli_comms_app, "ensure_tracker"), \
                     mock.patch.object(broccoli_comms_app, "ensure_tmux"), \
                     mock.patch.object(broccoli_comms_app, "window_exists", return_value=False), \
                     mock.patch.object(broccoli_comms_app, "tmux", side_effect=fake_tmux), \
                     mock.patch.object(broccoli_comms_app, "ephemeral_agent_workspace", side_effect=fake_workspace):
                    broccoli_comms_app.run(argparse.Namespace(name="planner", cwd=tmp, scope=None, swarm=None, role=None, host=None, command=["claude"], json=True))

        self.assertEqual(workspace_calls[0][1]["agent_root_dir"], "~/.agents-root")

    def test_ephemeral_agent_workspace_writes_agents_md_from_config_template(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text('[learning]\nagent_contract_template = "hello {agent} {cwd}"\n')
            workspace = broccoli_comms_app.ephemeral_agent_workspace("planner")
        body = (Path(workspace) / "AGENTS.md").read_text()
        self.assertEqual(body, f"hello planner {workspace}")
        self.assertIn("/broccoli-agents/planner/", workspace)

    def test_ephemeral_agent_workspace_jetski_layout_keeps_agents_md_at_root(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TMPDIR": tmp}, clear=False):
            workspace = Path(broccoli_comms_app.ephemeral_agent_workspace("planner", agents_dir=".agents", context_layout="jetski"))
            self.assertTrue((workspace / "AGENTS.md").exists())
            self.assertFalse((workspace / ".agents" / "AGENTS.md").exists())

    def test_ephemeral_agent_workspace_uses_temp_root_when_agent_root_unset(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
            first = Path(broccoli_comms_app.ephemeral_agent_workspace("planner"))
            second = Path(broccoli_comms_app.ephemeral_agent_workspace("planner"))
        self.assertNotEqual(first, second)
        self.assertIn("broccoli-agents", str(first))
        self.assertEqual(first.parent.name, "planner")

    def test_ephemeral_agent_workspace_uses_configured_agent_root_dir(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
            cfg_dir = Path(tmp) / "broccoli-comms"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.toml").write_text(f'[paths]\nagent-root-dir = "{root}"\n[learning]\nagent_contract_template = "hello {{agent}} {{cwd}}"\n')
            workspace = Path(broccoli_comms_app.ephemeral_agent_workspace("planner"))
            again = Path(broccoli_comms_app.ephemeral_agent_workspace("planner"))
            self.assertEqual(workspace, Path(root) / "planner")
            self.assertEqual(again, workspace)
            self.assertEqual((workspace / "AGENTS.md").read_text(), f"hello planner {workspace}")

    def test_task_update_broadcasts_to_remote_agent_communicator_instances(self):
        calls = []

        def fake_tracker_rpc(method, params=None, **kwargs):
            calls.append((method, params or {}, kwargs))
            if method == "tracker_info":
                return {"tracker_id": "local-tracker"}
            if method == "list_trackers":
                return [
                    {"tracker_id": "local-tracker", "hostname": "local-host", "status": "active"},
                    {"tracker_id": "remote-tracker", "hostname": "remote-host", "status": "active"},
                    {"tracker_id": "gone-tracker", "hostname": "gone-host", "status": "gone"},
                ]
            if method == "send_message":
                return {"success": True}
            return None

        task = {"task_id": "task-1", "title": "Remote visible", "status": "review", "assigned_agent": "remote-host/worker", "participants": []}
        with mock.patch.object(broccoli_comms_app, "tracker_rpc", side_effect=fake_tracker_rpc):
            result = broccoli_comms_app.notify_task_update(task, "coder", {"status": "review"})

        send_calls = [(method, params) for method, params, _ in calls if method == "send_message"]
        self.assertEqual(send_calls[0][1]["agent_name"], "agent-communicator")
        self.assertEqual(send_calls[1][1]["target_address"], "remote-host/agent-communicator")
        self.assertEqual(send_calls[1][1]["metadata"]["delivery_scope"], "shared_service_broadcast")
        self.assertEqual(send_calls[1][1]["metadata"]["content_type"], "application/vnd.broccoli.task-update+json")
        self.assertEqual(send_calls[1][1]["metadata"]["task_assigned_agent"], "remote-host/worker")
        self.assertTrue(result["sent"])
        self.assertEqual(result["ui_broadcast"]["remote"][0]["target"], "remote-host/agent-communicator")

    def test_agent_assign_swarm_calls_live_assignment_rpc(self):
        args = argparse.Namespace(swarm="backend-fix", main="planner", subagent=["coder-a"], json=True)
        with mock.patch.object(broccoli_comms_app, "ensure_tracker") as ensure_tracker, \
             mock.patch.object(broccoli_comms_app, "tracker_rpc", return_value={"ok": True, "swarm": "backend-fix", "members": []}) as tracker_rpc, \
             mock.patch("sys.stdout", io.StringIO()):
            broccoli_comms_app.agent_assign_swarm(args)
        ensure_tracker.assert_called_once()
        tracker_rpc.assert_called_once_with("assign_live_swarm", {"swarm": "backend-fix", "main": "planner", "subagents": ["coder-a"]})

    def test_agent_start_swarm_launches_top_level_configured_members(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp, "BROCCOLI_COMMS_DISABLE_CONFIG_REGISTRIES": "1"}, clear=False):
            broccoli_comms_app.save_config({
                "agents": {
                    "planner": {"cwd": tmp, "command": "pi"},
                    "coder-a": {"cwd": tmp, "command": "pi"},
                },
                "swarms": {
                    "backend-fix": {"members": [{"agent": "planner", "role": "main"}, {"agent": "coder-a", "role": "subagent"}]}
                },
            })
            with mock.patch.object(broccoli_comms_app, "ensure_tracker") as ensure_tracker, \
                 mock.patch.object(broccoli_comms_app, "ensure_tmux") as ensure_tmux, \
                 mock.patch.object(broccoli_comms_app, "reconcile_agents", return_value=["coder-a"]) as reconcile, \
                 mock.patch("sys.stdout", io.StringIO()) as out:
                broccoli_comms_app.agent_start_swarm(argparse.Namespace(swarm="backend-fix", json=True))
        ensure_tracker.assert_called_once()
        ensure_tmux.assert_called_once()
        reconcile.assert_called_once_with({"planner", "coder-a"})
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["swarm"], "backend-fix")
        self.assertEqual(payload["members"], ["planner", "coder-a"])
        self.assertEqual(payload["launched"], ["coder-a"])
        self.assertEqual(payload["already_running"], ["planner"])

    def test_agent_start_swarm_rejects_legacy_per_agent_membership(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp, "XDG_RUNTIME_DIR": tmp, "BROCCOLI_COMMS_DISABLE_CONFIG_REGISTRIES": "1"}, clear=False):
            broccoli_comms_app.save_config({"agents": {"planner": {"cwd": tmp, "command": "pi", "swarms": [{"name": "backend-fix", "role": "main"}]}}})
            with self.assertRaises(SystemExit) as cm:
                broccoli_comms_app.agent_start_swarm(argparse.Namespace(swarm="backend-fix", json=True))
        self.assertIn("top-level swarms", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
