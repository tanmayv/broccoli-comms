import unittest
from unittest import mock
import time
import json

# Setup import path for local module loading
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from handlers import agent_handlers
import registry_client

class TestAgentHandlersGracefulTimeout(unittest.TestCase):
    @mock.patch("registry_client.load_registry_clients")
    def test_fetch_registry_agents_graceful_timeout(self, mock_load_clients):
        # Create a fast mock client
        fast_client = mock.MagicMock(spec=registry_client.RegistryClient)
        fast_client.name = "fast_reg"
        fast_client.fetch_agents.return_value = (200, {
            "agents": [
                {"name": "agent-fast", "hostname": "host-fast", "agent_id": "id-fast"}
            ]
        })
        fast_client.fetch_trackers.return_value = (200, {"trackers": []})

        # Create a slow mock client that hangs
        slow_client = mock.MagicMock(spec=registry_client.RegistryClient)
        slow_client.name = "slow_reg"
        
        def slow_fetch(*args, **kwargs):
            time.sleep(5.0)  # Hang for 5 seconds
            return (200, {"agents": []})
            
        slow_client.fetch_agents.side_effect = slow_fetch
        slow_client.fetch_trackers.return_value = (200, {"trackers": []})

        # Mock load_registry_clients to return both
        mock_load_clients.return_value = [fast_client, slow_client]

        start_time = time.time()
        # Call the target function
        remote_agents = agent_handlers._fetch_registry_agents_for_list()
        end_time = time.time()

        # Assertions:
        # 1. It should complete in less than 3.0 seconds (since total wait timeout is 2.0s)
        duration = end_time - start_time
        print(f"\nGraceful timeout test completed in {duration:.4f} seconds.")
        self.assertLess(duration, 3.0)
        
        # 2. It should successfully return the agent from the fast registry
        self.assertIn("host-fast/agent-fast", remote_agents)
        self.assertEqual(remote_agents["host-fast/agent-fast"]["registry_name"], "fast_reg")

        # 3. The slow registry should be reported as a virtual error agent
        self.assertIn("registry-error/slow_reg", remote_agents)
        err_agent = remote_agents["registry-error/slow_reg"]
        self.assertEqual(err_agent["name"], "⚠️ Registry 'slow_reg' offline")
        self.assertEqual(err_agent["status"], "offline")
        self.assertEqual(err_agent["cwd"], "Connection timed out")

if __name__ == "__main__":
    unittest.main()
