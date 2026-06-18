package tracker

import (
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/config"

	"context"
	"encoding/json"
	"net"
	"path/filepath"
	"testing"
	"time"
)

func fakeClient(t *testing.T, handler func(r rpcRequest) any) *Client {
	t.Helper()
	return &Client{
		SocketPath: "fake.sock",
		Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
			client, server := net.Pipe()
			go func() {
				defer server.Close()
				var req rpcRequest
				if err := json.NewDecoder(server).Decode(&req); err != nil {
					return
				}
				_ = json.NewEncoder(server).Encode(map[string]any{
					"jsonrpc": "2.0",
					"id":      req.ID,
					"result":  handler(req),
				})
			}()
			return client, nil
		},
	}
}

func TestDefaultSocketPathUsesBroccoliRuntimeDir(t *testing.T) {
	config.ResetForTest()
	t.Setenv("XDG_CONFIG_HOME", "/dev/null")
	t.Setenv("AGENT_TRACKER_SOCKET", "")
	t.Setenv("BROCCOLI_COMMS_RUNTIME_DIR", "/tmp/broccoli-runtime")
	t.Setenv("XDG_RUNTIME_DIR", "")
	t.Setenv("XDG_CACHE_HOME", "")
	if got, want := DefaultSocketPath(), filepath.Join("/tmp/broccoli-runtime", "agent-tracker.sock"); got != want {
		t.Fatalf("DefaultSocketPath() = %q, want %q", got, want)
	}
}

func TestDefaultSocketPathPrefersExplicitAgentTrackerSocket(t *testing.T) {
	config.ResetForTest()
	t.Setenv("XDG_CONFIG_HOME", "/dev/null")
	t.Setenv("AGENT_TRACKER_SOCKET", "/tmp/private/tracker.sock")
	t.Setenv("BROCCOLI_COMMS_RUNTIME_DIR", "/tmp/broccoli-runtime")
	t.Setenv("XDG_RUNTIME_DIR", "")
	t.Setenv("XDG_CACHE_HOME", "")
	if got := DefaultSocketPath(); got != "/tmp/private/tracker.sock" {
		t.Fatalf("DefaultSocketPath() = %q, want explicit socket", got)
	}
}

func TestDefaultSocketPathUsesCanonicalRuntimeDefault(t *testing.T) {
	config.ResetForTest()
	t.Setenv("XDG_CONFIG_HOME", "/dev/null")
	t.Setenv("AGENT_TRACKER_SOCKET", "")
	t.Setenv("BROCCOLI_COMMS_RUNTIME_DIR", "")
	t.Setenv("XDG_RUNTIME_DIR", "/tmp/xdg-runtime")
	t.Setenv("XDG_CACHE_HOME", "/tmp/xdg-cache")
	if got, want := DefaultSocketPath(), filepath.Join("/tmp/xdg-cache", "broccoli-comms", "runtime", "agent-tracker.sock"); got != want {
		t.Fatalf("DefaultSocketPath() = %q, want %q", got, want)
	}
}

func TestListDecodesDetectionStatus(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		return map[string]Agent{"alpha": {Detection: DetectionStatus{Configured: true, Enabled: true, Provider: "claude", SecondsUntilNextScan: 4, LastResult: "no_match"}}}
	})
	agents, err := client.List(context.Background())
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	status := agents["alpha"].Detection
	if !status.Configured || !status.Enabled || status.Provider != "claude" || status.SecondsUntilNextScan != 4 || status.LastResult != "no_match" {
		t.Fatalf("unexpected detection status: %+v", status)
	}
}

func TestListWithOptionsIncludesRemoteAndPreferredCallerID(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "list" {
			t.Fatalf("method = %s, want list", req.Method)
		}
		params, ok := req.Params.(map[string]any)
		if !ok {
			t.Fatalf("params = %#v", req.Params)
		}
		if params["include_remote"] != true || params["agent_id"] != "caller-id" {
			t.Fatalf("params = %#v", params)
		}
		if _, ok := params["agent_name"]; ok {
			t.Fatalf("agent_name should not be sent when agent_id is available: %#v", params)
		}
		return map[string]Agent{}
	})
	if _, err := client.ListWithOptions(context.Background(), ListOptions{IncludeRemote: true, AgentID: "caller-id", AgentName: "caller-name"}); err != nil {
		t.Fatalf("ListWithOptions: %v", err)
	}
}

func TestListWithOptionsFallsBackToCallerName(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		params, ok := req.Params.(map[string]any)
		if !ok {
			t.Fatalf("params = %#v", req.Params)
		}
		if params["agent_name"] != "caller-name" {
			t.Fatalf("params = %#v", params)
		}
		return map[string]Agent{}
	})
	if _, err := client.ListWithOptions(context.Background(), ListOptions{AgentName: "caller-name"}); err != nil {
		t.Fatalf("ListWithOptions: %v", err)
	}
}

func TestListSetsAgentNamesFromMapKeys(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "list" {
			t.Fatalf("method = %s, want list", req.Method)
		}
		return map[string]Agent{"alpha": {AgentID: "id-1", Status: "idle"}}
	})
	agents, err := client.List(context.Background())
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if agents["alpha"].Name != "alpha" || agents["alpha"].AgentID != "id-1" {
		t.Fatalf("agent = %+v", agents["alpha"])
	}
}

func TestTrackerInfo(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "tracker_info" {
			t.Fatalf("method = %s, want tracker_info", req.Method)
		}
		connected := true
		return TrackerInfo{Status: "ok", Version: "0.1.0", Revision: "abc1234", Build: BuildInfo{Display: "0.1.0+abc1234"}, AgentCount: 3, OnlineAgentCount: 2, RegistryConnected: &connected, RemoteTrackerCount: 4, OnlineRemoteTrackerCount: 3}
	})
	info, err := client.TrackerInfo(context.Background())
	if err != nil || info.AgentCount != 3 || info.OnlineAgentCount != 2 || info.RegistryConnected == nil || !*info.RegistryConnected || info.OnlineRemoteTrackerCount != 3 || info.Build.Display != "0.1.0+abc1234" {
		t.Fatalf("TrackerInfo = %+v, %v", info, err)
	}
}

func TestWaitEventsUsesLongerSocketDeadlineAndFilters(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "wait_events" {
			t.Fatalf("method = %s, want wait_events", req.Method)
		}
		params := req.Params.(map[string]any)
		if params["target_agent_id"] != "id-1" {
			t.Fatalf("target_agent_id = %v", params["target_agent_id"])
		}
		return WaitEventsResult{LastSeq: 7, Events: []Event{{Seq: 7, Type: "message_delivered"}}}
	})
	result, err := client.WaitEvents(context.Background(), WaitOptions{Since: 6, Timeout: 20 * time.Millisecond, TargetAgentID: "id-1"})
	if err != nil {
		t.Fatalf("WaitEvents: %v", err)
	}
	if result.LastSeq != 7 || len(result.Events) != 1 {
		t.Fatalf("result = %+v", result)
	}
}

func TestReadInbox(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "get_inbox" {
			t.Fatalf("method = %s, want get_inbox", req.Method)
		}
		return ReadInboxResult{Mode: "history", Messages: []Message{{Sender: "agent", Body: "hi"}}}
	})
	inbox, err := client.ReadInbox(context.Background(), "alpha", 5, false)
	if err != nil || len(inbox.Messages) != 1 {
		t.Fatalf("ReadInbox = %+v, %v", inbox, err)
	}
}

func TestReadInboxForSenderAddsStableFilters(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "get_inbox" {
			t.Fatalf("method = %s, want get_inbox", req.Method)
		}
		params := req.Params.(map[string]any)
		if params["sender_agent_id"] != "a1" || params["sender_tracker_id"] != "t1" || params["sender_name"] != "alice" {
			t.Fatalf("params = %+v", params)
		}
		return ReadInboxResult{Mode: "last_n", Messages: []Message{{Sender: "alice", Body: "hi"}}}
	})
	inbox, err := client.ReadInboxForSender(context.Background(), "agent-communicator", 5, false, "a1", "t1", "alice")
	if err != nil || len(inbox.Messages) != 1 {
		t.Fatalf("ReadInboxForSender = %+v, %v", inbox, err)
	}
}

func TestGetUnreadCounts(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "get_unread_counts" {
			t.Fatalf("method = %s, want get_unread_counts", req.Method)
		}
		params := req.Params.(map[string]any)
		if params["agent_name"] != "agent-communicator" {
			t.Fatalf("agent_name = %v", params["agent_name"])
		}
		return UnreadCountsResult{Counts: map[string]int{"local:a1": 2}, Total: 2}
	})
	counts, err := client.GetUnreadCounts(context.Background(), "agent-communicator")
	if err != nil || counts.Counts["local:a1"] != 2 || counts.Total != 2 {
		t.Fatalf("GetUnreadCounts = %+v, %v", counts, err)
	}
}

func TestSendMessageUsesAgentNameForPlainLocalTarget(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "send_message" {
			t.Fatalf("method = %s, want send_message", req.Method)
		}
		params := req.Params.(map[string]any)
		if params["agent_name"] != "alpha" || params["target_address"] != nil || params["agent_id"] != nil || params["interrupt"] != true {
			t.Fatalf("params = %+v", params)
		}
		return true
	})
	if err := client.SendMessage(context.Background(), "alpha", "hello", nil); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}
}

func TestSendMessageUsesTargetAddressForHostQualifiedTarget(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		params := req.Params.(map[string]any)
		if params["target_address"] != "host/alpha" || params["agent_name"] != nil || params["interrupt"] != true {
			t.Fatalf("params = %+v", params)
		}
		return true
	})
	if err := client.SendMessage(context.Background(), "host/alpha", "hello", nil); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}
}

func TestSendMessageFromIncludesSenderName(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		params := req.Params.(map[string]any)
		if params["sender_name"] != "agent-communicator" || params["agent_name"] != "alpha" || params["interrupt"] != true {
			t.Fatalf("params = %+v", params)
		}
		return true
	})
	if err := client.SendMessageFrom(context.Background(), "agent-communicator", "alpha", "hello", nil); err != nil {
		t.Fatalf("SendMessageFrom: %v", err)
	}
}

func TestSendMessageWithContextIncludesSwarmContext(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		params := req.Params.(map[string]any)
		if params["sender_name"] != "agent-communicator" || params["agent_name"] != "planner" || params["message_id"] != "sent-1" || params["swarm_context"] != "backend-fix" || params["interrupt"] != true {
			t.Fatalf("params = %+v", params)
		}
		return true
	})
	if err := client.SendMessageWithContext(context.Background(), "agent-communicator", "planner", "hello", "sent-1", "backend-fix", nil); err != nil {
		t.Fatalf("SendMessageWithContext: %v", err)
	}
}

func TestSendMessageUsesLocalTargetAddressForUUIDTarget(t *testing.T) {
	target := "123e4567-e89b-12d3-a456-426614174000"
	client := fakeClient(t, func(req rpcRequest) any {
		params := req.Params.(map[string]any)
		if params["target_address"] != "local/"+target || params["agent_id"] != nil || params["interrupt"] != true {
			t.Fatalf("params = %+v", params)
		}
		return true
	})
	if err := client.SendMessage(context.Background(), target, "hello", nil); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}
}

func TestSendTextCallsSendInput(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "send_input" {
			t.Fatalf("method = %s, want send_input", req.Method)
		}
		params := req.Params.(map[string]any)
		if params["agent_name"] != "alpha" || params["input_type"] != "text" || params["text"] != "hello" || params["submit"] != false {
			t.Fatalf("params = %+v", params)
		}
		return map[string]any{"success": true}
	})
	if err := client.SendText(context.Background(), "alpha", "hello", false); err != nil {
		t.Fatalf("SendText: %v", err)
	}
}

func TestSendKeysCallsSendInputWithUUIDAgentID(t *testing.T) {
	target := "123e4567-e89b-12d3-a456-426614174000"
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "send_input" {
			t.Fatalf("method = %s, want send_input", req.Method)
		}
		params := req.Params.(map[string]any)
		keys, _ := params["keys"].([]any)
		if params["agent_id"] != target || params["target_address"] != nil || params["input_type"] != "keys" || len(keys) != 2 || keys[0] != "C-c" || keys[1] != "Enter" {
			t.Fatalf("params = %+v", params)
		}
		return map[string]any{"success": true}
	})
	if err := client.SendKeys(context.Background(), target, []string{"C-c", "Enter"}); err != nil {
		t.Fatalf("SendKeys: %v", err)
	}
}

func TestCallHonorsContextCancellationAfterDial(t *testing.T) {
	client := &Client{
		SocketPath: "fake.sock",
		Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
			client, _ := net.Pipe()
			return client, nil
		},
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := client.SendMessage(ctx, "alpha", "hello", nil); err == nil {
		t.Fatal("SendMessage succeeded, want context cancellation error")
	}
}

func fakeErrorClient(t *testing.T, method string, rpcErr map[string]any) *Client {
	t.Helper()
	return &Client{
		SocketPath: "fake.sock",
		Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
			client, server := net.Pipe()
			go func() {
				defer server.Close()
				var req rpcRequest
				if err := json.NewDecoder(server).Decode(&req); err != nil {
					return
				}
				if req.Method != method {
					t.Errorf("method = %s, want %s", req.Method, method)
				}
				_ = json.NewEncoder(server).Encode(map[string]any{
					"jsonrpc": "2.0",
					"id":      req.ID,
					"error":   rpcErr,
				})
			}()
			return client, nil
		},
	}
}

func TestEnsureMailboxCallsRPC(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "ensure_mailbox" {
			t.Fatalf("method = %s, want ensure_mailbox", req.Method)
		}
		params := req.Params.(map[string]any)
		if params["agent_name"] != "agent-communicator" || params["preserve_pane"] != true {
			t.Fatalf("params = %+v", params)
		}
		return EnsureMailboxResult{Name: "agent-communicator", AgentID: "id-1", UUID: "id-1"}
	})
	result, err := client.EnsureMailbox(context.Background(), "agent-communicator")
	if err != nil || result.AgentID != "id-1" {
		t.Fatalf("EnsureMailbox = %+v, %v", result, err)
	}
}

func TestRPCErrorPreservesRawStringAndStructuredData(t *testing.T) {
	client := fakeErrorClient(t, "get_inbox", map[string]any{
		"code":    -32004,
		"message": "Agent 'agent-communicator' not found",
		"data": map[string]any{
			"error_code": "agent_not_found",
			"agent":      "agent-communicator",
			"operation":  "get_inbox",
			"retryable":  true,
		},
	})
	_, err := client.ReadInbox(context.Background(), "agent-communicator", 5, false)
	if err == nil {
		t.Fatal("ReadInbox succeeded, want error")
	}
	if err.Error() != "tracker rpc get_inbox failed: Agent 'agent-communicator' not found" {
		t.Fatalf("error string = %q", err.Error())
	}
	rpcErr, ok := err.(*RPCError)
	if !ok || rpcErr.Data == nil || rpcErr.Data.ErrorCode != "agent_not_found" || !rpcErr.Data.Retryable {
		t.Fatalf("rpc error = %#v", err)
	}
}

func TestRequestStopCallsRPC(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "request_stop" {
			t.Fatalf("method = %s, want request_stop", req.Method)
		}
		params := req.Params.(map[string]any)
		if params["target_address"] != "alpha" || params["timeout"] != "20s" || params["force"] != false {
			t.Fatalf("params = %+v", params)
		}
		return true
	})
	success, err := client.RequestStop(context.Background(), "alpha", "20s", false)
	if err != nil || !success {
		t.Fatalf("RequestStop: success=%t, err=%v", success, err)
	}
}

func TestRestartAgentCallsRPC(t *testing.T) {
	client := fakeClient(t, func(req rpcRequest) any {
		if req.Method != "restart_agent" {
			t.Fatalf("method = %s, want restart_agent", req.Method)
		}
		params := req.Params.(map[string]any)
		if params["target_address"] != "alpha" {
			t.Fatalf("params = %+v", params)
		}
		return true
	})
	success, err := client.RestartAgent(context.Background(), "alpha")
	if err != nil || !success {
		t.Fatalf("RestartAgent: success=%t, err=%v", success, err)
	}
}
