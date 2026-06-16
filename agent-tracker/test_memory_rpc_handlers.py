import os
import sys
import unittest
import tempfile
from pathlib import Path
from unittest import mock

# Add parent directories to sys.path
_workspace_root = Path(__file__).resolve().parents[1]
if str(_workspace_root) not in sys.path:
    sys.path.insert(0, str(_workspace_root))
_agent_tracker_dir = _workspace_root / "agent-tracker"
if str(_agent_tracker_dir) not in sys.path:
    sys.path.insert(0, str(_agent_tracker_dir))

from handlers import memory_handlers
from rpc_handler import RPCStructuredError

class TestMemoryRpcHandlers(unittest.TestCase):
    def setUp(self):
        # Create temp dir for isolated database
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        
        # Patch the cache dir helper in memory_handlers
        self.cache_dir_patcher = mock.patch("handlers.memory_handlers._get_app_cache_dir", return_value=self.temp_path)
        self.cache_dir_patcher.start()
        
        # Reset the kernel instance to force re-initialization with the temp path
        memory_handlers._kernel_instance = None
        
    def tearDown(self):
        self.cache_dir_patcher.stop()
        self.temp_dir.cleanup()
        memory_handlers._kernel_instance = None

    def mock_identify(self, name):
        return lambda params, caller_pid=None: name

    def test_memory_propose_and_approve_lifecycle(self):
        # 1. Propose (v1 pending)
        params = {
            "type": "fact",
            "title": "Port",
            "body": "5432",
            "scope": "global",
            "trusted_manual": True
        }
        res = memory_handlers.handle_memory_propose(params, identify_agent=self.mock_identify("user"))
        self.assertIsNotNone(res)
        mem = res["memory"]
        self.assertEqual(mem["version"], 1)
        self.assertEqual(mem["status"], "pending")
        self.assertEqual(mem["title"], "Port")
        self.assertEqual(mem["body"], "5432")
        self.assertEqual(mem["proposed_by"], "user")
        self.assertIsNotNone(mem.get("source_event_seq"))
        
        memory_id = mem["memory_id"]
        
        # 2. List (should show pending)
        list_res = memory_handlers.handle_memory_list({"status": "pending"})
        self.assertEqual(len(list_res), 1)
        self.assertEqual(list_res[0]["memory_id"], memory_id)
        
        # 3. Approve (v1 active)
        approve_res = memory_handlers.handle_memory_approve({"memory_id": memory_id, "version": 1}, identify_agent=self.mock_identify("user"))
        self.assertEqual(approve_res["memory"]["status"], "active")
        self.assertEqual(approve_res["memory"]["version"], 1)
        self.assertEqual(approve_res["event"]["event_type"], "memory_approved")
        
        # 4. List (should show active)
        list_res = memory_handlers.handle_memory_list({"status": "active"})
        self.assertEqual(len(list_res), 1)
        self.assertEqual(list_res[0]["memory_id"], memory_id)
        
        # 5. Propose Edit (v2 pending_edit)
        edit_params = {
            "memory_id": memory_id,
            "expected_version": 1,
            "body": "5433"
        }
        edit_res = memory_handlers.handle_memory_propose_edit(edit_params, identify_agent=self.mock_identify("user"))
        self.assertEqual(edit_res["memory"]["version"], 2)
        self.assertEqual(edit_res["memory"]["status"], "pending_edit")
        self.assertEqual(edit_res["memory"]["body"], "5433")
        
        # 6. Show (should show active v1 by default, and pending v2 if requested)
        show_active = memory_handlers.handle_memory_show({"memory_id": memory_id})
        self.assertEqual(show_active["version"], 1)
        self.assertEqual(show_active["status"], "active")
        self.assertEqual(show_active["body"], "5432")
        
        show_pending = memory_handlers.handle_memory_show({"memory_id": memory_id, "version": 2})
        self.assertEqual(show_pending["version"], 2)
        self.assertEqual(show_pending["status"], "pending_edit")
        self.assertEqual(show_pending["body"], "5433")
        
        # 7. Approve Edit (v2 active, v1 superseded)
        approve_edit_res = memory_handlers.handle_memory_approve({"memory_id": memory_id, "version": 2}, identify_agent=self.mock_identify("user"))
        self.assertEqual(approve_edit_res["memory"]["status"], "active")
        self.assertEqual(approve_edit_res["memory"]["version"], 2)
        self.assertEqual(approve_edit_res["event"]["event_type"], "memory_edited")
        
        # Verify v1 is superseded
        show_v1 = memory_handlers.handle_memory_show({"memory_id": memory_id, "version": 1})
        self.assertEqual(show_v1["status"], "superseded")
        
        # 8. History
        history_res = memory_handlers.handle_memory_history({"memory_id": memory_id})
        self.assertEqual(history_res["memory_id"], memory_id)
        self.assertEqual(len(history_res["versions"]), 2)
        self.assertEqual(history_res["versions"][0]["version"], 1)
        self.assertEqual(history_res["versions"][0]["status"], "superseded")
        self.assertEqual(history_res["versions"][1]["version"], 2)
        self.assertEqual(history_res["versions"][1]["status"], "active")

        # 9. Search
        search_res = memory_handlers.handle_memory_search({"query": "database"})
        # No match expected for "database" yet since title is "Port" and body is "5433"
        self.assertEqual(len(search_res), 0)
        
        search_res_match = memory_handlers.handle_memory_search({"query": "5433"})
        self.assertEqual(len(search_res_match), 1)
        self.assertEqual(search_res_match[0]["memory_id"], memory_id)

    def test_memory_propose_archive_and_reject_flow(self):
        # Setup: Propose and approve v1 active
        params = {"type": "fact", "title": "A", "body": "A", "trusted_manual": True}
        res = memory_handlers.handle_memory_propose(params, identify_agent=self.mock_identify("user"))
        memory_id = res["memory"]["memory_id"]
        memory_handlers.handle_memory_approve({"memory_id": memory_id, "version": 1}, identify_agent=self.mock_identify("user"))
        
        # 1. Propose Archive (v2 pending_revocation)
        archive_res = memory_handlers.handle_memory_propose_archive({"memory_id": memory_id, "expected_version": 1, "reason": "obsolete"}, identify_agent=self.mock_identify("user"))
        self.assertEqual(archive_res["memory"]["version"], 2)
        self.assertEqual(archive_res["memory"]["status"], "pending_revocation")
        self.assertEqual(archive_res["memory"]["metadata"]["archive_reason"], "obsolete")
        
        # 2. Reject Archive (v2 rejected, v1 remains active)
        reject_res = memory_handlers.handle_memory_reject({"memory_id": memory_id, "version": 2, "reason": "still needed"}, identify_agent=self.mock_identify("user"))
        self.assertEqual(reject_res["memory"]["status"], "rejected")
        self.assertEqual(reject_res["event"]["event_type"], "memory_rejected")
        
        show_v1 = memory_handlers.handle_memory_show({"memory_id": memory_id, "version": 1})
        self.assertEqual(show_v1["status"], "active")
        
        # 3. Propose Archive again (v3 pending_revocation, since v2 is rejected)
        archive_res2 = memory_handlers.handle_memory_propose_archive({"memory_id": memory_id, "expected_version": 1, "reason": "really obsolete"}, identify_agent=self.mock_identify("user"))
        self.assertEqual(archive_res2["memory"]["version"], 3)
        self.assertEqual(archive_res2["memory"]["status"], "pending_revocation")
        
        # 4. Approve Archive (v3 revoked, v1 superseded)
        approve_archive_res = memory_handlers.handle_memory_approve({"memory_id": memory_id, "version": 3}, identify_agent=self.mock_identify("user"))
        self.assertEqual(approve_archive_res["memory"]["status"], "revoked")
        self.assertEqual(approve_archive_res["event"]["event_type"], "memory_revoked")
        
        show_v1_post = memory_handlers.handle_memory_show({"memory_id": memory_id, "version": 1})
        self.assertEqual(show_v1_post["status"], "superseded")

    def test_structured_error_mapping(self):
        # Setup: Propose v1
        params = {"type": "fact", "title": "A", "body": "A", "trusted_manual": True}
        res = memory_handlers.handle_memory_propose(params, identify_agent=self.mock_identify("user"))
        memory_id = res["memory"]["memory_id"]
        
        # 1. Test idempotency of reject: rejecting again is idempotent
        reject_res1 = memory_handlers.handle_memory_reject({"memory_id": memory_id, "version": 1}, identify_agent=self.mock_identify("user"))
        self.assertFalse(reject_res1.get("idempotent", False))
        reject_res2 = memory_handlers.handle_memory_reject({"memory_id": memory_id, "version": 1}, identify_agent=self.mock_identify("user"))
        self.assertTrue(reject_res2.get("idempotent", False))
        
        # Test transition conflict: try to reject an already active memory
        res_active = memory_handlers.handle_memory_propose(params, identify_agent=self.mock_identify("user"))
        active_id = res_active["memory"]["memory_id"]
        memory_handlers.handle_memory_approve({"memory_id": active_id, "version": 1}, identify_agent=self.mock_identify("user"))
        
        with self.assertRaises(RPCStructuredError) as ctx:
            memory_handlers.handle_memory_reject({"memory_id": active_id, "version": 1}, identify_agent=self.mock_identify("user"))
        self.assertEqual(ctx.exception.code, -32003) # transition conflict
        self.assertEqual(ctx.exception.data["memory_id"], active_id)
        
        # Setup: Propose and approve new memory
        res = memory_handlers.handle_memory_propose(params, identify_agent=self.mock_identify("user"))
        memory_id2 = res["memory"]["memory_id"]
        memory_handlers.handle_memory_approve({"memory_id": memory_id2, "version": 1}, identify_agent=self.mock_identify("user"))
        
        # 2. Test stale version check: propose edit with wrong expected version
        with self.assertRaises(RPCStructuredError) as ctx:
            memory_handlers.handle_memory_propose_edit({"memory_id": memory_id2, "expected_version": 99, "body": "B"}, identify_agent=self.mock_identify("user"))
        self.assertEqual(ctx.exception.code, -32002) # stale memory version
        
        # 3. Test actor check: anyone is trusted, so untrusted-agent can approve!
        res_prop = memory_handlers.handle_memory_propose_edit({"memory_id": memory_id2, "expected_version": 1, "body": "B"}, identify_agent=self.mock_identify("user"))
        approve_untrusted = memory_handlers.handle_memory_approve(
            {"memory_id": memory_id2, "version": res_prop["memory"]["version"]}, 
            identify_agent=self.mock_identify("untrusted-agent")
        )
        self.assertEqual(approve_untrusted["memory"]["status"], "active")
        self.assertEqual(approve_untrusted["memory"]["version"], res_prop["memory"]["version"])

    def test_new_rpc_endpoints_direct_access(self):
        # Setup: Propose and approve v1 active
        params = {"type": "fact", "title": "A", "body": "A", "trusted_manual": True}
        res = memory_handlers.handle_memory_propose(params, identify_agent=self.mock_identify("user"))
        memory_id = res["memory"]["memory_id"]
        memory_handlers.handle_memory_approve({"memory_id": memory_id, "version": 1}, identify_agent=self.mock_identify("user"))
        
        # 1. Test memory.edit (direct edit)
        edit_res = memory_handlers.handle_memory_edit({
            "memory_id": memory_id,
            "expected_version": 1,
            "body": "A-edited",
            "title": "A-edited"
        }, identify_agent=self.mock_identify("user"))
        
        self.assertEqual(edit_res["memory"]["version"], 2)
        self.assertEqual(edit_res["memory"]["status"], "active")
        self.assertEqual(edit_res["memory"]["body"], "A-edited")
        
        show_res = memory_handlers.handle_memory_show({"memory_id": memory_id})
        self.assertEqual(show_res["body"], "A-edited")
        
        # 2. Test memory.rollback (direct rollback)
        rollback_res = memory_handlers.handle_memory_rollback({
            "memory_id": memory_id,
            "target_version": 1,
            "expected_version": 2
        }, identify_agent=self.mock_identify("user"))
        
        self.assertEqual(rollback_res["memory"]["version"], 3)
        self.assertEqual(rollback_res["memory"]["status"], "active")
        self.assertEqual(rollback_res["memory"]["body"], "A")
        
        show_res2 = memory_handlers.handle_memory_show({"memory_id": memory_id})
        self.assertEqual(show_res2["body"], "A")
        
        # 3. Test memory.budget (query budget)
        budget_res = memory_handlers.handle_memory_budget({"agent": "user"})
        self.assertIsNotNone(budget_res.get("active"))
        self.assertIsNotNone(budget_res.get("limits"))
        self.assertIsNotNone(budget_res.get("pending"))
        
        # 4. Test memory.revoke (direct revoke)
        revoke_res = memory_handlers.handle_memory_revoke({
            "memory_id": memory_id,
            "expected_version": 3,
            "reason": "obsolete"
        }, identify_agent=self.mock_identify("user"))
        
        self.assertEqual(revoke_res["memory"]["version"], 4)
        self.assertEqual(revoke_res["memory"]["status"], "revoked")
        self.assertEqual(revoke_res["memory"]["metadata"]["archive_reason"], "obsolete")
        
        show_v4 = memory_handlers.handle_memory_show({"memory_id": memory_id, "version": 4})
        self.assertEqual(show_v4["status"], "revoked")
