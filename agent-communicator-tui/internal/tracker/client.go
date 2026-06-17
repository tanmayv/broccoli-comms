package tracker

import (
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/config"

	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Client struct {
	SocketPath string
	Dial       func(ctx context.Context, network, address string) (net.Conn, error)
}

type rpcRequest struct {
	JSONRPC string `json:"jsonrpc"`
	Method  string `json:"method"`
	Params  any    `json:"params"`
	ID      int    `json:"id"`
}

type rpcResponse struct {
	Result json.RawMessage `json:"result"`
	Error  *rpcError       `json:"error"`
}

type rpcError struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data,omitempty"`
}

type RPCErrorData struct {
	ErrorCode string `json:"error_code,omitempty"`
	Agent     string `json:"agent,omitempty"`
	Hostname  string `json:"hostname,omitempty"`
	Operation string `json:"operation,omitempty"`
	Retryable bool   `json:"retryable,omitempty"`
}

type RPCError struct {
	Method  string
	Code    int
	Message string
	Data    *RPCErrorData
}

func (e *RPCError) Error() string {
	return fmt.Sprintf("tracker rpc %s failed: %s", e.Method, e.Message)
}

func DefaultSocketPath() string {
	candidates := []string{}
	if path := os.Getenv("AGENT_TRACKER_SOCKET"); path != "" {
		return path
	}
	if path := config.GetString("", "paths", "agent_tracker_socket"); path != "" {
		return path
	}
	if runtimeDir := os.Getenv("BROCCOLI_COMMS_RUNTIME_DIR"); runtimeDir != "" {
		return filepath.Join(runtimeDir, "agent-tracker.sock")
	}
	if runtimeDir := config.GetString("", "paths", "runtime_dir"); runtimeDir != "" {
		path := filepath.Join(runtimeDir, "agent-tracker.sock")
		return path
	}

	cacheHome := os.Getenv("XDG_CACHE_HOME")
	if cacheHome == "" {
		home, _ := os.UserHomeDir()
		cacheHome = filepath.Join(home, ".cache")
	}
	candidates = append(candidates, filepath.Join(cacheHome, "broccoli-comms", "runtime", "agent-tracker.sock"))

	if runtimeDir := os.Getenv("XDG_RUNTIME_DIR"); runtimeDir != "" {
		candidates = append(candidates, filepath.Join(runtimeDir, "broccoli-comms", "agent-tracker.sock"))
	} else {
		candidates = append(candidates, filepath.Join("/tmp", fmt.Sprint(os.Getuid()), "broccoli-comms", "agent-tracker.sock"))
	}

	for _, p := range candidates {
		if _, err := os.Stat(p); err == nil {
			conn, err := net.DialTimeout("unix", p, 100*time.Millisecond)
			if err == nil {
				conn.Close()
				return p
			}
		}
	}
	if len(candidates) > 0 {
		return candidates[0]
	}
	return filepath.Join("/tmp", fmt.Sprint(os.Getuid()), "broccoli-comms", "agent-tracker.sock")
}

func New(socketPath string) *Client {
	if socketPath == "" {
		socketPath = DefaultSocketPath()
	}
	return &Client{SocketPath: socketPath}
}

var logMutex sync.Mutex

func debugLog(format string, args ...interface{}) {
	logMutex.Lock()
	defer logMutex.Unlock()

	home, err := os.UserHomeDir()
	if err != nil {
		return
	}
	logDir := filepath.Join(home, ".cache", "broccoli-comms")
	if err := os.MkdirAll(logDir, 0755); err != nil {
		return
	}
	logPath := filepath.Join(logDir, "tui-debug.log")

	f, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()

	timestamp := time.Now().Format("2006-01-02 15:04:05.000000")
	msg := fmt.Sprintf(format, args...)
	_, _ = fmt.Fprintf(f, "[%s] %s\n", timestamp, msg)
}

func (c *Client) call(ctx context.Context, method string, params any, timeout time.Duration, out any) error {
	debugLog("Client.call start: method=%s", method)
	if c.SocketPath == "" {
		debugLog("Client.call error: socket path is empty")
		return errors.New("tracker socket path is empty")
	}
	dial := c.Dial
	if dial == nil {
		d := net.Dialer{}
		dial = d.DialContext
	}
	debugLog("Client.call: dialing unix socket %s", c.SocketPath)
	conn, err := dial(ctx, "unix", c.SocketPath)
	if err != nil {
		debugLog("Client.call dial error: %v", err)
		return err
	}
	debugLog("Client.call dial success: local=%s, remote=%s", conn.LocalAddr(), conn.RemoteAddr())
	defer func() {
		debugLog("Client.call: closing connection")
		conn.Close()
	}()
	deadline := time.Time{}
	if timeout > 0 {
		deadline = time.Now().Add(timeout)
	}
	if ctxDeadline, ok := ctx.Deadline(); ok && (deadline.IsZero() || ctxDeadline.Before(deadline)) {
		deadline = ctxDeadline
	}
	if !deadline.IsZero() {
		debugLog("Client.call: setting connection deadline to %v", deadline)
		_ = conn.SetDeadline(deadline)
	}
	stopCancelWatcher := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			debugLog("Client.call: context cancelled, forcing deadline now")
			_ = conn.SetDeadline(time.Now())
		case <-stopCancelWatcher:
		}
	}()
	defer close(stopCancelWatcher)

	reqPayload := rpcRequest{JSONRPC: "2.0", Method: method, Params: params, ID: 1}
	reqBytes, err := json.Marshal(reqPayload)
	if err == nil {
		debugLog("Client.call: writing request bytes (len=%d)", len(reqBytes))
	}

	if err := json.NewEncoder(conn).Encode(reqPayload); err != nil {
		debugLog("Client.call encode/write error: %v", err)
		return err
	}

	if unix, ok := conn.(*net.UnixConn); ok {
		debugLog("Client.call: calling CloseWrite()")
		err := unix.CloseWrite()
		debugLog("Client.call: CloseWrite() done, err=%v", err)
	}

	debugLog("Client.call: entering io.ReadAll()")
	respBytes, err := io.ReadAll(bufio.NewReader(conn))
	if err != nil {
		debugLog("Client.call read error: %v", err)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return err
	}
	debugLog("Client.call: bytes read (len=%d)", len(respBytes))

	debugLog("Client.call: unmarshaling JSON response")
	var resp rpcResponse
	if err := json.Unmarshal(respBytes, &resp); err != nil {
		debugLog("Client.call unmarshal error: %v", err)
		return err
	}
	if resp.Error != nil {
		debugLog("Client.call RPC error returned: code=%d, message=%s", resp.Error.Code, resp.Error.Message)
		rpcErr := &RPCError{Method: method, Code: resp.Error.Code, Message: resp.Error.Message}
		if len(resp.Error.Data) > 0 {
			var data RPCErrorData
			if err := json.Unmarshal(resp.Error.Data, &data); err == nil {
				rpcErr.Data = &data
			}
		}
		return rpcErr
	}
	if out == nil {
		debugLog("Client.call success (nil output)")
		return nil
	}
	err = json.Unmarshal(resp.Result, out)
	if err != nil {
		debugLog("Client.call unmarshal result error: %v", err)
	} else {
		debugLog("Client.call success")
	}
	return err
}

func (c *Client) EnsureMailbox(ctx context.Context, agentName string) (EnsureMailboxResult, error) {
	var result EnsureMailboxResult
	err := c.call(ctx, "ensure_mailbox", map[string]any{"agent_name": agentName, "preserve_pane": true}, 5*time.Second, &result)
	return result, err
}

func (c *Client) TrackerInfo(ctx context.Context) (TrackerInfo, error) {
	var result TrackerInfo
	err := c.call(ctx, "tracker_info", map[string]any{}, 5*time.Second, &result)
	return result, err
}

func (c *Client) List(ctx context.Context) (map[string]Agent, error) {
	return c.ListWithOptions(ctx, ListOptions{})
}

func (c *Client) ListWithOptions(ctx context.Context, opts ListOptions) (map[string]Agent, error) {
	agents := map[string]Agent{}
	params := map[string]any{}
	if opts.IncludeRemote {
		params["include_remote"] = true
	}
	if opts.AgentID != "" {
		params["agent_id"] = opts.AgentID
	} else if opts.AgentName != "" {
		params["agent_name"] = opts.AgentName
	}
	if err := c.call(ctx, "list", params, 5*time.Second, &agents); err != nil {
		return nil, err
	}
	for name, agent := range agents {
		agent.Name = name
		agents[name] = agent
	}
	return agents, nil
}

func (c *Client) WaitEvents(ctx context.Context, opts WaitOptions) (WaitEventsResult, error) {
	if opts.Timeout == 0 {
		opts.Timeout = 25 * time.Second
	}
	params := map[string]any{"since": opts.Since, "timeout": opts.Timeout.Seconds()}
	if opts.TargetAgentID != "" {
		params["target_agent_id"] = opts.TargetAgentID
	}
	if opts.TargetAgentName != "" {
		params["target_agent_name"] = opts.TargetAgentName
	}
	var result WaitEventsResult
	err := c.call(ctx, "wait_events", params, opts.Timeout+5*time.Second, &result)
	return result, err
}

func (c *Client) ReadInbox(ctx context.Context, agentName string, last int, clear bool) (ReadInboxResult, error) {
	return c.ReadInboxForSender(ctx, agentName, last, clear, "", "", "")
}

func (c *Client) ReadInboxForSender(ctx context.Context, agentName string, last int, clear bool, senderAgentID, senderTrackerID, senderName string) (ReadInboxResult, error) {
	params := map[string]any{"agent_name": agentName, "clear": clear}
	if last > 0 {
		params["last_n"] = last
	}
	if senderAgentID != "" {
		params["sender_agent_id"] = senderAgentID
	}
	if senderTrackerID != "" {
		params["sender_tracker_id"] = senderTrackerID
	}
	if senderName != "" {
		params["sender_name"] = senderName
	}
	var result ReadInboxResult
	err := c.call(ctx, "get_inbox", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) GetUnreadCounts(ctx context.Context, agentName string) (UnreadCountsResult, error) {
	var result UnreadCountsResult
	err := c.call(ctx, "get_unread_counts", map[string]any{"agent_name": agentName}, 5*time.Second, &result)
	if result.Counts == nil {
		result.Counts = map[string]int{}
	}
	return result, err
}

func (c *Client) SendMessage(ctx context.Context, target, body string, attachments []Attachment) error {
	return c.SendMessageFrom(ctx, "", target, body, attachments)
}

func (c *Client) SendMessageFrom(ctx context.Context, senderName, target, body string, attachments []Attachment) error {
	return c.SendMessageWithID(ctx, senderName, target, body, "", attachments)
}

func (c *Client) SendMessageWithID(ctx context.Context, senderName, target, body, messageID string, attachments []Attachment) error {
	return c.SendMessageWithContext(ctx, senderName, target, body, messageID, "", attachments)
}

func (c *Client) SendMessageWithContext(ctx context.Context, senderName, target, body, messageID, swarmContext string, attachments []Attachment) error {
	params := map[string]any{"message": body}
	params["interrupt"] = true
	if senderName != "" {
		params["sender_name"] = senderName
	}
	if messageID != "" {
		params["message_id"] = messageID
	}
	if swarmContext != "" {
		params["swarm_context"] = swarmContext
	}
	for key, value := range messageTargetParams(target) {
		params[key] = value
	}
	if len(attachments) > 0 {
		params["attachments"] = attachments
	}
	return c.call(ctx, "send_message", params, 10*time.Second, nil)
}

func (c *Client) SendText(ctx context.Context, target, text string, submit bool) error {
	params := map[string]any{"input_type": "text", "text": text, "submit": submit}
	for key, value := range directInputTargetParams(target) {
		params[key] = value
	}
	return c.call(ctx, "send_input", params, 10*time.Second, nil)
}

func (c *Client) SendKeys(ctx context.Context, target string, keys []string) error {
	params := map[string]any{"input_type": "keys", "keys": keys}
	for key, value := range directInputTargetParams(target) {
		params[key] = value
	}
	return c.call(ctx, "send_input", params, 10*time.Second, nil)
}

func messageTargetParams(target string) map[string]any {
	if strings.Contains(target, "/") {
		return map[string]any{"target_address": target}
	}
	if isUUID(target) {
		return map[string]any{"target_address": "local/" + target}
	}
	return map[string]any{"agent_name": target}
}

func directInputTargetParams(target string) map[string]any {
	if strings.Contains(target, "/") {
		return map[string]any{"target_address": target}
	}
	if isUUID(target) {
		return map[string]any{"agent_id": target}
	}
	return map[string]any{"agent_name": target}
}

func isUUID(value string) bool {
	if len(value) != 36 {
		return false
	}
	for i, r := range value {
		switch i {
		case 8, 13, 18, 23:
			if r != '-' {
				return false
			}
		default:
			if !(r >= '0' && r <= '9') && !(r >= 'a' && r <= 'f') && !(r >= 'A' && r <= 'F') {
				return false
			}
		}
	}
	return true
}

func (c *Client) ListTrackers(ctx context.Context) ([]RemoteTracker, error) {
	var trackers []RemoteTracker
	if err := c.call(ctx, "list_trackers", map[string]any{}, 10*time.Second, &trackers); err != nil {
		return nil, err
	}
	return trackers, nil
}

func (c *Client) PublishTrackerEvent(ctx context.Context, targetTrackerID, eventType string, payload any) error {
	params := map[string]any{
		"target_tracker_id": targetTrackerID,
		"event_type":        eventType,
		"payload":           payload,
	}
	return c.call(ctx, "publish_tracker_event", params, 10*time.Second, nil)
}

func (c *Client) ListSwarms(ctx context.Context) (ListSwarmsResult, error) {
	var result ListSwarmsResult
	err := c.call(ctx, "list_swarms", map[string]any{}, 5*time.Second, &result)
	return result, err
}

func (c *Client) GetSwarmTimeline(ctx context.Context, swarmName string, lastN int) (SwarmTimelineResult, error) {
	params := map[string]any{"swarm": swarmName}
	if lastN > 0 {
		params["last_n"] = lastN
	}
	var result SwarmTimelineResult
	err := c.call(ctx, "get_swarm_timeline", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) AssignSwarm(ctx context.Context, swarmName, main string, subagents []string) (AssignSwarmResult, error) {
	params := map[string]any{"swarm": swarmName, "main": main, "subagents": subagents}
	var result AssignSwarmResult
	err := c.call(ctx, "assign_live_swarm", params, 10*time.Second, &result)
	return result, err
}

func (c *Client) MemoryShow(ctx context.Context, memoryID string, version int) (MemoryRecord, error) {
	params := map[string]any{"memory_id": memoryID}
	if version > 0 {
		params["version"] = version
	}
	var result MemoryRecord
	err := c.call(ctx, "memory.show", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryList(ctx context.Context, params map[string]any) ([]MemoryRecord, error) {
	var result []MemoryRecord
	err := c.call(ctx, "memory.list", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryPropose(ctx context.Context, params map[string]any) (MemoryResult, error) {
	var result MemoryResult
	err := c.call(ctx, "memory.propose", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryProposeEdit(ctx context.Context, params map[string]any) (MemoryResult, error) {
	var result MemoryResult
	err := c.call(ctx, "memory.propose_edit", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryProposeArchive(ctx context.Context, params map[string]any) (MemoryResult, error) {
	var result MemoryResult
	err := c.call(ctx, "memory.propose_archive", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryApprove(ctx context.Context, memoryID string, version int, actor string) (MemoryResult, error) {
	params := map[string]any{
		"memory_id": memoryID,
		"version":   version,
	}
	if actor != "" {
		params["actor"] = actor
	}
	var result MemoryResult
	err := c.call(ctx, "memory.approve", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryReject(ctx context.Context, memoryID string, version int, reason string, actor string) (MemoryResult, error) {
	params := map[string]any{
		"memory_id": memoryID,
		"version":   version,
	}
	if reason != "" {
		params["reason"] = reason
	}
	if actor != "" {
		params["actor"] = actor
	}
	var result MemoryResult
	err := c.call(ctx, "memory.reject", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryEdit(ctx context.Context, params map[string]any) (MemoryResult, error) {
	var result MemoryResult
	err := c.call(ctx, "memory.edit", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryRollback(ctx context.Context, memoryID string, targetVersion, expectedVersion int, actor string) (MemoryResult, error) {
	params := map[string]any{
		"memory_id":        memoryID,
		"target_version":   targetVersion,
		"expected_version": expectedVersion,
	}
	if actor != "" {
		params["actor"] = actor
	}
	var result MemoryResult
	err := c.call(ctx, "memory.rollback", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) MemoryRevoke(ctx context.Context, memoryID string, reason string, expectedVersion int, actor string) (MemoryResult, error) {
	params := map[string]any{
		"memory_id":        memoryID,
		"expected_version": expectedVersion,
	}
	if reason != "" {
		params["reason"] = reason
	}
	if actor != "" {
		params["actor"] = actor
	}
	var result MemoryResult
	err := c.call(ctx, "memory.revoke", params, 5*time.Second, &result)
	return result, err
}

func (c *Client) RequestStop(ctx context.Context, target string, timeout string, force bool) (bool, error) {
	params := map[string]any{
		"target_address": target,
		"timeout":        timeout,
		"force":          force,
	}
	var result bool
	err := c.call(ctx, "request_stop", params, 10*time.Second, &result)
	return result, err
}
