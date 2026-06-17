import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

_CTL_PATH = os.path.join(os.path.dirname(__file__), "agent-tracker-ctl.py")
_spec = importlib.util.spec_from_file_location("agent_tracker_ctl", _CTL_PATH)
ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctl)


class TestAgentTrackerCtl(unittest.TestCase):
    def setUp(self):
        self.load_config_patcher = mock.patch("config.load_config", return_value={})
        self.mock_load_config = self.load_config_patcher.start()

    def tearDown(self):
        self.load_config_patcher.stop()

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_format_status_bar_shows_green_registry_indicator_when_connected(self):
        bar = ctl.format_status_bar(
            {"agent1": {"tmux_pane": "%1", "status": "idle"}},
            "%2",
            registry_connected=True,
        )
        self.assertIn("Active Agents:", bar)
        self.assertTrue(bar.startswith("#[fg=#2ac3de,bold]Active Agents:"))
        self.assertIn("#[align=right]#[range=user|agent-registries]#[fg=#9ece6a,bold]●#[norange]#[default]", bar)

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_format_status_bar_shows_red_registry_indicator_when_disconnected(self):
        bar = ctl.format_status_bar(
            {"agent1": {"tmux_pane": "%1", "status": "idle"}},
            "%2",
            registry_connected=False,
        )
        self.assertTrue(bar.startswith("#[fg=#2ac3de,bold]Active Agents:"))
        self.assertIn("#[align=right]#[range=user|agent-registries]#[fg=#db4b4b,bold]●#[norange]#[default]", bar)

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_format_status_bar_filters_invalid_panes_but_allows_agent_communicator_with_pane(self):
        bar = ctl.format_status_bar(
            {
                "agent-communicator": {"tmux_pane": "%9", "status": "idle"},
                "mailbox": {"status": "idle"},
                "remote/agent": {"tmux_pane": "remote-pane", "status": "working"},
                "local": {"tmux_pane": "%1", "status": "idle"},
            },
            "%9",
            registry_connected=True,
        )
        self.assertIn("local", bar)
        self.assertIn("agent-communicator", bar)
        self.assertIn("#[fg=#e0af68,bold]agent-communicator", bar)
        self.assertNotIn("mailbox", bar)
        self.assertNotIn("remote/agent", bar)
        self.assertIn("range=user|agent:%1", bar)
        self.assertIn("range=user|agent:%9", bar)
        self.assertNotIn("range=user|agent:remote-pane", bar)

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_format_status_bar_does_not_highlight_invalid_current_pane(self):
        bar = ctl.format_status_bar(
            {"agent1": {"tmux_pane": "%1", "status": "idle"}},
            "remote-pane",
            registry_connected=True,
        )
        self.assertIn("#[fg=#9ece6a,bold]agent1", bar)
        self.assertNotIn("#[fg=#e0af68,bold]agent1", bar)

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_format_status_bar_returns_empty_when_no_valid_agent_panes(self):
        bar = ctl.format_status_bar(
            {"agent-communicator": {}, "remote": {"tmux_pane": "host/%1"}},
            "%9",
            registry_connected=True,
        )
        self.assertEqual(bar, "")

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_format_status_bar_shows_dot_for_each_registry(self):
        bar = ctl.format_status_bar(
            {"agent1": {"tmux_pane": "%1", "status": "idle"}},
            "%2",
            registry_states=[("local", True), ("mundus", False)],
        )
        self.assertTrue(bar.startswith("#[fg=#2ac3de,bold]Active Agents:"))
        self.assertIn("#[align=right]#[range=user|agent-registries]#[fg=#9ece6a,bold]●#[fg=#db4b4b,bold]●#[norange]#[default]", bar)

    def test_registry_connection_states_follow_config_order(self):
        status = {"registries": {"local": {"connected": True, "last_success": 100.0}, "mundus": {"connected": False}}}
        with mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": '[{"name":"local","url":"http://127.0.0.1:8000"},{"name":"mundus","url":"https://agents.mundus.in"}]', "AGENT_REGISTRY_HEARTBEAT_SECONDS": "30"}, clear=True):
            self.assertEqual(ctl.registry_connection_states(status=status, now=120.0), [("local", True), ("mundus", False)])

    def test_fetch_registry_agents_keys_by_hostname_and_sends_auth(self):
        class FakeResponse:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return json.dumps({"agents": [{"hostname": "host1", "name": "agent1", "agent_id": "a1", "status": "idle"}]}).encode()

        seen = {}
        def fake_urlopen(req, timeout=0):
            seen["url"] = req.full_url
            seen["auth"] = req.headers.get("Authorization")
            return FakeResponse()

        registries = json.dumps([{"name": "default", "url": "https://registry.example/", "token": "secret"}])
        with mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": registries}, clear=True), \
             mock.patch.object(ctl.urllib.request, "urlopen", fake_urlopen):
            agents = ctl.fetch_registry_agents()

        self.assertEqual(seen, {"url": "https://registry.example/agents", "auth": "Bearer secret"})
        self.assertIn("host1/agent1", agents)
        self.assertEqual(agents["host1/agent1"]["scope"], "remote")
        self.assertEqual(agents["host1/agent1"]["target_address"], "host1/agent1")

    def test_registry_configs_reads_token_file(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="secret\n")):
            registries = json.dumps([{"name": "secure", "url": "https://secure.example", "token-file": "/tmp/token"}])
            with mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": registries}, clear=True):
                configs = ctl.registry_configs()
        self.assertEqual(configs[0]["token"], "secret")

    def test_fetch_registry_agents_prefixes_ambiguous_cross_registry_keys(self):
        class FakeResponse:
            status = 200
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return json.dumps(self.payload).encode()

        def fake_urlopen(req, timeout=0):
            agent_id = "a1" if req.full_url.startswith("https://r1.example") else "a2"
            return FakeResponse({"agents": [{"hostname": "shared-host", "name": "agent", "agent_id": agent_id}]})

        registries = json.dumps([
            {"name": "r1", "url": "https://r1.example/"},
            {"name": "r2", "url": "https://r2.example"},
        ])
        with mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": registries}, clear=True), \
             mock.patch.object(ctl.urllib.request, "urlopen", fake_urlopen):
            agents = ctl.fetch_registry_agents()

        self.assertNotIn("shared-host/agent", agents)
        self.assertIn("r1:shared-host/agent", agents)
        self.assertIn("r2:shared-host/agent", agents)

    def test_fetch_registry_agents_merges_multiple_registries(self):
        class FakeResponse:
            status = 200
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return json.dumps(self.payload).encode()

        seen = []
        def fake_urlopen(req, timeout=0):
            seen.append((req.full_url, req.headers.get("Authorization")))
            if req.full_url.startswith("https://r1.example"):
                return FakeResponse({"agents": [{"hostname": "host1", "name": "agent1", "agent_id": "a1"}]})
            return FakeResponse({"agents": [{"hostname": "host2", "name": "agent2", "agent_id": "a2"}]})

        registries = json.dumps([
            {"name": "r1", "url": "https://r1.example/", "token": "one"},
            {"name": "r2", "url": "https://r2.example", "token": "two"},
        ])
        with mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": registries}, clear=True), \
             mock.patch.object(ctl.urllib.request, "urlopen", fake_urlopen):
            agents = ctl.fetch_registry_agents()

        self.assertEqual(seen, [("https://r1.example/agents", "Bearer one"), ("https://r2.example/agents", "Bearer two")])
        self.assertIn("host1/agent1", agents)
        self.assertIn("host2/agent2", agents)
        self.assertEqual(agents["host1/agent1"]["registry_name"], "r1")
        self.assertEqual(agents["host2/agent2"]["registry_name"], "r2")

    def test_merge_registry_agents_preserves_local_and_adds_remote(self):
        merged = ctl.merge_registry_agents(
            {"local-agent": {"agent_id": "l1"}},
            {"host1/remote-agent": {"agent_id": "r1", "scope": "remote"}},
        )
        self.assertEqual(merged["local-agent"]["scope"], "local")
        self.assertEqual(merged["host1/remote-agent"]["scope"], "remote")

    def test_merge_registry_agents_skips_remote_duplicate_local_agent_id(self):
        merged = ctl.merge_registry_agents(
            {"local-agent": {"agent_id": "same-id"}},
            {
                "this-host/local-agent": {"agent_id": "same-id", "scope": "remote"},
                "other-host/remote-agent": {"agent_id": "remote-id", "scope": "remote"},
            },
        )
        self.assertIn("local-agent", merged)
        self.assertNotIn("this-host/local-agent", merged)
        self.assertIn("other-host/remote-agent", merged)

    def test_is_registry_connected_requires_fresh_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = os.path.join(tmp, "registry-status.json")
            with open(status_path, "w") as f:
                json.dump({"registries": {"mundus": {"connected": True, "last_success": 100.0}}}, f)
            with mock.patch.object(ctl, "REGISTRY_STATUS_PATH", status_path), \
                 mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": '[{"name":"mundus","url":"https://agents.mundus.in"}]', "AGENT_REGISTRY_HEARTBEAT_SECONDS": "30"}, clear=True):
                self.assertTrue(ctl.is_registry_connected(now=120.0))
                self.assertFalse(ctl.is_registry_connected(now=200.0))

    def test_is_registry_connected_accepts_fresh_registry_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = os.path.join(tmp, "registry-status.json")
            with open(status_path, "w") as f:
                json.dump({"registries": {"corp": {"connected": True, "last_success": 100.0}}}, f)
            with mock.patch.object(ctl, "REGISTRY_STATUS_PATH", status_path), \
                 mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": '[{"name":"corp","url":"https://corp.example"}]', "AGENT_REGISTRY_HEARTBEAT_SECONDS": "30"}, clear=True):
                self.assertTrue(ctl.is_registry_connected(now=120.0))
                self.assertFalse(ctl.is_registry_connected(now=200.0))

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_format_registry_status_shows_each_registry(self):
        out = ctl.format_registry_status({
            "registries": {
                "corp": {"connected": True, "registry_url": "https://corp.example", "last_success": 100.0, "status_code": 200},
                "lab": {"connected": False, "registry_url": "https://lab.example", "last_error": "heartbeat:unreachable"},
            }
        }, now=130.0)
        self.assertIn("corp: connected, last_success=30s ago", out)
        self.assertIn("lab: disconnected, last_success=never", out)
        self.assertIn("heartbeat:unreachable", out)

    def test_format_registry_status_filters_to_configured_registries(self):
        status = {
            "registries": {
                "mundus": {"connected": True, "registry_url": "https://agents.mundus.in", "last_success": 1},
                "e2e": {"connected": True, "registry_url": "http://127.0.0.1:18000", "last_success": 1},
            }
        }
        with mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": '[{"name":"e2e","url":"http://127.0.0.1:18000"}]'}, clear=True):
            rendered = ctl.format_registry_status(status, now=2)
        self.assertIn("e2e:", rendered)
        self.assertNotIn("mundus", rendered)

    def test_registry_status_payload_prunes_stale_registries(self):
        import registry_client
        e2e_client = registry_client.RegistryClient(name="e2e", url="http://127.0.0.1:18000")
        existing = {
            "registries": {
                "mundus": {"connected": True, "registry_url": "https://agents.mundus.in", "last_success": 1},
                "e2e": {"connected": False, "registry_url": "http://127.0.0.1:18000"},
            }
        }
        with mock.patch.object(registry_client, "load_registry_clients", return_value=[e2e_client]):
            payload = registry_client._registry_status_payload(200, "heartbeat", existing, e2e_client)
        self.assertEqual(set(payload["registries"]), {"e2e"})

    def test_send_message_does_not_treat_target_name_as_sender(self):
        import rpc_handler
        caller_pid = os.getpid()
        agents = {
            "local-alpha": {"agent_id": "alpha-id", "uuid": "alpha-id", "wrapper_pid": caller_pid, "agent_type": "pi", "agent_cmd": "pi"},
            "local-beta": {"agent_id": "beta-id", "uuid": "beta-id", "wrapper_pid": caller_pid + 100000, "agent_type": "pi", "agent_cmd": "pi"},
        }
        delivered = {}

        def get_agent(name_or_id):
            if name_or_id in agents:
                return agents[name_or_id]
            for info in agents.values():
                if info.get("agent_id") == name_or_id:
                    return info
            return None

        def name_by_id(agent_id):
            for name, info in agents.items():
                if info.get("agent_id") == agent_id:
                    return name
            return None

        def capture_delivery(agent_name, payload, sender_name, verify=False):
            delivered.update(agent_name=agent_name, payload=payload, sender_name=sender_name, verify=verify)
            return agent_name

        with mock.patch.object(rpc_handler.state, "get_all_agents", return_value=agents), \
             mock.patch.object(rpc_handler.state, "get_agent", side_effect=get_agent), \
             mock.patch.object(rpc_handler.state, "get_agent_name_by_id", side_effect=name_by_id), \
             mock.patch.object(rpc_handler.state, "get_agent_name_by_pane", return_value=None), \
             mock.patch.object(rpc_handler.state, "normalize_model_type", return_value="pi"), \
             mock.patch.object(rpc_handler, "deliver_local_message", side_effect=capture_delivery):
            result = rpc_handler.handle_send_message({"agent_name": "local-beta", "message": "hello"}, caller_pid=caller_pid)

        self.assertTrue(result)
        self.assertEqual(delivered["agent_name"], "local-beta")
        self.assertEqual(delivered["sender_name"], "local-alpha")
        self.assertEqual(delivered["payload"]["sender"], "local-alpha")
        self.assertNotEqual(delivered["payload"]["sender"], "local-beta")

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_send_message_parser_registration(self):
        parser = ctl.build_parser()
        parsed = parser.parse_args(["send-message", "alice", "hello", "--swarm-context", "backend-fix"])
        self.assertEqual(parsed.subcommand, "send-message")
        self.assertEqual(parsed.target, "alice")
        self.assertEqual(parsed.message, "hello")
        self.assertEqual(parsed.swarm_context, "backend-fix")
        self.assertFalse(parsed.interrupt)

        # Verify --interrupt flag
        parsed2 = parser.parse_args(["send-message", "alice", "hello", "--interrupt"])
        self.assertTrue(parsed2.interrupt)

        # Verify --interupt alias
        parsed3 = parser.parse_args(["send-message", "alice", "hello", "--interupt"])
        self.assertTrue(parsed3.interrupt)

    @mock.patch("ctl_commands.send_message.call_rpc")
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_send_message_handler_forwards_swarm_context(self, mock_call_rpc):
        from ctl_commands import send_message

        mock_call_rpc.return_value = {"success": True}
        args = mock.Mock(target="alice", message="hello", agent_id=None, swarm_context="backend-fix", verify=False, interrupt=False)

        with mock.patch("builtins.print"):
            send_message.handle(args)

        mock_call_rpc.assert_called_once_with("send_message", {
            "message": "hello",
            "agent_name": "alice",
            "swarm_context": "backend-fix",
        })

    @mock.patch("ctl_commands.send_message.call_rpc")
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_send_message_handler_forwards_interrupt(self, mock_call_rpc):
        from ctl_commands import send_message

        mock_call_rpc.return_value = {"success": True}
        args = mock.Mock(target="alice", message="hello", agent_id=None, swarm_context=None, verify=False, interrupt=True)

        with mock.patch("builtins.print"):
            send_message.handle(args)

        mock_call_rpc.assert_called_once_with("send_message", {
            "message": "hello",
            "agent_name": "alice",
            "interrupt": True,
        })

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_assign_swarm_parser_registration(self):
        parser = ctl.build_parser()
        parsed = parser.parse_args(["assign-swarm", "backend-fix", "--main", "planner", "--subagent", "coder-a", "--subagent", "reviewer", "--json"])
        self.assertEqual(parsed.subcommand, "assign-swarm")
        self.assertEqual(parsed.swarm, "backend-fix")
        self.assertEqual(parsed.main, "planner")
        self.assertEqual(parsed.subagent, ["coder-a", "reviewer"])
        self.assertTrue(parsed.json)

    @mock.patch("ctl_commands.assign_swarm.call_rpc")
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_assign_swarm_handler_forwards_live_assignment(self, mock_call_rpc):
        from ctl_commands import assign_swarm

        mock_call_rpc.return_value = {"ok": True, "swarm": "backend-fix", "members": [{"agent": "planner", "role": "main"}, {"agent": "coder-a", "role": "subagent"}]}
        args = mock.Mock(swarm="backend-fix", main="planner", subagent=["coder-a"], json=False)

        with mock.patch("builtins.print") as mock_print:
            assign_swarm.handle(args)

        mock_call_rpc.assert_called_once_with("assign_live_swarm", {"swarm": "backend-fix", "main": "planner", "subagents": ["coder-a"]})
        self.assertIn("Assigned swarm backend-fix", mock_print.call_args.args[0])

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_capture_pane_parser_registration(self):
        parser = ctl.build_parser()
        # Test parsing typical capture-pane subcommand arguments
        parsed = parser.parse_args(["capture-pane", "agent1", "--last", "100", "--format", "markdown", "--include-ansi"])
        self.assertEqual(parsed.subcommand, "capture-pane")
        self.assertEqual(parsed.target, "agent1")
        self.assertEqual(parsed.last, 100)
        self.assertEqual(parsed.format, "markdown")
        self.assertTrue(parsed.include_ansi)
        
        # Test with explicit id and pane options
        parsed2 = parser.parse_args(["capture-pane", "--id", "some-uuid", "--pane", "%5"])
        self.assertIsNone(parsed2.target)
        self.assertEqual(parsed2.id, "some-uuid")
        self.assertEqual(parsed2.pane, "%5")
        self.assertEqual(parsed2.last, 20) # default

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_send_pane_parser_registration(self):
        parser = ctl.build_parser()
        # Test parsing typical send-pane subcommand arguments
        parsed = parser.parse_args(["send-pane", "alice", "--source", "bob", "--last", "100", "--note", "Custom Note", "--format", "json"])
        self.assertEqual(parsed.subcommand, "send-pane")
        self.assertEqual(parsed.target_address, "alice")
        self.assertEqual(parsed.source, "bob")
        self.assertEqual(parsed.last, 100)
        self.assertEqual(parsed.note, "Custom Note")
        self.assertEqual(parsed.format, "json")
        
        # Test default values
        parsed2 = parser.parse_args(["send-pane", "alice"])
        self.assertEqual(parsed2.subcommand, "send-pane")
        self.assertEqual(parsed2.target_address, "alice")
        self.assertIsNone(parsed2.source)
        self.assertEqual(parsed2.last, 20)
        self.assertIsNone(parsed2.note)
        self.assertEqual(parsed2.format, "markdown")

    @mock.patch.dict(os.environ, {"AGENT_TRACKER_CAPTURE_PANE_DEFAULT_LINES": "42"}, clear=True)
    def test_capture_and_send_pane_default_lines_follow_env(self):
        parser = ctl.build_parser()
        self.assertEqual(parser.parse_args(["capture-pane", "agent1"]).last, 42)
        self.assertEqual(parser.parse_args(["send-pane", "alice"]).last, 42)

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_send_text_and_send_key_parser_registration(self):
        parser = ctl.build_parser()

        parsed = parser.parse_args(["send-text", "alice", "hello"])
        self.assertEqual(parsed.subcommand, "send-text")
        self.assertEqual(parsed.target, "alice")
        self.assertEqual(parsed.text, ["hello"])
        self.assertFalse(parsed.no_submit)

        parsed = parser.parse_args(["send-text", "--no-submit", "alice", "draft text"])
        self.assertTrue(parsed.no_submit)
        self.assertEqual(parsed.text, ["draft text"])

        parsed = parser.parse_args(["send-text", "alice", "-starts-with-dash"])
        self.assertEqual(parsed.text, ["-starts-with-dash"])

        parsed = parser.parse_args(["send-key", "alice", "C-c", "Enter"])
        self.assertEqual(parsed.subcommand, "send-key")
        self.assertEqual(parsed.target, "alice")
        self.assertEqual(parsed.keys, ["C-c", "Enter"])

    @mock.patch("ctl_commands.send_text.call_rpc")
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_send_text_calls_send_input_for_bare_name(self, mock_call_rpc):
        from ctl_commands import send_text

        mock_call_rpc.return_value = {"success": True}
        args = mock.Mock(target="alice", text=["hello"], no_submit=False)

        with mock.patch("builtins.print"):
            send_text.handle(args)

        mock_call_rpc.assert_called_once_with("send_input", {
            "agent_name": "alice",
            "input_type": "text",
            "text": "hello",
            "submit": True,
        })

    @mock.patch("ctl_commands.send_text.call_rpc")
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_send_text_no_submit_calls_send_input_for_uuid(self, mock_call_rpc):
        from ctl_commands import send_text

        agent_id = "123e4567-e89b-12d3-a456-426614174000"
        mock_call_rpc.return_value = {"success": True}
        args = mock.Mock(target=agent_id, text=["draft"], no_submit=True)

        with mock.patch("builtins.print"):
            send_text.handle(args)

        mock_call_rpc.assert_called_once_with("send_input", {
            "agent_id": agent_id,
            "input_type": "text",
            "text": "draft",
            "submit": False,
        })

    @mock.patch("ctl_commands.send_key.call_rpc")
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_send_key_calls_send_input_for_remote_target_address(self, mock_call_rpc):
        from ctl_commands import send_key

        mock_call_rpc.return_value = {"success": True}
        args = mock.Mock(target="registry:host-a/alice", keys=["C-c", "Enter"])

        with mock.patch("builtins.print"):
            send_key.handle(args)

        mock_call_rpc.assert_called_once_with("send_input", {
            "target_address": "registry:host-a/alice",
            "input_type": "keys",
            "keys": ["C-c", "Enter"],
        })

    @mock.patch("ctl_commands.send_pane.call_rpc")
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_send_pane_handler_execution(self, mock_call_rpc):
        # 1. Mock call_rpc("capture_pane") and send_message response
        mock_call_rpc.side_effect = [
            {
                "agent_name": "bob",
                "agent_id": "id-bob",
                "tmux_pane": "%1",
                "session": "sess-bob",
                "copy_mode": False,
                "captured_at": "2026-05-23T12:00:00Z",
                "lines_requested": 100,
                "content": "Bob's Screen Content"
            },
            True # call_rpc("send_message") success
        ]
        
        # Import the send_pane command module to mock/test handler
        from ctl_commands import send_pane
        
        # Build mocked args namespace
        class MockArgs:
            target_address = "alice"
            source = "bob"
            id = "id-bob"
            pane = "%1"
            last = 100
            note = "Check this out"
            format = "markdown"
            
        # Run handler in a clean environment
        with mock.patch.dict(os.environ, {}, clear=True):
            send_pane.handle(MockArgs())
        
        # Verify first call: call_rpc("capture_pane")
        mock_call_rpc.assert_any_call("capture_pane", {
            "last_lines": 100,
            "include_ansi": False,
            "agent_name": "bob",
            "agent_id": "id-bob",
            "tmux_pane": "%1"
        })
        
        # Verify second call: call_rpc("send_message")
        expected_msg = (
            "### Pane Capture Snapshot from bob (id-bob)\n"
            "- **Pane:** %1\n"
            "- **Session:** sess-bob\n"
            "- **Copy Mode:** Inactive\n"
            "- **Captured At:** 2026-05-23T12:00:00Z\n"
            "- **User Note:** Check this out\n"
            "\n```\n"
            "Bob's Screen Content\n"
            "```\n"
        )
        mock_call_rpc.assert_any_call("send_message", {
            "agent_name": "alice",
            "message": expected_msg,
            "sender_id": "id-bob",
            "sender_name": "bob",
        })


if __name__ == "__main__":
    unittest.main()
