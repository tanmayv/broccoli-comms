import json
import unittest
from unittest import mock

import registry_client


class TestRegistryClientMulti(unittest.TestCase):
    def test_load_registry_clients_requires_registries_json(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(registry_client.config, "get", return_value=[]):
            clients = registry_client.load_registry_clients()
        self.assertEqual(clients, [])

    def test_load_registry_clients_from_json(self):
        payload = json.dumps([
            {"name": "corp", "url": "https://corp.example/", "token": "one"},
            {"name": "lab", "url": "https://lab.example", "token": "two"},
        ])
        with mock.patch.dict("os.environ", {"AGENT_REGISTRIES_JSON": payload}, clear=True):
            clients = registry_client.load_registry_clients()
        self.assertEqual([(c.name, c.url, c.token) for c in clients], [
            ("corp", "https://corp.example", "one"),
            ("lab", "https://lab.example", "two"),
        ])

    def test_load_registry_clients_ignores_entries_without_url(self):
        payload = json.dumps([{"name": "missing"}, {"name": "ok", "url": "https://ok.example"}])
        with mock.patch.dict("os.environ", {"AGENT_REGISTRIES_JSON": payload}, clear=True):
            clients = registry_client.load_registry_clients()
        self.assertEqual([(c.name, c.url) for c in clients], [("ok", "https://ok.example")])

    def test_load_registry_clients_reads_token_file(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="secret\n")):
            payload = json.dumps([{"name": "secure", "url": "https://secure.example", "token-file": "/tmp/token"}])
            with mock.patch.dict("os.environ", {"AGENT_REGISTRIES_JSON": payload}, clear=True):
                clients = registry_client.load_registry_clients()
        self.assertEqual(clients[0].token, "secret")

    def test_record_sync_result_tracks_per_registry_status(self):
        client = registry_client.RegistryClient(name="corp", url="https://corp.example", token="")
        with mock.patch.object(registry_client, "STATUS_PATH", "/tmp/status.json"), \
             mock.patch.object(registry_client.time, "time", return_value=123.0), \
             mock.patch.object(registry_client.fcntl, "flock"), \
             mock.patch("os.makedirs"), \
             mock.patch("builtins.open", mock.mock_open()) as opened:
            registry_client._record_sync_result(200, "heartbeat", client)
        written = "".join(call.args[0] for call in opened().write.call_args_list)
        status = json.loads(written)
        self.assertTrue(status["connected"])
        self.assertEqual(status["registries"]["corp"]["registry_url"], "https://corp.example")
        self.assertEqual(status["registries"]["corp"]["last_operation"], "heartbeat")


if __name__ == "__main__":
    unittest.main()
