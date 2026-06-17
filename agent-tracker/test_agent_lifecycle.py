import unittest
from unittest import mock
import os
import signal
import time as real_time

# Save a reference to the real sleep function before any patching occurs
_real_sleep = real_time.sleep

# Setup import path for local module loading
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from handlers import agent_handlers
import rpc_handler
import state
import registry_client

class TestAgentLifecycleHandlers(unittest.TestCase):
    def setUp(self):
        self.state_patch = mock.patch("state.get_agent")
        self.mock_get_agent = self.state_patch.start()
        
        self.state_name_patch = mock.patch("state.get_agent_name_by_id")
        self.mock_get_name_by_id = self.state_name_patch.start()
        
        self.os_kill_patch = mock.patch("os.kill")
        self.mock_kill = self.os_kill_patch.start()
        
        self.send_symbolic_patch = mock.patch("tmux_util.send_symbolic_keys")
        self.mock_send_symbolic_keys = self.send_symbolic_patch.start()
        
        self.send_literal_patch = mock.patch("tmux_util.send_literal_text")
        self.mock_send_literal_text = self.send_literal_patch.start()
        
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(self.state_name_patch.stop)
        self.addCleanup(self.os_kill_patch.stop)
        self.addCleanup(self.send_symbolic_patch.stop)
        self.addCleanup(self.send_literal_patch.stop)

    def test_handle_request_stop_local_graceful(self):
        # Mock local agent with a wrapper and tmux pane info
        self.mock_get_agent.return_value = {
            "agent_id": "id-123",
            "wrapper_pid": 5000,
            "pid": 5001,
            "scope": "local",
            "tmux_pane": "%1",
            "tmux_socket": "/tmp/tmux.sock"
        }
        self.mock_get_name_by_id.return_value = "local-agent"

        params = {"target_address": "local-agent", "timeout": "45s"}
        result = agent_handlers.handle_request_stop(params)

        self.assertTrue(result)
        # Cooperative graceful stop: should NOT kill immediately
        self.mock_kill.assert_not_called()
        
        # Should send Escape
        self.mock_send_symbolic_keys.assert_called_once_with("%1", ["Escape"], socket_path="/tmp/tmux.sock")
        
        # Should send warning keys
        expected_msg = "> Prepare for restart in 45s. Do memory audit and update task so that can takeover from the same. Call broccoli-comms agent local-agent restart when ready"
        self.mock_send_literal_text.assert_called_once_with("%1", expected_msg, submit=True, socket_path="/tmp/tmux.sock")

    def test_handle_request_stop_local_force(self):
        # Mock local agent
        self.mock_get_agent.return_value = {
            "agent_id": "id-123",
            "wrapper_pid": 5000,
            "pid": 5001,
            "scope": "local",
            "name": "local-agent"
        }
        self.mock_get_name_by_id.return_value = "local-agent"

        # Pass force=True
        params = {"target_address": "local-agent", "force": True}
        result = agent_handlers.handle_request_stop(params)

        self.assertTrue(result)
        # Immediate termination: should call SIGTERM immediately
        self.mock_kill.assert_called_once_with(5000, signal.SIGTERM)
        
        # Should NOT send any tmux keys
        self.mock_send_symbolic_keys.assert_not_called()
        self.mock_send_literal_text.assert_not_called()

    @mock.patch("time.sleep")
    def test_handle_request_stop_local_timeout_fallback(self, mock_sleep):
        # Mock local agent. state.get_agent returns info both times (initial & fallback check)
        self.mock_get_agent.return_value = {
            "agent_id": "id-123",
            "wrapper_pid": 5000,
            "pid": 5001,
            "scope": "local",
            "name": "local-agent",
            "tmux_pane": "%1",
            "tmux_socket": "/tmp/tmux.sock"
        }
        self.mock_get_name_by_id.return_value = "local-agent"

        params = {"target_address": "local-agent", "timeout": 5}
        result = agent_handlers.handle_request_stop(params)

        self.assertTrue(result)
        
        # Use the real sleep to wait for the background thread to run its body
        _real_sleep(0.05)
        
        # Verify the thread slept for 5.0 seconds
        mock_sleep.assert_called_once_with(5.0)
        
        # And since the agent didn't exit, it should have force-terminated it!
        self.mock_kill.assert_called_once_with(5000, signal.SIGTERM)

    @mock.patch("time.sleep")
    def test_handle_request_stop_local_timeout_exited_gracefully(self, mock_sleep):
        # state.get_agent is called:
        # 1. In _resolve_local_agent_name (returns agent_info)
        # 2. In handle_request_stop (returns agent_info)
        # 3. In the fallback thread (returns None, meaning it exited gracefully)
        agent_info = {
            "agent_id": "id-123",
            "wrapper_pid": 5000,
            "pid": 5001,
            "scope": "local",
            "name": "local-agent",
            "tmux_pane": "%1",
            "tmux_socket": "/tmp/tmux.sock"
        }
        self.mock_get_agent.side_effect = [agent_info, agent_info, None]
        self.mock_get_name_by_id.return_value = "local-agent"

        params = {"target_address": "local-agent", "timeout": 5}
        result = agent_handlers.handle_request_stop(params)

        self.assertTrue(result)
        
        # Use real sleep to wait for thread execution
        _real_sleep(0.05)
        
        # It should NOT have force-terminated the agent because it exited gracefully!
        self.mock_kill.assert_not_called()

    def test_handle_request_stop_local_missing_pane(self):
        self.mock_get_agent.return_value = {
            "agent_id": "id-123",
            "wrapper_pid": 5000,
            "pid": 5001,
            "scope": "local",
            "tmux_pane": None,
            "tmux_socket": None
        }
        self.mock_get_name_by_id.return_value = "local-agent"

        params = {"target_address": "local-agent"}
        with self.assertRaises(RuntimeError) as ctx:
            agent_handlers.handle_request_stop(params)
        self.assertIn("has no registered tmux pane", str(ctx.exception))

    def test_handle_restart_agent_local(self):
        self.mock_get_agent.return_value = {
            "agent_id": "id-123",
            "wrapper_pid": 5000,
            "pid": 5001,
            "scope": "local"
        }
        self.mock_get_name_by_id.return_value = "local-agent"

        params = {"target_address": "local-agent"}
        result = agent_handlers.handle_restart_agent(params)

        self.assertTrue(result)
        self.mock_kill.assert_called_once_with(5000, signal.SIGUSR1)

    def test_handle_restart_agent_fails_without_wrapper(self):
        self.mock_get_agent.return_value = {
            "agent_id": "id-123",
            "wrapper_pid": None,
            "pid": 5001,
            "scope": "local"
        }
        self.mock_get_name_by_id.return_value = "local-agent"

        params = {"target_address": "local-agent"}
        with self.assertRaises(RuntimeError) as ctx:
            agent_handlers.handle_restart_agent(params)
        self.assertIn("only supported for agents running under agent-wrapper", str(ctx.exception))

    @mock.patch("registry_client.find_remote_agent")
    @mock.patch("registry_client.publish_tracker_event")
    def test_handle_request_stop_remote(self, mock_publish_event, mock_find_agent):
        mock_find_agent.return_value = {
            "agent_id": "remote-id",
            "tracker_id": "remote-tracker-123",
            "name": "remote-agent"
        }
        mock_publish_event.return_value = 200

        params = {"target_address": "host-remote/remote-agent", "timeout": 45, "force": True}
        result = agent_handlers.handle_request_stop(params)

        self.assertTrue(result)
        mock_find_agent.assert_called_once_with("host-remote", "remote-agent", registry_name=None)
        # Should include timeout and force flags in the remote event payload
        mock_publish_event.assert_called_once_with(
            "remote-tracker-123",
            "remote_stop_request",
            {"target_agent_id": "remote-id", "target_agent_name": "remote-agent", "timeout": "45s", "force": True}
        )

    @mock.patch("config.get")
    def test_handle_request_stop_local_custom_message(self, mock_config_get):
        mock_config_get.return_value = "WARNING: restarting {agent} in {timeout}! Please prepare!"
        
        self.mock_get_agent.return_value = {
            "agent_id": "id-123",
            "wrapper_pid": 5000,
            "pid": 5001,
            "scope": "local",
            "tmux_pane": "%3",
            "tmux_socket": "/tmp/tmux.sock"
        }
        self.mock_get_name_by_id.return_value = "custom-agent"

        params = {"target_address": "custom-agent", "timeout": "10s"}
        result = agent_handlers.handle_request_stop(params)

        self.assertTrue(result)
        
        default_warn_msg = "> Prepare for restart in {timeout}. Do memory audit and update task so that can takeover from the same. Call broccoli-comms agent {agent} restart when ready"
        mock_config_get.assert_called_once_with("tracker", "restart_warn_message", default_warn_msg)
        
        expected_msg = "WARNING: restarting custom-agent in 10s! Please prepare!"
        self.mock_send_literal_text.assert_called_once_with("%3", expected_msg, submit=True, socket_path="/tmp/tmux.sock")

if __name__ == "__main__":
    unittest.main()
