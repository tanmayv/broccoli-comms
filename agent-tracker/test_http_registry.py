import contextlib
import importlib.util
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import unittest.mock as mock
from http.server import ThreadingHTTPServer

import http_sidecar
import message_journal
import registry_client
import rpc_handler
import state

_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent-registry", "server.py")
_spec = importlib.util.spec_from_file_location("agent_registry_server", _REGISTRY_PATH)
registry_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(registry_server)


def start(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def get(url, token=None):
    req = urllib.request.Request(url, headers=({"Authorization": f"Bearer {token}"} if token else {}))
    with urllib.request.urlopen(req, timeout=3) as resp:
        return resp.status, json.loads(resp.read().decode())


def post(url, body, token=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        return resp.status, json.loads(resp.read().decode())


class TestHttpAndRegistry(unittest.TestCase):
    def setUp(self):
        state.state = {}
        state.name_index = {}
        state.pane_index = {}
        state.INBOX_DIR = "/tmp/test-agent-http-inboxes"
        self.env_patch = mock.patch.dict(os.environ, {"AGENT_REGISTRIES_JSON": "[]"}, clear=False)
        self.env_patch.start()
        self.addCleanup(mock.patch.stopall)

    def test_sidecar_requires_auth_and_returns_snapshot(self):
        state.set_agent("agent1", {"agent_id": "id-1", "status": "idle", "tmux_pane": "%1"})
        server, base = start(http_sidecar.make_handler(token="secret", auth_required=True))
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            get(f"{base}/agents")
        self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(get(f"{base}/healthz"), (200, {"ok": True}))
        code, body = get(f"{base}/agents", token="secret")
        self.assertEqual(code, 200)
        self.assertEqual(body["agents"][0]["agent_id"], "id-1")
        self.assertNotIn("tmux_pane", body["agents"][0])

    def test_sidecar_deliver_requires_auth_and_writes_inbox(self):
        inbox_path = os.path.join(state.INBOX_DIR, "id-1.inbox")
        if os.path.exists(inbox_path):
            os.remove(inbox_path)
        state.set_agent("agent1", {"agent_id": "id-1", "status": "idle", "tmux_pane": "%1", "tmux_socket": "sock"})
        server, base = start(http_sidecar.make_handler(token="secret", auth_required=True))
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(f"{base}/deliver", {"target_agent_id": "id-1", "message": "hello"})
        self.assertEqual(ctx.exception.code, 401)
        with mock.patch("tmux_util.send_keys") as send_keys:
            self.assertEqual(post(f"{base}/deliver", {"target_agent_id": "id-1", "sender_name": "alice", "sender_tracker": "host2", "message": "hello"}, token="secret")[0], 200)
            send_keys.assert_called_once()
        with open(inbox_path, "r") as f:
            self.assertIn("alice (via host2)", f.read())

    def test_registry_register_heartbeat_update_and_gone_sweep(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            payload = {"tracker_id": "t1", "hostname": "host1", "address": "127.0.0.1", "http_port": 19876, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi", "model_type": "pi", "cwd": "/work/project", "swarms": [{"name": "backend-fix", "role": "main"}], "current_task": "Review card UX", "current_task_next_step": "run tests"}]}
            self.assertEqual(post(f"{base}/trackers", payload, token="secret")[0], 201)
            self.assertEqual(post(f"{base}/trackers/t1/agent-update", {"agent_id": "a1", "status": "working"}, token="secret")[0], 200)
            code, body = get(f"{base}/agents/a1", token="secret")
            self.assertEqual((code, body["status"], body["hostname"], body["cwd"]), (200, "working", "host1", "/work/project"))
            agents = get(f"{base}/agents", token="secret")[1]["agents"]
            self.assertIn("address", body)
            self.assertNotIn("address", agents[0])
            self.assertNotIn("http_port", agents[0])
            self.assertEqual(agents[0]["cwd"], "/work/project")
            self.assertEqual(agents[0]["model_type"], "pi")
            self.assertEqual(agents[0]["swarms"], [{"name": "backend-fix", "role": "main"}])
            self.assertEqual(agents[0]["current_task"], "Review card UX")
            self.assertEqual(agents[0]["current_task_next_step"], "run tests")
            heartbeat_agents = [{**payload["agents"][0], "swarms": [{"name": "backend-fix", "role": "subagent"}]}]
            self.assertEqual(post(f"{base}/trackers/t1/heartbeat", {"agents": heartbeat_agents}, token="secret")[0], 200)
            self.assertEqual(get(f"{base}/agents", token="secret")[1]["agents"][0]["swarms"], [{"name": "backend-fix", "role": "subagent"}])
            old_stale, old_gone = registry_server.STALE, registry_server.GONE
            registry_server.STALE, registry_server.GONE = 1, 2
            self.addCleanup(setattr, registry_server, "STALE", old_stale)
            self.addCleanup(setattr, registry_server, "GONE", old_gone)
            store.trackers["t1"]["last_heartbeat"] = time.time() - 5
            self.assertEqual(get(f"{base}/agents", token="secret")[1]["agents"], [])

    def test_registry_filters_shared_service_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            payload = {"tracker_id": "t1", "hostname": "host1", "address": "127.0.0.1", "http_port": 19876, "agents": [
                {"agent_id": "ui-1", "name": "agent-communicator", "aliases": [], "status": "idle", "agent_type": "agent-communicator-ui", "agent_cmd": "agent-communicator", "logical_identity": "agent-communicator", "service_kind": "shared_service", "capabilities": {"mailbox": True, "direct_input": False}},
                {"agent_id": "a1", "name": "coder", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"},
            ]}
            self.assertEqual(post(f"{base}/trackers", payload, token="secret")[0], 201)
            code, body = get(f"{base}/agents?logical_identity=agent-communicator&service_kind=shared_service", token="secret")
            self.assertEqual(code, 200)
            self.assertEqual([agent["name"] for agent in body["agents"]], ["agent-communicator"])
            self.assertEqual(body["agents"][0]["capabilities"], {"mailbox": True, "direct_input": False})

    def test_registry_current_task_fields_are_bounded(self):
        long_task = "x" * 5000
        long_next_step = "y" * 5000
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            payload = {
                "tracker_id": "t2",
                "hostname": "host2",
                "address": "127.0.0.1",
                "http_port": 19877,
                "agents": [
                    {
                        "agent_id": "remote-a",
                        "name": "agent1",
                        "aliases": [],
                        "status": "idle",
                        "agent_type": "pi",
                        "agent_cmd": "pi",
                        "model_type": "pi",
                        "current_task": long_task,
                        "current_task_id": long_task,
                        "current_task_status": "working",
                        "current_task_next_step": long_next_step,
                    }
                ],
            }
            self.assertEqual(post(f"{base}/trackers", payload, token="secret")[0], 201)
            agents = get(f"{base}/agents", token="secret")[1]["agents"]
            self.assertEqual(len(agents), 1)
            self.assertEqual(len(agents[0]["current_task"]), 200)
            self.assertEqual(len(agents[0]["current_task_id"]), 200)
            self.assertEqual(len(agents[0]["current_task_status"]), 7)
            self.assertEqual(len(agents[0]["current_task_next_step"]), 1000)
            self.assertEqual(agents[0]["current_task"], "x" * 200)
            self.assertEqual(agents[0]["current_task_id"], "x" * 200)
            self.assertEqual(agents[0]["current_task_status"], "working")
            self.assertEqual(agents[0]["current_task_next_step"], "y" * 1000)

    def test_registry_long_poll_client_disconnect_has_no_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            target = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": []}
            store.put_tracker(target)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                server, base = start(registry_server.make_handler(store=store, auth_required=False))
                self.addCleanup(server.shutdown)
                self.addCleanup(server.server_close)
                host, port = "127.0.0.1", server.server_port
                client = socket.create_connection((host, port), timeout=1)
                client.sendall(b"GET /trackers/t2/deliveries?wait=1 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                client.close()
                store.enqueue_delivery("t2", {"target_agent_id": "a2", "message": "hello"})
                time.sleep(0.2)
            self.assertNotIn("BrokenPipeError", stderr.getvalue())
            self.assertNotIn("Exception occurred during processing", stderr.getvalue())

    def test_registry_pane_inputs_default_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            source = {"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            store.put_tracker(source)
            store.put_tracker(target)
            server, base = start(registry_server.make_handler(store=store, token="secret", remote_pane_input_enabled=False))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/pane-inputs", {"sender_tracker_id": "t1", "target_agent_id": "a2", "pane_input_id": "pi-1", "request_id": "req-1", "input_type": "text", "text": "hello"}, token="secret")
            self.assertEqual(ctx.exception.code, 403)
            self.assertEqual(store.wait_for_deliveries("t2", 0), [])

    def test_registry_pane_inputs_validate_queue_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "registry-state.json")
            store = registry_server.Store(state_path=state_path)
            source = {"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": ["alias2"], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            store.put_tracker(source)
            store.put_tracker(target)
            server, base = start(registry_server.make_handler(store=store, token="secret", remote_pane_input_enabled=True))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            payload = {
                "sender_tracker_id": "t1",
                "sender_agent_id": "a1",
                "sender_agent_name": "agent1",
                "target_hostname": "host2",
                "target_agent_name": "alias2",
                "pane_input_id": "pi-1",
                "request_id": "req-1",
                "input_type": "keys",
                "keys": ["ctrl-c", "Enter"],
            }
            code, body = post(f"{base}/pane-inputs", payload, token="secret")
            self.assertEqual(code, 202)
            self.assertEqual(body["pane_input_id"], "pi-1")
            reloaded = registry_server.Store(state_path=state_path)
            delivery = reloaded.wait_for_deliveries("t2", 0)[0]
            self.assertEqual(delivery["delivery_type"], "pane_input")
            self.assertEqual(delivery["message_id"], "pi-1")
            self.assertEqual(delivery["request_id"], "req-1")
            self.assertEqual(delivery["target_agent_id"], "a2")
            self.assertEqual(delivery["keys"], ["C-c", "Enter"])
            self.assertNotIn("message", delivery)

    def test_registry_pane_inputs_reject_invalid_same_tracker_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            source = {"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            store.put_tracker(source)
            store.put_tracker(target)
            server, base = start(registry_server.make_handler(store=store, token="secret", remote_pane_input_enabled=True))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/pane-inputs", {"sender_tracker_id": "t1", "target_agent_name": "agent2", "pane_input_id": "pi-1", "request_id": "req-1", "input_type": "text", "text": "hello"}, token="secret")
            self.assertEqual(ctx.exception.code, 400)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/pane-inputs", {"sender_tracker_id": "t1", "target_agent_id": "a2", "pane_input_id": ["bad"], "request_id": "req-list", "input_type": "text", "text": "hello"}, token="secret")
            self.assertEqual(ctx.exception.code, 400)
            self.assertEqual(store.wait_for_deliveries("t2", 0), [])
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/pane-inputs", {"sender_tracker_id": "t1", "target_agent_id": "a2", "pane_input_id": "   ", "request_id": "req-blank", "input_type": "text", "text": "hello"}, token="secret")
            self.assertEqual(ctx.exception.code, 400)
            self.assertEqual(store.wait_for_deliveries("t2", 0), [])
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/pane-inputs", {"sender_tracker_id": "missing", "target_agent_id": "a2", "pane_input_id": "pi-missing", "request_id": "req-missing", "input_type": "text", "text": "hello"}, token="secret")
            self.assertEqual(ctx.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/pane-inputs", {"sender_tracker_id": "t2", "target_agent_id": "a2", "pane_input_id": "pi-2", "request_id": "req-2", "input_type": "text", "text": "hello"}, token="secret")
            self.assertEqual(ctx.exception.code, 400)
            store.trackers["t2"]["last_heartbeat"] = time.time() - 100
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/pane-inputs", {"sender_tracker_id": "t1", "target_agent_id": "a2", "pane_input_id": "pi-3", "request_id": "req-3", "input_type": "text", "text": "hello"}, token="secret")
            self.assertEqual(ctx.exception.code, 503)
            store.trackers["t2"]["last_heartbeat"] = time.time()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/pane-inputs", {"sender_tracker_id": "t1", "target_agent_id": "a2", "pane_input_id": "pi-4", "request_id": "req-4", "input_type": "keys", "keys": ["bad;key"]}, token="secret")
            self.assertEqual(ctx.exception.code, 400)

    def test_registry_messages_queue_ack_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "registry-state.json")
            store = registry_server.Store(state_path=state_path)
            source = {"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            store.put_tracker(source)
            store.put_tracker(target)
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            code, body = post(f"{base}/messages", {"sender_tracker_id": "t1", "sender_agent_id": "a1", "sender_agent_name": "agent1", "sender_hostname": "host1", "sender_model_type": "pi", "sender_agent_type": "pi", "sender_agent_cmd": "pi", "kind": "task_update", "content_type": "application/vnd.broccoli.task-update+json", "task_id": "task-1", "task_title": "Remote visible", "task_status": "review", "delivery_scope": "shared_service_broadcast", "delivery_id": "del-task-1-a2", "target_logical_identity": "agent-communicator", "target_agent_id": "a2", "message": "hello"}, token="secret")
            self.assertEqual(code, 202)
            message_id = body["message_id"]
            reloaded = registry_server.Store(state_path=state_path)
            self.assertEqual(reloaded.wait_for_deliveries("t2", 0)[0]["message_id"], message_id)
            code, deliveries = get(f"{base}/trackers/t2/deliveries?wait=0", token="secret")
            self.assertEqual(code, 200)
            self.assertEqual(deliveries["deliveries"][0]["message_id"], message_id)
            self.assertEqual(deliveries["deliveries"][0]["message"], "hello")
            self.assertEqual(deliveries["deliveries"][0]["sender_hostname"], "host1")
            self.assertEqual(deliveries["deliveries"][0]["sender_model_type"], "pi")
            self.assertEqual(deliveries["deliveries"][0]["sender_agent_type"], "pi")
            self.assertEqual(deliveries["deliveries"][0]["sender_agent_cmd"], "pi")
            self.assertEqual(deliveries["deliveries"][0]["kind"], "task_update")
            self.assertEqual(deliveries["deliveries"][0]["content_type"], "application/vnd.broccoli.task-update+json")
            self.assertEqual(deliveries["deliveries"][0]["task_id"], "task-1")
            self.assertEqual(deliveries["deliveries"][0]["task_title"], "Remote visible")
            self.assertEqual(deliveries["deliveries"][0]["task_status"], "review")
            self.assertEqual(deliveries["deliveries"][0]["delivery_scope"], "shared_service_broadcast")
            self.assertEqual(deliveries["deliveries"][0]["delivery_id"], "del-task-1-a2")
            self.assertEqual(deliveries["deliveries"][0]["target_logical_identity"], "agent-communicator")
            self.assertEqual(body["delivery_id"], "del-task-1-a2")
            self.assertEqual(post(f"{base}/trackers/t2/deliveries/del-task-1-a2/ack", {}, token="secret")[0], 200)
            self.assertEqual(get(f"{base}/trackers/t2/deliveries?wait=0", token="secret")[1]["deliveries"], [])

    def test_registry_message_events_post_query_dedupe_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "registry-state.json")
            store = registry_server.Store(state_path=state_path)
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            event = {
                "message_id": "event-1",
                "timestamp": "2026-06-06T10:00:00Z",
                "sender_tracker_id": "t1",
                "sender_hostname": "host1",
                "sender_agent_id": "a1",
                "sender_agent_name": "planner",
                "recipient_tracker_id": "t2",
                "recipient_hostname": "host2",
                "recipient_agent_id": "a2",
                "recipient_agent_name": "coder",
                "swarms": [{"name": "backend-fix"}],
                "message": "durable registry event",
                "attachments": [],
            }
            other_event = {**event, "message_id": "event-2", "swarms": [{"name": "frontend-fix"}], "message": "other swarm"}

            code, body = post(f"{base}/message-events", event, token="secret")
            self.assertEqual(code, 201)
            self.assertTrue(body["inserted"])
            duplicate_code, duplicate_body = post(f"{base}/message-events", {**event, "message": "changed duplicate"}, token="secret")
            self.assertEqual(duplicate_code, 200)
            self.assertFalse(duplicate_body["inserted"])
            self.assertEqual(post(f"{base}/message-events", other_event, token="secret")[0], 201)

            code, body = get(f"{base}/message-events?swarm=backend-fix&limit=10", token="secret")
            self.assertEqual(code, 200)
            self.assertEqual(len(body["events"]), 1)
            self.assertEqual(body["events"][0]["message_id"], "event-1")
            self.assertEqual(body["events"][0]["message"], "durable registry event")

            reloaded = registry_server.Store(state_path=state_path)
            self.assertEqual(reloaded.query_message_events("backend-fix", 10)[0]["message_id"], "event-1")
            self.assertEqual(reloaded.query_message_events("frontend-fix", 10)[0]["message_id"], "event-2")

    def test_registry_pane_output_events_validate_dedupe_query_and_fanout(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "registry-state.json")
            store = registry_server.Store(state_path=state_path)
            source = {"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            watcher = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            store.put_tracker(source)
            store.put_tracker(watcher)
            store.put_watch_lease("t2", "client-1", ["host1/agent1"], 60)
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            local_event = {
                "seq": 1,
                "agent_id": "a1",
                "agent_name": "agent1",
                "event_type": "progress",
                "confidence": 0.8,
                "payload": {"summary": "safe"},
            }
            event = registry_server.pane_output_registry.from_local_event(local_event, source_tracker_id="t1", source_hostname="host1", ttl_seconds=60, now=time.time())

            code, body = post(f"{base}/pane-output-events", event, token="secret")
            self.assertEqual(code, 201)
            self.assertTrue(body["inserted"])
            duplicate_code, duplicate_body = post(f"{base}/pane-output-events", event, token="secret")
            self.assertEqual(duplicate_code, 200)
            self.assertFalse(duplicate_body["inserted"])

            query_code, query_body = get(f"{base}/pane-output-events?limit=10", token="secret")
            self.assertEqual(query_code, 200)
            self.assertEqual(len(query_body["events"]), 1)
            self.assertEqual(query_body["events"][0]["event_id"], event["event_id"])
            encoded = json.dumps(query_body).lower()
            self.assertNotIn("raw", encoded)
            self.assertNotIn("token", encoded)
            self.assertNotIn("tmux", encoded)

            fanout_code, fanout_body = get(f"{base}/trackers/t2/events?wait=0", token="secret")
            self.assertEqual(fanout_code, 200)
            self.assertEqual(fanout_body["events"][0]["event_type"], "pane_output_event")
            self.assertEqual(fanout_body["events"][0]["payload"]["event_id"], event["event_id"])

    def test_registry_pane_output_events_clamp_far_future_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            store.put_tracker({"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]})
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            created_at = time.time()
            event = registry_server.pane_output_registry.from_local_event({
                "seq": 1,
                "agent_id": "a1",
                "agent_name": "agent1",
                "event_type": "progress",
                "payload": {"summary": "safe"},
            }, source_tracker_id="t1", source_hostname="host1", ttl_seconds=60, now=created_at)
            event["expires_at"] = created_at + 10_000_000

            self.assertEqual(post(f"{base}/pane-output-events", event, token="secret")[0], 201)
            stored = get(f"{base}/pane-output-events?limit=10", token="secret")[1]["events"][0]

            self.assertEqual(stored["ttl_seconds"], float(registry_server.pane_output_registry.MAX_TTL_SECONDS))
            self.assertEqual(stored["expires_at"], stored["created_at"] + registry_server.pane_output_registry.MAX_TTL_SECONDS)
            encoded = json.dumps(stored).lower()
            self.assertNotIn("raw", encoded)
            self.assertNotIn("token", encoded)
            self.assertNotIn("tmux", encoded)

    def test_registry_pane_output_events_reject_spoof_and_raw_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            store.put_tracker({"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]})
            store.put_tracker({"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": []})
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            good = registry_server.pane_output_registry.from_local_event({
                "seq": 1,
                "agent_id": "a1",
                "agent_name": "agent1",
                "event_type": "progress",
                "payload": {"summary": "safe"},
            }, source_tracker_id="t1", source_hostname="host1", now=time.time())

            with self.assertRaises(urllib.error.HTTPError) as spoof_ctx:
                post(f"{base}/pane-output-events", {**good, "source_tracker_id": "t2"}, token="secret")
            self.assertEqual(spoof_ctx.exception.code, 403)

            bad = {**good, "payload": {"raw_output": "RAW_SECRET_VALUE"}}
            with self.assertRaises(urllib.error.HTTPError) as raw_ctx:
                post(f"{base}/pane-output-events", bad, token="secret")
            self.assertEqual(raw_ctx.exception.code, 400)
            body = json.loads(raw_ctx.exception.read().decode())
            self.assertNotIn("RAW_SECRET_VALUE", json.dumps(body))
            self.assertEqual(get(f"{base}/pane-output-events?limit=10", token="secret")[1]["events"], [])

    def test_registry_message_events_require_auth_and_valid_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            server, base = start(registry_server.make_handler(store=store, token="secret", auth_required=True))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/message-events", {"message_id": "event-1", "swarms": []})
            self.assertEqual(ctx.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/message-events", {"swarms": []}, token="secret")
            self.assertEqual(ctx.exception.code, 400)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                get(f"{base}/message-events", token="secret")
            self.assertEqual(ctx.exception.code, 400)

    def test_registry_messages_preserve_swarm_metadata_in_queued_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            source = {"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            store.put_tracker(source)
            store.put_tracker(target)
            server, base = start(registry_server.make_handler(store=store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            code, body = post(f"{base}/messages", {
                "sender_tracker_id": "t1",
                "sender_agent_id": "a1",
                "sender_agent_name": "agent1",
                "target_agent_id": "a2",
                "message": "hello swarm remote",
                "message_id": "remote-meta-1",
                "swarms": [{"name": "backend-fix"}],
                "membership_snapshot": {"backend-fix": {"sender_role": "main", "recipient_role": "subagent"}},
                "swarm_context": "backend-fix",
            }, token="secret")
            self.assertEqual(code, 202)
            delivery = store.wait_for_deliveries("t2", 0)[0]
            self.assertEqual(delivery["message_id"], body["message_id"])
            self.assertEqual(delivery["swarms"], [{"name": "backend-fix"}])
            self.assertEqual(delivery["membership_snapshot"]["backend-fix"]["recipient_role"], "subagent")
            self.assertEqual(delivery["swarm_context"], "backend-fix")

    def test_registry_tracker_events_queue_ack_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "registry-state.json")
            store = registry_server.Store(state_path=state_path)
            source = {"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": []}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": []}
            store.put_tracker(source)
            store.put_tracker(target)
            server, base = start(registry_server.make_handler(store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)

            code, body = post(f"{base}/tracker-events", {"event_type": "message_read", "source_tracker_id": "t1", "target_tracker_id": "t2", "payload": {"message_id": "m1"}}, token="secret")
            self.assertEqual(code, 202)
            event_id = body["event_id"]
            reloaded = registry_server.Store(state_path=state_path)
            self.assertEqual(reloaded.wait_for_tracker_events("t2", 0)[0]["event_id"], event_id)
            code, events = get(f"{base}/trackers/t2/events?wait=0", token="secret")
            self.assertEqual(code, 200)
            self.assertEqual(events["events"][0]["payload"]["message_id"], "m1")
            self.assertEqual(post(f"{base}/trackers/t2/events/{event_id}/ack", {}, token="secret")[0], 200)
            self.assertEqual(get(f"{base}/trackers/t2/events?wait=0", token="secret")[1]["events"], [])

    def test_registry_messages_auth_same_tracker_offline_and_attachment_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            source = {"tracker_id": "t1", "hostname": "host1", "address": "host1", "http_port": 19875, "agents": [{"agent_id": "a1", "name": "agent1", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "host2", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]}
            store.put_tracker(source)
            store.put_tracker(target)
            server, base = start(registry_server.make_handler(store=store, token="secret", auth_required=True))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/messages", {"sender_tracker_id": "t1", "target_agent_id": "a2", "message": "hi"})
            self.assertEqual(ctx.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/messages", {"sender_tracker_id": "t2", "target_agent_id": "a2", "message": "hi"}, token="secret")
            self.assertEqual(ctx.exception.code, 400)
            store.trackers["t2"]["last_heartbeat"] = time.time() - 100
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/messages", {"sender_tracker_id": "t1", "target_agent_id": "a2", "message": "hi"}, token="secret")
            self.assertEqual(ctx.exception.code, 503)
            store.trackers["t2"]["last_heartbeat"] = time.time()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(f"{base}/messages", {"sender_tracker_id": "t1", "target_agent_id": "a2", "message": "hi", "attachments": [{"name": "bad.txt", "content_b64": "%%%"}]}, token="secret")
            self.assertEqual(ctx.exception.code, 400)

    def test_registry_client_delivery_loop_delivers_and_acks(self):
        delivery = {
            "message_id": "m1",
            "target_agent_id": "a2",
            "sender_name": "agent1",
            "sender_tracker": "host1",
            "sender_hostname": "host1",
            "sender_model_type": "pi",
            "sender_agent_type": "pi",
            "sender_agent_cmd": "pi",
            "kind": "text",
            "message": "hello",
            "sent_at": "2026-05-17T00:00:00+00:00",
        }
        with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery") as ack, \
             mock.patch("rpc_handler.deliver_local_message") as deliver:
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        deliver.assert_called_once()
        delivered_payload = deliver.call_args.args[1]
        self.assertEqual(delivered_payload["sender_hostname"], "host1")
        self.assertEqual(delivered_payload["sender_model_type"], "pi")
        self.assertEqual(delivered_payload["sender_agent_type"], "pi")
        self.assertEqual(delivered_payload["sender_agent_cmd"], "pi")
        self.assertEqual(delivered_payload["kind"], "text")
        ack.assert_called_once_with("m1")

    def test_inbound_remote_delivery_with_swarm_metadata_is_journaled_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_journal = message_journal.JOURNAL_PATH
            old_inbox = state.INBOX_DIR
            message_journal.JOURNAL_PATH = os.path.join(tmp, "message_journal.jsonl")
            state.INBOX_DIR = os.path.join(tmp, "inboxes")
            self.addCleanup(setattr, message_journal, "JOURNAL_PATH", old_journal)
            self.addCleanup(setattr, state, "INBOX_DIR", old_inbox)
            state.set_agent("agent2", {
                "agent_id": "a2",
                "status": "idle",
                "tmux_pane": "%2",
                "tmux_socket": "sock",
                "no_notify_with_send_keys": True,
                "swarms": [{"name": "backend-fix", "role": "subagent"}],
            })
            delivery = {
                "message_id": "remote-swarm-1",
                "target_agent_id": "a2",
                "sender_name": "agent1",
                "sender_agent_id": "a1",
                "sender_tracker_id": "t1",
                "sender_tracker": "host1",
                "sender_hostname": "host1",
                "message": "remote swarm hello",
                "sent_at": "2026-05-17T00:00:00+00:00",
                "swarms": [{"name": "backend-fix"}],
                "membership_snapshot": {"backend-fix": {"sender_role": "main", "recipient_role": "subagent"}},
            }
            with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), SystemExit]), \
                 mock.patch.object(registry_client, "ack_delivery") as ack, \
                 mock.patch.object(registry_client, "fetch_message_events", return_value=[]):
                with self.assertRaises(SystemExit):
                    registry_client._delivery_loop()

            ack.assert_called_once_with("remote-swarm-1")
            timeline = rpc_handler.handle_get_swarm_timeline({"swarm": "backend-fix", "last_n": 10})
            self.assertEqual(len(timeline["messages"]), 1)
            self.assertEqual(timeline["messages"][0]["message_id"], "remote-swarm-1")
            self.assertEqual(timeline["messages"][0]["message"], "remote swarm hello")
            self.assertEqual(timeline["messages"][0]["sender_hostname"], "host1")
            self.assertEqual(timeline["messages"][0]["sender_tracker_id"], "t1")
            self.assertEqual(timeline["messages"][0]["recipient_hostname"], registry_client.HOSTNAME)
            self.assertEqual(timeline["messages"][0]["direction"], "inbound")
            self.assertEqual(timeline["messages"][0]["journal_source"], "registry_delivery")
            self.assertEqual(timeline["messages"][0]["membership_snapshot"]["backend-fix"]["sender_role"], "main")

    def test_duplicate_inbound_remote_delivery_does_not_duplicate_swarm_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_journal = message_journal.JOURNAL_PATH
            old_inbox = state.INBOX_DIR
            message_journal.JOURNAL_PATH = os.path.join(tmp, "message_journal.jsonl")
            state.INBOX_DIR = os.path.join(tmp, "inboxes")
            self.addCleanup(setattr, message_journal, "JOURNAL_PATH", old_journal)
            self.addCleanup(setattr, state, "INBOX_DIR", old_inbox)
            state.set_agent("agent2", {
                "agent_id": "a2",
                "status": "idle",
                "tmux_pane": "%2",
                "tmux_socket": "sock",
                "no_notify_with_send_keys": True,
                "swarms": [{"name": "backend-fix", "role": "subagent"}],
            })
            delivery = {
                "message_id": "remote-swarm-dup",
                "target_agent_id": "a2",
                "sender_name": "agent1",
                "sender_tracker": "host1",
                "message": "remote swarm duplicate",
                "sent_at": "2026-05-17T00:00:00+00:00",
                "swarms": [{"name": "backend-fix"}],
            }
            with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery, delivery]}), SystemExit]), \
                 mock.patch.object(registry_client, "ack_delivery"), \
                 mock.patch.object(registry_client, "fetch_message_events", return_value=[]):
                with self.assertRaises(SystemExit):
                    registry_client._delivery_loop()

            timeline = rpc_handler.handle_get_swarm_timeline({"swarm": "backend-fix", "last_n": 10})
            self.assertEqual([msg["message_id"] for msg in timeline["messages"]], ["remote-swarm-dup"])

    def test_non_swarm_inbound_remote_delivery_is_excluded_from_swarm_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_journal = message_journal.JOURNAL_PATH
            old_inbox = state.INBOX_DIR
            message_journal.JOURNAL_PATH = os.path.join(tmp, "message_journal.jsonl")
            state.INBOX_DIR = os.path.join(tmp, "inboxes")
            self.addCleanup(setattr, message_journal, "JOURNAL_PATH", old_journal)
            self.addCleanup(setattr, state, "INBOX_DIR", old_inbox)
            state.set_agent("agent2", {
                "agent_id": "a2",
                "status": "idle",
                "tmux_pane": "%2",
                "tmux_socket": "sock",
                "no_notify_with_send_keys": True,
                "swarms": [{"name": "backend-fix", "role": "subagent"}],
            })
            delivery = {
                "message_id": "remote-non-swarm-1",
                "target_agent_id": "a2",
                "sender_name": "agent1",
                "sender_tracker": "host1",
                "message": "remote simple hello",
                "sent_at": "2026-05-17T00:00:00+00:00",
            }
            with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), SystemExit]), \
                 mock.patch.object(registry_client, "ack_delivery"), \
                 mock.patch.object(registry_client, "fetch_message_events", return_value=[]):
                with self.assertRaises(SystemExit):
                    registry_client._delivery_loop()

            timeline = rpc_handler.handle_get_swarm_timeline({"swarm": "backend-fix", "last_n": 10})
            self.assertEqual(timeline["messages"], [])

    def test_registry_client_delivery_loop_retries_missing_target_until_available(self):
        delivery = {
            "message_id": "m1",
            "target_agent_id": "a2",
            "sender_name": "agent1",
            "sender_tracker": "host1",
            "message": "hello",
            "sent_at": "2026-05-17T00:00:00+00:00",
        }
        with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), (200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery") as ack, \
             mock.patch.object(registry_client, "DELIVERY_TARGET_GRACE_SECONDS", 60), \
             mock.patch.object(registry_client.time, "sleep") as sleep, \
             mock.patch.object(registry_client.time, "time", return_value=100.0), \
             mock.patch("rpc_handler.deliver_local_message", side_effect=[rpc_handler.DeliveryTargetNotFound("not recovered yet"), "agent2"]) as deliver:
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        self.assertEqual(deliver.call_count, 2)
        ack.assert_called_once_with("m1")
        sleep.assert_called_once_with(2)

    def test_registry_client_delivery_loop_acks_missing_target_after_grace(self):
        delivery = {
            "message_id": "m1",
            "target_agent_id": "a2",
            "sender_name": "agent1",
            "sender_tracker": "host1",
            "message": "hello",
            "sent_at": "2026-05-17T00:00:00+00:00",
        }
        with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), (200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery") as ack, \
             mock.patch.object(registry_client, "DELIVERY_TARGET_GRACE_SECONDS", 60), \
             mock.patch.object(registry_client.time, "sleep") as sleep, \
             mock.patch.object(registry_client.time, "time", side_effect=[100.0, 200.0]), \
             mock.patch("rpc_handler.deliver_local_message", side_effect=rpc_handler.DeliveryTargetNotFound("gone")):
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        ack.assert_called_once_with("m1")
        sleep.assert_called_once_with(2)

    def test_registry_client_delivery_loop_acks_invalid_delivery_immediately(self):
        delivery = {
            "message_id": "m1",
            "target_agent_id": "a2",
            "sender_name": "agent1",
            "sender_tracker": "host1",
            "message": "hello",
            "sent_at": "2026-05-17T00:00:00+00:00",
        }
        with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery") as ack, \
             mock.patch.object(registry_client.time, "sleep") as sleep, \
             mock.patch("rpc_handler.deliver_local_message", side_effect=rpc_handler.DeliveryValidationError("bad attachment")):
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        ack.assert_called_once_with("m1")
        sleep.assert_not_called()

    def test_registry_client_delivery_loop_does_not_ack_transient_failures(self):
        delivery = {
            "message_id": "m1",
            "target_agent_id": "a2",
            "sender_name": "agent1",
            "sender_tracker": "host1",
            "message": "hello",
            "sent_at": "2026-05-17T00:00:00+00:00",
        }
        with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery") as ack, \
             mock.patch.object(registry_client.time, "sleep"), \
             mock.patch("rpc_handler.deliver_local_message", side_effect=RuntimeError("disk full")):
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        ack.assert_not_called()

    def test_registry_client_delivery_loop_dispatches_pane_input_and_acks_after_injection(self):
        delivery = {
            "delivery_type": "pane_input",
            "message_id": "pi-1",
            "pane_input_id": "pi-1",
            "request_id": "req-1",
            "target_agent_id": "a2",
            "input_type": "text",
            "text": "hello",
            "submit": False,
        }
        with mock.patch.dict(os.environ, {"AGENT_TRACKER_REMOTE_PANE_INPUT_RECEIVE_ENABLED": "1"}, clear=True), \
             mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery") as ack, \
             mock.patch.object(state, "pane_input_was_applied", return_value=False) as was_applied, \
             mock.patch.object(state, "mark_pane_input_applied") as mark_applied, \
             mock.patch("rpc_handler.handle_send_input", return_value={"success": True}):
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        was_applied.assert_called_once_with("req-1")
        mark_applied.assert_called_once_with("req-1", "pi-1", "a2")
        ack.assert_called_once_with("pi-1")

    def test_registry_client_delivery_loop_acks_duplicate_pane_input_without_injecting(self):
        delivery = {
            "delivery_type": "pane_input",
            "message_id": "pi-1",
            "pane_input_id": "pi-1",
            "request_id": "req-1",
            "target_agent_id": "a2",
            "input_type": "keys",
            "keys": ["Enter"],
        }
        with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery") as ack, \
             mock.patch.object(state, "pane_input_was_applied", return_value=True), \
             mock.patch("rpc_handler.handle_send_input") as send_input:
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        send_input.assert_not_called()
        ack.assert_called_once_with("pi-1")

    def test_registry_client_delivery_loop_does_not_ack_pane_input_transient_failure(self):
        delivery = {
            "delivery_type": "pane_input",
            "message_id": "pi-1",
            "pane_input_id": "pi-1",
            "request_id": "req-1",
            "target_agent_id": "a2",
            "input_type": "text",
            "text": "hello",
        }
        with mock.patch.dict(os.environ, {"AGENT_TRACKER_REMOTE_PANE_INPUT_RECEIVE_ENABLED": "1"}, clear=True), \
             mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery") as ack, \
             mock.patch.object(registry_client.time, "sleep"), \
             mock.patch.object(state, "pane_input_was_applied", return_value=False), \
             mock.patch("rpc_handler.handle_send_input", side_effect=RuntimeError("tmux down")):
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        ack.assert_not_called()

    def test_handle_pane_input_delivery_injects_without_inbox_or_notification(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dedupe = state.PANE_INPUT_DEDUPE_PATH
            state.PANE_INPUT_DEDUPE_PATH = os.path.join(tmp, "dedupe.json")
            self.addCleanup(setattr, state, "PANE_INPUT_DEDUPE_PATH", old_dedupe)
            state.set_agent("agent2", {"agent_id": "a2", "status": "idle", "tmux_pane": "%2", "tmux_socket": "sock"})
            inbox_path = os.path.join(state.INBOX_DIR, "a2.inbox")
            if os.path.exists(inbox_path):
                os.remove(inbox_path)
            delivery = {
                "delivery_type": "pane_input",
                "message_id": "pi-1",
                "pane_input_id": "pi-1",
                "request_id": "req-1",
                "target_agent_id": "a2",
                "input_type": "text",
                "text": "hello",
                "submit": True,
            }
            with mock.patch.dict(os.environ, {"AGENT_TRACKER_REMOTE_PANE_INPUT_RECEIVE_ENABLED": "1"}, clear=True), \
                 mock.patch("tmux_util.send_literal_text") as send_literal, \
                 mock.patch("tmux_util.send_keys") as notify_send_keys:
                result = registry_client._handle_pane_input_delivery(delivery)
            self.assertEqual(result, "injected")
            send_literal.assert_called_once_with("%2", "hello", submit=True, socket_path="sock")
            notify_send_keys.assert_not_called()
            self.assertFalse(os.path.exists(inbox_path))
            self.assertTrue(state.pane_input_was_applied("req-1"))

    def test_registry_client_redelivery_after_write_before_ack_is_deduped(self):
        inbox_path = os.path.join(state.INBOX_DIR, "a2.inbox")
        if os.path.exists(inbox_path):
            os.remove(inbox_path)
        state.set_agent("agent2", {"agent_id": "a2", "status": "idle", "tmux_pane": "%2", "tmux_socket": "sock"})
        delivery = {
            "message_id": "m1",
            "target_agent_id": "a2",
            "sender_name": "agent1",
            "sender_tracker": "host1",
            "message": "hello",
            "sent_at": "2026-05-17T00:00:00+00:00",
        }
        with mock.patch.object(registry_client, "fetch_deliveries", side_effect=[(200, {"deliveries": [delivery]}), (200, {"deliveries": [delivery]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_delivery", side_effect=[None, 200]) as ack, \
             mock.patch("tmux_util.send_keys"):
            with self.assertRaises(SystemExit):
                registry_client._delivery_loop()
        self.assertEqual(ack.call_count, 2)
        with open(inbox_path, "r") as f:
            lines = [line for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["message_id"], "m1")

    def test_registry_client_heartbeat_reregisters_on_404(self):
        with mock.patch.object(registry_client, "register") as register, \
             mock.patch.object(registry_client, "heartbeat", side_effect=[404]), \
             mock.patch.object(registry_client.time, "sleep", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                registry_client._heartbeat_loop()
        self.assertEqual(register.call_count, 2)

    def test_registry_remote_save_triggers_tracker_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "registry-state.json")
            store = registry_server.Store(state_path=state_path)
            target = {
                "tracker_id": "t2",
                "hostname": "host2",
                "address": "host2",
                "http_port": 19876,
                "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle", "agent_type": "pi", "agent_cmd": "pi"}]
            }
            store.put_tracker(target)
            server, base = start(registry_server.make_handler(store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)

            code, body = post(
                f"{base}/save-agent",
                {
                    "agent_to_save": "agent2",
                    "agent_name": "agent2-saved-config",
                    "command": "pi custom --args",
                    "description": "custom desc",
                    "cwd": "/custom/cwd"
                },
                token="secret"
            )
            self.assertEqual(code, 202)
            self.assertTrue(body["queued"])
            self.assertEqual(body["target_tracker"], "host2")
            
            code, events = get(f"{base}/trackers/t2/events?wait=0", token="secret")
            self.assertEqual(code, 200)
            self.assertEqual(len(events["events"]), 1)
            event = events["events"][0]
            self.assertEqual(event["event_type"], "save_request")
            self.assertEqual(event["payload"]["agent_to_save"], "a2")
            self.assertEqual(event["payload"]["agent_name"], "agent2-saved-config")
            self.assertEqual(event["payload"]["command"], "pi custom --args")
            self.assertEqual(event["payload"]["description"], "custom desc")
            self.assertEqual(event["payload"]["cwd"], "/custom/cwd")

    def test_registry_client_ingests_pane_output_event_once_as_observer_only(self):
        registry_client._pane_output_event_dedupe = {}
        registry_event = registry_client.pane_output_registry.from_local_event({
            "seq": 1,
            "agent_id": "remote-a1",
            "agent_name": "remote-agent",
            "event_type": "progress",
            "confidence": 0.8,
            "payload": {"summary": "safe"},
            "state_patch": {"current_task": "remote task"},
        }, source_tracker_id="remote-t1", source_hostname="remote-host", now=time.time())
        state.set_agent("local-agent", {"agent_id": "local-1", "status": "idle", "tmux_pane": "%1"})

        first = registry_client._ingest_registry_pane_output_event(registry_event)
        second = registry_client._ingest_registry_pane_output_event(registry_event)

        self.assertTrue(first)
        self.assertFalse(second)
        output_events = [event for event in state.events if event["type"] == "agent_output_event"]
        self.assertEqual(len(output_events), 1)
        self.assertEqual(output_events[0]["source"], "registry-pane-output")
        self.assertEqual(output_events[0]["target_agent_id"], "remote-host/remote-a1")
        self.assertEqual(output_events[0]["target_agent_name"], "remote-host/remote-agent")
        self.assertEqual(state.get_agent("local-1")["status"], "idle")
        encoded = json.dumps(output_events[0]).lower()
        self.assertNotIn("raw", encoded)
        self.assertNotIn("token", encoded)

    def test_registry_client_event_loop_handles_save_request(self):
        event = {
            "event_id": "e1",
            "event_type": "save_request",
            "payload": {
                "agent_to_save": "a2",
                "agent_name": "agent2-saved-config",
                "command": "pi custom --args",
                "description": "custom desc",
                "cwd": "/custom/cwd"
            }
        }
        with mock.patch.object(registry_client, "fetch_events", side_effect=[(200, {"events": [event]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_event") as ack, \
             mock.patch.object(registry_client, "_handle_remote_save") as handle_save, \
             mock.patch.object(registry_client, "register") as register, \
             mock.patch("registry_client.time.sleep") as sleep:
            with self.assertRaises(SystemExit):
                registry_client._event_loop()
        
        handle_save.assert_called_once_with("a2", "agent2-saved-config", "pi custom --args", "custom desc", "/custom/cwd")
        register.assert_called_once()
        ack.assert_called_once_with("e1")

    def test_event_loop_dispatches_remote_run_request(self):
        event = {
            "event_id": "e-run",
            "event_type": "remote_run_request",
            "payload": {"request_id": "rr-1", "agent": "coder", "cwd": "/tmp/repo", "scope": "project:x", "command": "pi"},
        }

        class InlineThread:
            def __init__(self, target, args=(), kwargs=None, daemon=None):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}
            def start(self):
                self.target(*self.args, **self.kwargs)

        with mock.patch.object(registry_client, "fetch_events", side_effect=[(200, {"events": [event]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_event") as ack, \
             mock.patch.object(registry_client, "_handle_remote_run_request") as handle_run, \
             mock.patch("registry_client.threading.Thread", new=InlineThread):
            with self.assertRaises(SystemExit):
                registry_client._event_loop()

        handle_run.assert_called_once_with(event["payload"])
        ack.assert_called_once_with("e-run")

    def test_spin_request_routes_to_remote_run_with_deprecation_log(self):
        event = {"event_id": "e-spin", "event_type": "spin_request", "payload": {"config_name": "legacy-coder"}}

        class InlineThread:
            def __init__(self, target, args=(), kwargs=None, daemon=None):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}
            def start(self):
                self.target(*self.args, **self.kwargs)

        with mock.patch.object(registry_client, "fetch_events", side_effect=[(200, {"events": [event]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_event"), \
             mock.patch.object(registry_client, "_handle_remote_run_request") as handle_run, \
             mock.patch("registry_client.threading.Thread", new=InlineThread), \
             self.assertLogs("agent-tracker.registry", level="WARNING") as logs:
            with self.assertRaises(SystemExit):
                registry_client._event_loop()

        handle_run.assert_called_once()
        self.assertEqual(handle_run.call_args.args[0]["agent"], "legacy-coder")
        self.assertIn("spin_request is deprecated", "\n".join(logs.output))

    def test_handle_remote_run_request_uses_concise_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = mock.Mock(returncode=0, stdout=json.dumps({"started": "coder"}), stderr="")
            with mock.patch.dict(os.environ, {"BROCCOLI_COMMS_CLI": ""}, clear=False), \
                 mock.patch.object(registry_client.subprocess, "run", return_value=proc) as run, \
                 mock.patch.object(registry_client, "publish_tracker_event", return_value=202) as publish, \
                 mock.patch.object(registry_client, "remote_run_enabled", return_value=True):
                registry_client._handle_remote_run_request({
                    "request_id": "rr-2",
                    "agent": "coder",
                    "cwd": tmp,
                    "scope": "project:remote-run-request",
                    "command": ["pi", "--fast"],
                    "source_tracker_id": "tracker-a",
                })

        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["broccoli-comms", "run", "coder", "--json"])
        self.assertIn("--cwd", argv)
        self.assertIn(tmp, argv)
        self.assertIn("--scope", argv)
        self.assertIn("project:remote-run-request", argv)
        self.assertEqual(argv[-3:], ["--", "pi", "--fast"])
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[0], "tracker-a")
        self.assertEqual(publish.call_args.args[1], "remote_run_result")
        self.assertTrue(publish.call_args.args[2]["ok"])

    def test_handle_remote_run_request_supports_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = mock.Mock(returncode=0, stdout=json.dumps({"started": "coder"}), stderr="")
            with mock.patch.dict(os.environ, {"BROCCOLI_COMMS_CLI": ""}, clear=False), \
                 mock.patch.object(registry_client.subprocess, "run", return_value=proc) as run, \
                 mock.patch.object(registry_client, "publish_tracker_event", return_value=202) as publish, \
                 mock.patch.object(registry_client, "remote_run_enabled", return_value=True):
                registry_client._handle_remote_run_request({
                    "request_id": "rr-2",
                    "agent": "coder",
                    "cwd": tmp,
                    "scope": "project:remote-run-request",
                    "command": ["pi", "--fast"],
                    "source_tracker_id": "tracker-a",
                    "wait": True,
                })

        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["broccoli-comms", "run", "coder", "--json"])
        self.assertIn("--wait", argv)

    def test_remote_run_request_omitted_cwd_command_uses_broccoli_saved_config_semantics(self):
        proc = mock.Mock(returncode=0, stdout=json.dumps({"started": "coder"}), stderr="")
        with mock.patch.dict(os.environ, {"BROCCOLI_COMMS_CLI": ""}, clear=False), \
             mock.patch.object(registry_client.subprocess, "run", return_value=proc) as run, \
             mock.patch.object(registry_client, "publish_tracker_event", return_value=202), \
             mock.patch.object(registry_client, "_load_remote_run_config") as load_legacy, \
             mock.patch.object(registry_client, "remote_run_enabled", return_value=True):
            registry_client._handle_remote_run_request({"request_id": "rr-saved", "agent": "coder", "reply_to_tracker_id": "tracker-a"})

        load_legacy.assert_not_called()
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["broccoli-comms", "run", "coder", "--json"])
        self.assertNotIn("/legacy/cwd", argv)
        self.assertNotIn("legacy-command", argv)

    def test_remote_run_request_publishes_failure_result(self):
        proc = mock.Mock(returncode=2, stdout="", stderr="boom")
        with mock.patch.object(registry_client.subprocess, "run", return_value=proc), \
             mock.patch.object(registry_client, "publish_tracker_event", return_value=202) as publish, \
             mock.patch.object(registry_client, "remote_run_enabled", return_value=True):
            registry_client._handle_remote_run_request({"request_id": "rr-fail", "agent": "coder", "command": "pi", "reply_to_tracker_id": "tracker-a"})
        result = publish.call_args.args[2]
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "boom")
        self.assertEqual(result["request_id"], "rr-fail")

    def test_remote_run_request_can_be_disabled(self):
        with mock.patch.dict(os.environ, {"AGENT_TRACKER_REMOTE_RUN_ENABLED": "0"}, clear=False), \
             mock.patch.object(registry_client.subprocess, "run") as run:
            registry_client._handle_remote_run_request({"request_id": "rr-disabled", "agent": "coder", "command": "pi"})
        run.assert_not_called()

    def test_remote_run_request_drops_non_dict_payload(self):
        with mock.patch.object(registry_client.subprocess, "run") as run, \
             self.assertLogs("agent-tracker.registry", level="WARNING") as logs:
            registry_client._handle_remote_run_request(["not", "a", "dict"])
        run.assert_not_called()
        self.assertIn("payload must be an object", "\n".join(logs.output))

    def test_remote_run_request_drops_oversized_id_fields(self):
        too_long = "x" * (registry_client.REMOTE_RUN_ID_MAX + 1)
        for payload in [
            {"request_id": too_long, "agent": "coder", "command": "pi"},
            {"request_id": "ok", "agent": too_long, "command": "pi"},
            {"request_id": "ok", "agent": "coder", "reply_to_tracker_id": too_long, "command": "pi"},
            {"request_id": "ok", "agent": "coder", "source_tracker_id": too_long, "command": "pi"},
        ]:
            with self.subTest(payload=list(payload.keys())), \
                 mock.patch.object(registry_client.subprocess, "run") as run:
                registry_client._handle_remote_run_request(payload)
                run.assert_not_called()

    def test_remote_run_request_drops_oversized_cwd_scope_and_command(self):
        cases = [
            {"agent": "coder", "cwd": "x" * (registry_client.REMOTE_RUN_PATH_MAX + 1), "command": "pi"},
            {"agent": "coder", "scope": "x" * (registry_client.REMOTE_RUN_SCOPE_MAX + 1), "command": "pi"},
            {"agent": "coder", "command": "x" * (registry_client.REMOTE_RUN_COMMAND_MAX + 1)},
            {"agent": "coder", "command": ["x"] * (registry_client.REMOTE_RUN_COMMAND_PARTS_MAX + 1)},
            {"agent": "coder", "command": ["x" * (registry_client.REMOTE_RUN_COMMAND_PART_MAX + 1)]},
            {"agent": "coder", "command": ["pi", {"bad": "part"}]},
        ]
        for payload in cases:
            with self.subTest(payload_type=type(payload.get("command")).__name__), \
                 mock.patch.object(registry_client.subprocess, "run") as run:
                registry_client._handle_remote_run_request(payload)
                run.assert_not_called()

    def test_remote_run_request_invalid_event_payload_does_not_crash_or_launch(self):
        event = {"event_id": "e-bad-run", "event_type": "remote_run_request", "payload": "not-a-dict"}

        class InlineThread:
            def __init__(self, target, args=(), kwargs=None, daemon=None):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}
            def start(self):
                self.target(*self.args, **self.kwargs)

        with mock.patch.object(registry_client, "fetch_events", side_effect=[(200, {"events": [event]}), SystemExit]), \
             mock.patch.object(registry_client, "ack_event"), \
             mock.patch.object(registry_client.subprocess, "run") as run, \
             mock.patch("registry_client.threading.Thread", new=InlineThread):
            with self.assertRaises(SystemExit):
                registry_client._event_loop()
        run.assert_not_called()

    def test_handle_remote_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            def side_effect(path):
                if path == "~":
                    return tmp
                return path
            with mock.patch("os.path.expanduser", side_effect=side_effect):
                state.state = {
                    "a2": {
                        "agent_id": "a2",
                        "name": "agent2",
                        "aliases": [],
                        "status": "idle",
                        "agent_type": "pi",
                        "agent_cmd": "pi --foo bar",
                        "cwd": f"{tmp}/my-project"
                    }
                }
                state.name_index = {"agent2": "a2"}
                
                registry_client._handle_remote_save("agent2", "agent2-saved-config", "pi custom --args", "custom desc", f"{tmp}/custom-cwd")
                
                config_path = os.path.join(tmp, ".config", "agent-tracker", "agents", "agent2-saved-config", "config.json")
                self.assertTrue(os.path.isfile(config_path))
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                
                self.assertEqual(cfg["directory"], f"{tmp}/custom-cwd")
                self.assertEqual(cfg["agent-command"], "pi")
                self.assertEqual(cfg["agent-args"], ["custom", "--args"])
                self.assertEqual(cfg["description"], "custom desc")

    def test_registry_watch_leases_queue_and_fanout(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            
            # Register source tracker t1 and target tracker t2
            source = {"tracker_id": "t1", "hostname": "host1", "address": "127.0.0.1", "http_port": 19875, "agents": []}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "127.0.0.1", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle"}]}
            store.put_tracker(source)
            store.put_tracker(target)
            
            server, base = start(registry_server.make_handler(store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            
            # Register a remote watch lease for client_win_1 from source t1 watching agent2 on host2
            lease_payload = {
                "client_id": "client_win_1",
                "watch_targets": ["host2/agent2"],
                "lease_seconds": 10.0
            }
            code, body = post(f"{base}/trackers/t1/watch-leases", lease_payload, token="secret")
            self.assertEqual(code, 200)
            
            # Verify watch lease registered inside store
            self.assertIn("t2", store.remote_watch_leases)
            self.assertIn(("t1", "client_win_1"), store.remote_watch_leases["t2"])
            
            # Enqueue a message delivery for target agent2 on target t2. This should trigger event fanout!
            msg_payload = {
                "sender_tracker_id": "t1",
                "sender_agent_name": "agent1",
                "target_agent_id": "a2",
                "message": "hello remote world"
            }
            code, body = post(f"{base}/messages", msg_payload, token="secret")
            self.assertEqual(code, 202)
            
            # Watcher tracker t1 should have received a remote_agent_event tracker event queued!
            code, events = get(f"{base}/trackers/t1/events?wait=0", token="secret")
            self.assertEqual(code, 200)
            self.assertEqual(len(events["events"]), 1)
            event = events["events"][0]
            self.assertEqual(event["event_type"], "remote_agent_event")
            self.assertEqual(event["payload"]["sender"], "agent1")
            self.assertEqual(event["payload"]["message"], "hello remote world")
            self.assertEqual(event["payload"]["target_agent_name"], "host2/agent2")
            
            # Clear watch lease
            req = urllib.request.Request(f"{base}/trackers/t1/watch-leases/client_win_1", headers={"Authorization": "Bearer secret"}, method="DELETE")
            with urllib.request.urlopen(req, timeout=3) as resp:
                self.assertEqual(resp.status, 200)
                
            # Verify cleared
            self.assertNotIn(("t1", "client_win_1"), store.remote_watch_leases.get("t2", {}))

    def test_registry_broad_watch_policy_denial(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            
            source = {"tracker_id": "t1", "hostname": "host1", "address": "127.0.0.1", "http_port": 19875, "agents": []}
            store.put_tracker(source)
            
            server, base = start(registry_server.make_handler(store, token="secret", remote_pane_input_enabled=False))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            
            old_allowed = registry_server.AGENT_REGISTRY_BROAD_WATCH_ALLOWED
            try:
                registry_server.AGENT_REGISTRY_BROAD_WATCH_ALLOWED = False
                
                lease_payload = {
                    "client_id": "client_win_1",
                    "watch_targets": ["host2/agent2"],
                    "scope": "broad",
                    "lease_seconds": 10.0
                }
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    post(f"{base}/trackers/t1/watch-leases", lease_payload, token="secret")
                self.assertEqual(ctx.exception.code, 403)
                self.assertNotIn("t2", store.remote_watch_leases)
            finally:
                registry_server.AGENT_REGISTRY_BROAD_WATCH_ALLOWED = old_allowed

    def test_registry_narrow_watchlist_blocks_passive_broad_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = registry_server.Store(state_path=os.path.join(tmp, "registry-state.json"))
            
            source = {"tracker_id": "t1", "hostname": "host1", "address": "127.0.0.1", "http_port": 19875, "agents": []}
            target = {"tracker_id": "t2", "hostname": "host2", "address": "127.0.0.1", "http_port": 19876, "agents": [{"agent_id": "a2", "name": "agent2", "aliases": [], "status": "idle"}]}
            store.put_tracker(source)
            store.put_tracker(target)
            
            server, base = start(registry_server.make_handler(store, token="secret"))
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            
            lease_payload = {
                "client_id": "client_win_1",
                "watch_targets": ["host2/agent2"],
                "scope": "narrow",
                "lease_seconds": 10.0
            }
            code, body = post(f"{base}/trackers/t1/watch-leases", lease_payload, token="secret")
            self.assertEqual(code, 200)
            
            msg_payload = {
                "sender_tracker_id": "t3",
                "sender_agent_name": "agent3",
                "target_agent_id": "a2",
                "message": "passive observation text"
            }
            code, body = post(f"{base}/messages", msg_payload, token="secret")
            self.assertEqual(code, 202)
            
            code, events = get(f"{base}/trackers/t1/events?wait=0", token="secret")
            self.assertEqual(code, 200)
            self.assertEqual(events["events"], [])


if __name__ == "__main__":
    unittest.main()
