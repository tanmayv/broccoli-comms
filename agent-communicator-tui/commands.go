package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/config"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

type localClient interface {
	EnsureMailbox(context.Context, string) (tracker.EnsureMailboxResult, error)
	TrackerInfo(context.Context) (tracker.TrackerInfo, error)
	List(context.Context) (map[string]tracker.Agent, error)
	ListWithOptions(context.Context, tracker.ListOptions) (map[string]tracker.Agent, error)
	ReadInbox(context.Context, string, int, bool) (tracker.ReadInboxResult, error)
	ReadInboxForSender(context.Context, string, int, bool, string, string, string) (tracker.ReadInboxResult, error)
	GetUnreadCounts(context.Context, string) (tracker.UnreadCountsResult, error)
	SendMessage(context.Context, string, string, []tracker.Attachment) error
	SendMessageFrom(context.Context, string, string, string, []tracker.Attachment) error
	SendText(context.Context, string, string, bool) error
	SendKeys(context.Context, string, []string) error
	WaitEvents(context.Context, tracker.WaitOptions) (tracker.WaitEventsResult, error)
	ListTrackers(context.Context) ([]tracker.RemoteTracker, error)
	PublishTrackerEvent(ctx context.Context, targetTrackerID, eventType string, payload any) error
	ListSwarms(context.Context) (tracker.ListSwarmsResult, error)
	GetSwarmTimeline(context.Context, string, int) (tracker.SwarmTimelineResult, error)
	AssignSwarm(context.Context, string, string, []string) (tracker.AssignSwarmResult, error)
	MemoryShow(ctx context.Context, memoryID string, version int) (tracker.MemoryRecord, error)
	MemoryList(ctx context.Context, params map[string]any) ([]tracker.MemoryRecord, error)
	MemoryPropose(ctx context.Context, params map[string]any) (tracker.MemoryResult, error)
	MemoryProposeEdit(ctx context.Context, params map[string]any) (tracker.MemoryResult, error)
	MemoryProposeArchive(ctx context.Context, params map[string]any) (tracker.MemoryResult, error)
	MemoryApprove(ctx context.Context, memoryID string, version int, actor string) (tracker.MemoryResult, error)
	MemoryReject(ctx context.Context, memoryID string, version int, reason string, actor string) (tracker.MemoryResult, error)
	MemoryEdit(ctx context.Context, params map[string]any) (tracker.MemoryResult, error)
	MemoryRollback(ctx context.Context, memoryID string, targetVersion, expectedVersion int, actor string) (tracker.MemoryResult, error)
	MemoryRevoke(ctx context.Context, memoryID string, reason string, expectedVersion int, actor string) (tracker.MemoryResult, error)
}

type messageIDSender interface {
	SendMessageWithID(context.Context, string, string, string, string, []tracker.Attachment) error
}

type messageContextSender interface {
	SendMessageWithContext(context.Context, string, string, string, string, string, []tracker.Attachment) error
}

type agentRow struct {
	Name                string
	Scope               string
	Status              string
	CWD                 string
	TargetAddress       string
	Configured          *bool
	Running             *bool
	Launchable          *bool
	Role                string
	Hostname            string
	AgentName           string
	TmuxPane            string
	AgentCmd            string
	AgentType           string
	CurrentTask         string
	CurrentTaskID       string
	CurrentTaskStatus   string
	CurrentTaskNextStep string
	AgentID             string
	TrackerID           string
	RegistryName        string
	ModelType           string
	Detection           tracker.DetectionStatus
}
type mailboxEnsured struct{ Err error }

type agentsLoaded struct {
	Rows []agentRow
	Err  error
}
type healthLoaded struct {
	Info tracker.TrackerInfo
	Err  error
}
type inboxLoaded struct {
	Messages []tracker.Message
	Err      error
}
type allInboxLoaded struct {
	Messages []tracker.Message
	Err      error
}
type swarmsLoaded struct {
	Rows []swarmRow
	Err  error
}
type swarmTimelineLoaded struct {
	Swarm    string
	Messages []tracker.SwarmTimelineMessage
	Err      error
}
type swarmAssigned struct {
	Result tracker.AssignSwarmResult
	Err    error
}
type unreadCountsLoaded struct {
	Counts map[string]int
	Err    error
}
type messageSent struct {
	Body   string
	Row    agentRow
	Record outboxRecord
	Err    error
}
type directInputSent struct {
	Original string
	Row      agentRow
	Mode     string
	Err      error
}
type eventsLoaded struct {
	Result tracker.WaitEventsResult
	Err    error
}
type refreshTick struct{}
type retryEvents struct{}
type agentListSpinnerTick struct{}
type cursorBlinkTick struct{}
type clearDirectInputStatusTick struct{}

type promptTemplate struct {
	Name string
	Path string
}

type promptsLoaded struct {
	Prompts []promptTemplate
	Err     error
}

type promptEdited struct {
	Body  string
	Saved bool
	Err   error
}

func promptDirectory() string {
	if xdg := os.Getenv("XDG_CONFIG_HOME"); xdg != "" {
		return filepath.Join(xdg, "agent-communicator", "prompts")
	}
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return filepath.Join(".", "prompts")
	}
	return filepath.Join(home, ".config", "agent-communicator", "prompts")
}

func loadPromptsCmd() tea.Cmd {
	return func() tea.Msg {
		prompts, err := loadPromptTemplates(promptDirectory())
		return promptsLoaded{Prompts: prompts, Err: err}
	}
}

func loadPromptTemplates(dir string) ([]promptTemplate, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	prompts := []promptTemplate{}
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".md" {
			continue
		}
		name := strings.TrimSuffix(entry.Name(), ".md")
		prompts = append(prompts, promptTemplate{Name: name, Path: filepath.Join(dir, entry.Name())})
	}
	sort.Slice(prompts, func(i, j int) bool { return prompts[i].Name < prompts[j].Name })
	return prompts, nil
}

func ensureMailboxCmd(local localClient, ownName string) tea.Cmd {
	return func() tea.Msg {
		if local == nil || strings.TrimSpace(ownName) == "" {
			return mailboxEnsured{}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_, err := local.EnsureMailbox(ctx, ownName)
		return mailboxEnsured{Err: err}
	}
}

func loadInbox(local localClient, inboxOwner string, row agentRow) tea.Cmd {
	return func() tea.Msg {
		if local == nil || row.Name == "" {
			return inboxLoaded{}
		}
		owner := inboxOwner
		if owner == "" && row.Scope == "local" {
			owner = row.Name
		}
		if owner == "" {
			return inboxLoaded{}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		senderAgentID, senderTrackerID, senderName := rowInboxFilters(row)
		inbox, err := local.ReadInboxForSender(ctx, owner, simpleInboxFetchLimit, false, senderAgentID, senderTrackerID, senderName)
		return inboxLoaded{Messages: filterConversation(inbox.Messages, row), Err: err}
	}
}

func loadAllInbox(local localClient, inboxOwner string) tea.Cmd {
	return func() tea.Msg {
		if local == nil || inboxOwner == "" {
			return allInboxLoaded{}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		inbox, err := local.ReadInbox(ctx, inboxOwner, advancedInboxFetchLimit, false)
		return allInboxLoaded{Messages: inbox.Messages, Err: err}
	}
}

func loadUnreadCounts(local localClient, inboxOwner string) tea.Cmd {
	return func() tea.Msg {
		if local == nil || strings.TrimSpace(inboxOwner) == "" {
			return unreadCountsLoaded{}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		result, err := local.GetUnreadCounts(ctx, inboxOwner)
		return unreadCountsLoaded{Counts: result.Counts, Err: err}
	}
}

func rowInboxFilters(row agentRow) (senderAgentID, senderTrackerID, senderName string) {
	if row.AgentID != "" {
		senderAgentID = row.AgentID
		senderTrackerID = row.TrackerID
		return senderAgentID, senderTrackerID, ""
	}
	if row.Scope == "remote" {
		return "", "", ""
	}
	return "", "", fallback(row.AgentName, row.Name)
}

func filterConversation(messages []tracker.Message, row agentRow) []tracker.Message {
	if row.Name == "" {
		return messages
	}
	filtered := []tracker.Message{}
	for _, msg := range messages {
		if taskUpdateMatchesRow(msg, row) || messageMatchesRow(msg, row) {
			filtered = append(filtered, msg)
		}
	}
	return filtered
}

func taskUpdateMatchesRow(msg tracker.Message, row agentRow) bool {
	if !isTaskUpdateMessage(msg) {
		return false
	}
	if row.CurrentTaskID != "" && msg.TaskID != "" && row.CurrentTaskID == msg.TaskID {
		return true
	}
	assigned := strings.TrimSpace(msg.TaskAssignedAgent)
	if assigned == "" {
		return false
	}
	if row.Scope == "remote" {
		if !strings.Contains(assigned, "/") {
			return false
		}
		return assigned == strings.TrimSpace(rowTarget(row)) || (row.Hostname != "" && row.AgentName != "" && assigned == row.Hostname+"/"+row.AgentName)
	}
	if strings.Contains(assigned, "/") {
		return false
	}
	for _, name := range []string{row.Name, row.AgentName, rowTarget(row)} {
		if strings.TrimSpace(name) == assigned {
			return true
		}
	}
	return false
}

func senderMatchesRow(sender string, row agentRow) bool {
	if sender == row.Name || strings.HasPrefix(sender, row.Name+" ") {
		return true
	}
	if row.Scope == "remote" {
		agentName, hostname := row.AgentName, row.Hostname
		address := rowTarget(row)
		if agentName == "" && strings.Contains(address, "/") {
			parts := strings.SplitN(address, "/", 2)
			hostname, agentName = parts[0], parts[1]
		}
		return agentName != "" && hostname != "" && strings.HasPrefix(sender, agentName+" ") && strings.Contains(sender, "(via "+hostname+")")
	}
	return false
}

func filterOwnAgent(rows []agentRow, ownName string) []agentRow {
	if ownName == "" {
		return rows
	}
	filtered := rows[:0]
	for _, row := range rows {
		if row.Name != ownName {
			filtered = append(filtered, row)
		}
	}
	return filtered
}

func deletePreviousWord(value []rune) []rune {
	end := len(value)
	for end > 0 && value[end-1] == ' ' {
		end--
	}
	start := end
	for start > 0 && value[start-1] != ' ' {
		start--
	}
	return value[:start]
}

const markdownReplyInstruction = "PS: Reply in markdown format."

type composerAction struct {
	Kind       string
	Body       string
	Text       string
	Submit     bool
	Keys       []string
	ApprovalID string
	MemoryID   string
	Result     string
	Title      string
	SwarmName  string
	MainAgent  string
	Subagents  []string
	Original   string
}

func parseComposerAction(input string) composerAction {
	trimmed := strings.TrimSpace(input)
	action := composerAction{Kind: "message", Body: input, Submit: true, Original: input}
	if trimmed == "" {
		return action
	}
	if trimmed == "/msg" {
		action.Body = ""
		return action
	}
	if strings.HasPrefix(trimmed, "/msg ") {
		action.Body = strings.TrimSpace(strings.TrimPrefix(trimmed, "/msg"))
		return action
	}
	if trimmed == "/text" {
		return composerAction{Kind: "direct_text", Submit: true, Original: input}
	}
	if strings.HasPrefix(trimmed, "/text ") {
		rest := strings.TrimSpace(strings.TrimPrefix(trimmed, "/text"))
		submit := true
		if rest == "--no-submit" {
			rest = ""
			submit = false
		} else if strings.HasPrefix(rest, "--no-submit ") {
			rest = strings.TrimSpace(strings.TrimPrefix(rest, "--no-submit"))
			submit = false
		}
		return composerAction{Kind: "direct_text", Text: rest, Submit: submit, Original: input}
	}
	if trimmed == "/key" || trimmed == "/keys" {
		return composerAction{Kind: "direct_keys", Original: input}
	}
	if strings.HasPrefix(trimmed, "/key ") {
		rest := strings.TrimSpace(strings.TrimPrefix(trimmed, "/key"))
		return composerAction{Kind: "direct_keys", Keys: strings.Fields(rest), Original: input}
	}
	if strings.HasPrefix(trimmed, "/keys ") {
		rest := strings.TrimSpace(strings.TrimPrefix(trimmed, "/keys"))
		return composerAction{Kind: "direct_keys", Keys: strings.Fields(rest), Original: input}
	}
	if trimmed == "/approval" {
		return composerAction{Kind: "approval_review", Original: input}
	}
	if strings.HasPrefix(trimmed, "/approval ") {
		fields := strings.Fields(strings.TrimSpace(strings.TrimPrefix(trimmed, "/approval")))
		action := composerAction{Kind: "approval_review", Original: input}
		if len(fields) > 0 {
			action.Result = normalizeApprovalReviewResult(fields[0])
		}
		if len(fields) > 1 {
			action.ApprovalID = fields[1]
		}
		return action
	}
	if trimmed == "/reject" {
		return composerAction{Kind: "approval_review", Result: "bad", Original: input}
	}
	if strings.HasPrefix(trimmed, "/reject ") {
		return composerAction{Kind: "approval_review", Result: "bad", ApprovalID: firstField(strings.TrimSpace(strings.TrimPrefix(trimmed, "/reject"))), Original: input}
	}
	if trimmed == "/needs" {
		return composerAction{Kind: "approval_review", Result: "need_improvements", Original: input}
	}
	if strings.HasPrefix(trimmed, "/needs ") {
		return composerAction{Kind: "approval_review", Result: "need_improvements", ApprovalID: firstField(strings.TrimSpace(strings.TrimPrefix(trimmed, "/needs"))), Original: input}
	}
	if trimmed == "/memory" {
		return composerAction{Kind: "memory_action", Original: input}
	}
	if strings.HasPrefix(trimmed, "/memory ") {
		fields := strings.Fields(strings.TrimSpace(strings.TrimPrefix(trimmed, "/memory")))
		action := composerAction{Kind: "memory_action", Original: input}
		if len(fields) > 0 {
			action.Result = fields[0]
		}
		if len(fields) > 1 {
			action.MemoryID = fields[1]
		}
		if action.Result == "edit" && len(fields) > 2 {
			rest := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(strings.TrimPrefix(trimmed, "/memory")), strings.Join(fields[:2], " ")))
			parts := strings.SplitN(rest, "|", 2)
			action.Title = strings.TrimSpace(parts[0])
			if len(parts) > 1 {
				action.Body = strings.TrimSpace(parts[1])
			}
		}
		return action
	}
	if trimmed == "/swarm" || trimmed == "/swarm create" {
		return composerAction{Kind: "swarm_create", Original: input}
	}
	if strings.HasPrefix(trimmed, "/swarm create ") {
		fields := strings.Fields(strings.TrimSpace(strings.TrimPrefix(trimmed, "/swarm create")))
		action := composerAction{Kind: "swarm_create", Original: input}
		if len(fields) > 0 {
			action.SwarmName = fields[0]
		}
		for i := 1; i < len(fields); i++ {
			switch fields[i] {
			case "--main":
				if i+1 < len(fields) {
					action.MainAgent = fields[i+1]
					i++
				}
			case "--subagent", "--sub":
				if i+1 < len(fields) {
					action.Subagents = append(action.Subagents, fields[i+1])
					i++
				}
			}
		}
		return action
	}
	if trimmed == "/broadcast" {
		return composerAction{Kind: "broadcast", Original: input}
	}
	if strings.HasPrefix(trimmed, "/broadcast ") {
		rest := strings.TrimSpace(strings.TrimPrefix(trimmed, "/broadcast"))
		return composerAction{Kind: "broadcast", Body: rest, Original: input}
	}
	return action
}

func firstField(value string) string {
	fields := strings.Fields(value)
	if len(fields) == 0 {
		return ""
	}
	return fields[0]
}

func normalizeApprovalReviewResult(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "good", "approve", "approved":
		return "good"
	case "bad", "reject", "rejected":
		return "bad"
	case "need_improvements", "needs", "improvements":
		return "need_improvements"
	default:
		return ""
	}
}

func sendCurrentMessage(local localClient, senderName string, row agentRow, body string) tea.Cmd {
	return sendOutboxRecord(local, senderName, row, makeOutboxRecord(senderName, row, body))
}

func assignSwarmCmd(local localClient, swarmName, main string, subagents []string) tea.Cmd {
	return func() tea.Msg {
		if local == nil {
			return swarmAssigned{Err: errors.New("local tracker client unavailable")}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		result, err := local.AssignSwarm(ctx, swarmName, main, subagents)
		return swarmAssigned{Result: result, Err: err}
	}
}

func sendOutboxRecord(local localClient, senderName string, row agentRow, record outboxRecord) tea.Cmd {
	return func() tea.Msg {
		if local == nil {
			return messageSent{Body: record.Body, Row: row, Record: record, Err: errors.New("local tracker client unavailable")}
		}
		target := rowTarget(row)
		if strings.TrimSpace(record.Body) == "" || target == "" {
			return messageSent{Body: record.Body, Row: row, Record: record}
		}
		deliveryBody := messageBodyForDelivery(record.Body)
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		var err error
		if record.SwarmContext != "" {
			if withContext, ok := local.(messageContextSender); ok {
				err = withContext.SendMessageWithContext(ctx, senderName, target, deliveryBody, record.ID, record.SwarmContext, nil)
			} else if withID, ok := local.(messageIDSender); ok {
				err = withID.SendMessageWithID(ctx, senderName, target, deliveryBody, record.ID, nil)
			} else if senderName != "" {
				err = local.SendMessageFrom(ctx, senderName, target, deliveryBody, nil)
			} else {
				err = local.SendMessage(ctx, target, deliveryBody, nil)
			}
		} else if withID, ok := local.(messageIDSender); ok {
			err = withID.SendMessageWithID(ctx, senderName, target, deliveryBody, record.ID, nil)
		} else if senderName != "" {
			err = local.SendMessageFrom(ctx, senderName, target, deliveryBody, nil)
		} else {
			err = local.SendMessage(ctx, target, deliveryBody, nil)
		}
		if err == nil {
			err = appendOutbox(record)
		}
		return messageSent{Body: record.Body, Row: row, Record: record, Err: err}
	}
}

func sendDirectInput(local localClient, row agentRow, action composerAction, allowRemote bool) tea.Cmd {
	return func() tea.Msg {
		if rowDisallowsDirectInput(row) {
			return directInputSent{Original: action.Original, Row: row, Mode: action.Kind, Err: errors.New("direct pane input to Broccoli Comms UI is disabled")}
		}
		if local == nil {
			return directInputSent{Original: action.Original, Row: row, Mode: action.Kind, Err: errors.New("local tracker client unavailable")}
		}
		if row.Scope == "remote" && !allowRemote {
			return directInputSent{Original: action.Original, Row: row, Mode: action.Kind, Err: errors.New("remote direct pane input is disabled")}
		}
		target := rowTarget(row)
		if target == "" {
			return directInputSent{Original: action.Original, Row: row, Mode: action.Kind, Err: errors.New("target agent unavailable")}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		var err error
		switch action.Kind {
		case "direct_text":
			if action.Text == "" {
				err = errors.New("/text requires TEXT")
			} else {
				err = local.SendText(ctx, target, action.Text, action.Submit)
			}
		case "direct_keys":
			if len(action.Keys) == 0 {
				err = errors.New("/key requires KEY [KEY...]")
			} else {
				err = local.SendKeys(ctx, target, action.Keys)
			}
		default:
			err = errors.New("unknown direct input command")
		}
		return directInputSent{Original: action.Original, Row: row, Mode: action.Kind, Err: err}
	}
}

func rowDisallowsDirectInput(row agentRow) bool {
	agentName := strings.TrimSpace(row.AgentName)
	if agentName == "" {
		agentName = strings.TrimSpace(row.Name)
		if strings.Contains(agentName, "/") {
			parts := strings.Split(agentName, "/")
			agentName = parts[len(parts)-1]
		}
	}
	return row.AgentType == "agent-communicator-ui" || agentName == "agent-communicator" || strings.Contains(row.AgentCmd, "agent-communicator")
}

func messageBodyForDelivery(body string) string {
	return strings.TrimRight(body, " \t\r\n") + "\n\n(" + markdownReplyInstruction + ")"
}

func waitEvents(local localClient, since int64) tea.Cmd {
	return func() tea.Msg {
		if local == nil {
			return eventsLoaded{}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
		defer cancel()
		result, err := local.WaitEvents(ctx, tracker.WaitOptions{Since: since, Timeout: 25 * time.Second})
		return eventsLoaded{Result: result, Err: err}
	}
}

func shouldReloadForEvents(ownName string, row agentRow, result tracker.WaitEventsResult) bool {
	if row.Name == "" {
		return false
	}
	if ownName == "" && row.Scope != "local" {
		return false
	}
	if result.Reset || result.Gap {
		return true
	}
	targetName := row.Name
	if ownName != "" {
		targetName = ownName
	}
	for _, event := range result.Events {
		if event.TargetAgentName == targetName {
			return true
		}
	}
	return false
}

func tickRefresh() tea.Cmd {
	return tea.Tick(refreshInterval, func(time.Time) tea.Msg { return refreshTick{} })
}
func tickAgentListSpinner() tea.Cmd {
	return tea.Tick(150*time.Millisecond, func(time.Time) tea.Msg { return agentListSpinnerTick{} })
}
func tickCursorBlink() tea.Cmd {
	return tea.Tick(550*time.Millisecond, func(time.Time) tea.Msg { return cursorBlinkTick{} })
}
func retryWaitEvents() tea.Cmd {
	return tea.Tick(2*time.Second, func(time.Time) tea.Msg { return retryEvents{} })
}
func rowTarget(row agentRow) string {
	if row.TargetAddress != "" {
		return row.TargetAddress
	}
	return row.Name
}
func shortHost(hostname string) string {
	if len([]rune(hostname)) <= 5 {
		return hostname
	}
	return string([]rune(hostname)[:5])
}
func fallback(v, d string) string {
	if v == "" {
		return d
	}
	return v
}

type agentConfigSpun struct {
	Name string
	Err  error
}

func runNewAgentArgs(name, host, provider string, optionalArgs ...string) ([]string, error) {
	if strings.TrimSpace(provider) == "" {
		return nil, fmt.Errorf("no configured provider found in config.toml")
	}
	args := []string{"run"}
	if strings.TrimSpace(host) != "" && host != localHostname() {
		args = append(args, "--host", host)
	}
	args = append(args, "--json", name, "--", provider)
	for _, raw := range optionalArgs {
		args = append(args, strings.Fields(raw)...)
	}
	return args, nil
}

func runNewAgentCmd(name, host, provider string, optionalArgs ...string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		args, err := runNewAgentArgs(name, host, provider, optionalArgs...)
		if err != nil {
			return agentConfigSpun{Name: name, Err: err}
		}
		cmd := broccoliCommsCommandContext(ctx, args...)
		out, err := cmd.CombinedOutput()
		if err != nil {
			return agentConfigSpun{Name: name, Err: fmt.Errorf("%s: %s", err, strings.TrimSpace(string(out)))}
		}
		return agentConfigSpun{Name: name}
	}
}

func runConfiguredAgentCmd(name string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		cmd := broccoliCommsCommandContext(ctx, "run", name, "--json")
		out, err := cmd.CombinedOutput()
		if err != nil {
			return agentConfigSpun{Name: name, Err: fmt.Errorf("%s: %s", err, strings.TrimSpace(string(out)))}
		}
		return agentConfigSpun{Name: name}
	}
}

func immutableCopyName(item ConfigSelectionItem) string {
	base := item.Name
	if item.TargetAddress != "" {
		base = item.TargetAddress
	}
	base = strings.Trim(strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '.' || r == '_' || r == '-' {
			return r
		}
		return '-'
	}, base), "-._")
	if base == "" {
		base = "agent"
	}
	return "copy-" + base
}

func copyAgentImmutableCmd(item ConfigSelectionItem) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		newName := immutableCopyName(item)
		source := fallback(item.TargetAddress, item.Name)
		cmd := broccoliCommsCommandContext(ctx, "agent", "copy", source, newName, "--immutable", "--json")
		out, err := cmd.CombinedOutput()
		if err != nil {
			return agentConfigSpun{Name: newName, Err: fmt.Errorf("%s: %s", err, strings.TrimSpace(string(out)))}
		}
		return agentConfigSpun{Name: newName}
	}
}

type ConfigSelectionItem struct {
	Name          string
	Description   string
	IsRemote      bool
	IsNewAgent    bool
	TrackerID     string
	Hostname      string
	TargetAddress string
	Configured    bool
	Running       bool
	Launchable    bool
	Copyable      bool
	Command       string
	Source        string
}

type configItemsLoaded struct {
	Items []ConfigSelectionItem
	Err   error
}

type broccoliAgentListPayload struct {
	Agents map[string]broccoliAgentListRow `json:"agents"`
}

type broccoliAgentListRow struct {
	Name                string `json:"name"`
	Status              string `json:"status"`
	ScopeKind           string `json:"scope_kind"`
	Remote              bool   `json:"remote"`
	IsConfigured        bool   `json:"is_configured"`
	Running             bool   `json:"running"`
	Launchable          bool   `json:"launchable"`
	Copyable            bool   `json:"copyable"`
	TargetAddress       string `json:"target_address"`
	Hostname            string `json:"hostname"`
	TrackerID           string `json:"tracker_id"`
	RegistryName        string `json:"registry_name"`
	Command             string `json:"command"`
	CWD                 string `json:"cwd"`
	CurrentTask         string `json:"current_task"`
	CurrentTaskID       string `json:"current_task_id"`
	CurrentTaskStatus   string `json:"current_task_status"`
	CurrentTaskNextStep string `json:"current_task_next_step"`
}

func loadConfigItemsFromBroccoliComms(ctx context.Context) ([]ConfigSelectionItem, error) {
	cmd := broccoliCommsCommandContext(ctx, "agent", "list", "--include-remote", "--json")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("%s: %s", err, strings.TrimSpace(string(out)))
	}
	var payload broccoliAgentListPayload
	if err := json.Unmarshal(out, &payload); err != nil {
		return nil, err
	}
	items := make([]ConfigSelectionItem, 0, len(payload.Agents))
	for key, row := range payload.Agents {
		name := fallback(row.Name, key)
		descParts := []string{}
		if row.IsConfigured {
			descParts = append(descParts, "configured")
		}
		if row.Running {
			descParts = append(descParts, "running")
		}
		if row.Remote {
			descParts = append(descParts, "remote")
		}
		if row.Command != "" {
			descParts = append(descParts, row.Command)
		}
		description := strings.Join(descParts, " · ")
		if description == "" {
			description = row.Status
		}
		items = append(items, ConfigSelectionItem{
			Name:          name,
			Description:   description,
			IsRemote:      row.Remote,
			TrackerID:     row.TrackerID,
			Hostname:      row.Hostname,
			TargetAddress: fallback(row.TargetAddress, key),
			Configured:    row.IsConfigured,
			Running:       row.Running,
			Launchable:    row.Launchable,
			Copyable:      row.Copyable,
			Command:       row.Command,
			Source:        "broccoli-comms",
		})
	}
	hosts := map[string]bool{localHostname(): true}
	for _, item := range items {
		if item.Hostname != "" {
			hosts[item.Hostname] = true
		}
	}
	var hostNames []string
	for host := range hosts {
		hostNames = append(hostNames, host)
	}
	sort.Strings(hostNames)
	provider := config.FirstProviderName()
	for _, host := range hostNames {
		description := "new agent name only"
		if provider != "" {
			description += " · provider " + provider
		} else {
			description += " · no provider configured"
		}
		items = append(items, ConfigSelectionItem{
			Name:        "Run new agent on " + host,
			Description: description,
			IsRemote:    host != localHostname(),
			IsNewAgent:  true,
			Hostname:    host,
			Launchable:  provider != "",
			Source:      "new-agent",
		})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].IsRemote != items[j].IsRemote {
			return !items[i].IsRemote
		}
		return items[i].Name < items[j].Name
	})
	return items, nil
}

func loadConfigItemsCmd(local localClient) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		items, err := loadConfigItemsFromBroccoliComms(ctx)
		if err == nil {
			return configItemsLoaded{Items: items}
		}

		var fallbackItems []ConfigSelectionItem
		localConfigs, localKeys, localErr := LoadAgentConfigs()
		if localErr == nil {
			for _, key := range localKeys {
				cfg := localConfigs[key]
				fallbackItems = append(fallbackItems, ConfigSelectionItem{Name: cfg.Name, Description: cfg.Description, IsRemote: false, Configured: true, Launchable: true, Copyable: true, Source: "legacy"})
			}
		}
		if len(fallbackItems) > 0 {
			return configItemsLoaded{Items: fallbackItems, Err: nil}
		}
		return configItemsLoaded{Items: nil, Err: err}
	}
}

func (m *model) openRunAgentForm(item ConfigSelectionItem) {
	m.showingConfigMenu = false
	m.showingRunAgentForm = true
	m.runAgentHost = fallback(item.Hostname, localHostname())
	m.runAgentProviders = providerNamesForHost(m.configItems, m.runAgentHost)
	m.runAgentProvider = firstProviderForForm(m.runAgentProviders)
	m.runAgentName = nil
	m.runAgentArgs = nil
	m.runAgentField = 0
	m.runAgentSuggestions = agentNameSuggestionsForHost(m.configItems, m.runAgentHost)
}

func providerNamesForHost(items []ConfigSelectionItem, host string) []string {
	configured := config.ProviderNames()
	seen := map[string]bool{}
	var hostProviders []string
	for _, item := range items {
		itemHost := fallback(item.Hostname, localHostname())
		if itemHost != host {
			continue
		}
		provider := strings.Fields(item.Command)
		if len(provider) == 0 {
			continue
		}
		name := provider[0]
		if !seen[name] {
			seen[name] = true
			hostProviders = append(hostProviders, name)
		}
	}
	if len(hostProviders) == 0 || host == localHostname() {
		for _, name := range configured {
			if !seen[name] {
				seen[name] = true
				hostProviders = append(hostProviders, name)
			}
		}
	}
	sort.Strings(hostProviders)
	return hostProviders
}

func firstProviderForForm(providers []string) string {
	if len(providers) == 0 {
		return ""
	}
	return providers[0]
}

func agentNameSuggestionsForHost(items []ConfigSelectionItem, host string) []string {
	seen := map[string]bool{}
	var names []string
	for _, item := range items {
		if item.IsNewAgent || item.Name == "" {
			continue
		}
		itemHost := fallback(item.Hostname, localHostname())
		if itemHost != host {
			continue
		}
		if seen[item.Name] {
			continue
		}
		seen[item.Name] = true
		names = append(names, item.Name)
	}
	sort.Strings(names)
	return names
}

func completeAgentName(prefix string, suggestions []string) string {
	prefix = strings.ToLower(strings.TrimSpace(prefix))
	if prefix == "" {
		if len(suggestions) > 0 {
			return suggestions[0]
		}
		return ""
	}
	for _, suggestion := range suggestions {
		if strings.HasPrefix(strings.ToLower(suggestion), prefix) {
			return suggestion
		}
	}
	return ""
}

func spinRemoteAgentCmd(local localClient, targetTrackerID, configName string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		// Remote runs use the canonical Broccoli Comms launch path.  The CLI
		// publishes remote_run_request and waits for remote_run_result; it does
		// not launch tmux directly from the TUI.
		cmd := broccoliCommsCommandContext(ctx, "run", "--host", targetTrackerID, configName, "--json")
		out, err := cmd.CombinedOutput()
		if err != nil {
			return agentConfigSpun{Name: configName, Err: fmt.Errorf("%s: %s", err, strings.TrimSpace(string(out)))}
		}
		return agentConfigSpun{Name: configName}
	}
}

type paneCaptured struct {
	Target string
	Err    error
}

type clearPaneCaptureStatusTick struct{}

func requestPaneCaptureCmd(targetAddress string) tea.Cmd {
	return func() tea.Msg {
		args := []string{"send-pane", "agent-communicator", "--source", targetAddress, "--last", "20", "--note", "Requested from agent-communicator"}
		cmd := broccoliAgentTrackerCommand(args...)
		out, err := cmd.CombinedOutput()
		if err != nil {
			return paneCaptured{Target: targetAddress, Err: fmt.Errorf("%s: %s", err, string(out))}
		}
		return paneCaptured{Target: targetAddress}
	}
}
