package main

import (
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/config"

	"os"
	"sort"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

type viewMode int

const (
	simpleView viewMode = iota
	advancedView
	savedView
	memoryView
	tasksView
	homeView
	changelogView
)

type runtimeInfo struct {
	AppRuntime               bool
	RuntimeDir               string
	TrackerSocket            string
	TmuxSocket               string
	RemoteDirectInputEnabled bool
}

type model struct {
	width, height, selected int
	agentOffset             int
	mode                    viewMode
	rows                    []agentRow
	allRows                 []agentRow
	showSystemAgents        bool
	messages                []tracker.Message
	allMessages             []tracker.Message
	outbox                  []outboxRecord
	savedMessages           []savedMessageRecord
	savedSelected           int
	sentMessages            map[string][]tracker.Message
	unreadRows              map[string]bool
	unreadCounts            map[string]int
	hiddenAgents            map[string]bool
	agentSection            agentSection
	autoHiddenApplied       bool
	messageOffset           int
	messageSelected         int
	messageFocused          bool
	agentListStale          bool
	agentListLoading        bool
	agentListFrame          int
	composer                []rune
	inputMode               inputMode
	cursorHidden            bool
	err                     error
	eventSeq                int64
	health                  tracker.TrackerInfo
	healthErr               error
	systemEvents            []tracker.Event
	ownName                 string
	local                   localClient
	runtime                 runtimeInfo

	// Custom Agent Configurations (Ctrl-L)
	configItems       []ConfigSelectionItem
	showingConfigMenu bool
	configSelected    int
	configQuery       []rune

	// Run new agent form
	showingRunAgentForm bool
	runAgentHost        string
	runAgentProvider    string
	runAgentProviders   []string
	runAgentName        []rune
	runAgentArgs        []rune
	runAgentField       int
	runAgentSuggestions []string
	runAgentIsExisting  bool   // True if overriding existing agent, False if new
	runAgentProfileName string // Short name for execution
	runAgentDefaultCWD  string // Default CWD to show as hint
	runAgentDefaultProv string // Default provider to detect overrides
	runAgentCWD         []rune // CWD override input

	// Prompt templates (Ctrl-O)
	prompts           []promptTemplate
	showingPromptMenu bool
	promptSelected    int

	// Command palette (Ctrl-P)
	commandPalette commandPaletteState

	// Memory management tab
	memoryItems         []memoryRecord
	memorySelected      int
	memoryOffset        int
	memoryLoading       bool
	memorySearchFocused bool
	memoryQuery         []rune
	memoryStatusFilter  string
	memoryTypeFilter    string
	memoryAgentFilter   string
	memoryForm          memoryFormState
	memoryConfirm       memoryActionConfirmation
	memoryErr           error

	// Task management tab
	tasksItems              []taskRecord
	tasksStates             []taskWorkingState
	tasksApprovals          []taskApprovalRecord
	tasksSelected           int
	tasksOffset             int
	tasksLoading            bool
	tasksErr                error
	tasksConfirm            taskActionConfirmation
	tasksForm               taskChainFormState
	tasksPalette            taskCommandPaletteState
	tasksChainFocused       bool
	tasksChainSelected      int
	tasksAgentFilterFocused bool
	tasksAgentFilter        []rune
	tasksAgentChip          int

	// Save Agent Form (Ctrl-S)
	showingSaveForm bool
	saveFormIndex   int // 0: Name, 1: Description, 2: Command, 3: CWD, 4: Save, 5: Cancel
	saveFormInputs  []textinput.Model

	// Short footer statuses
	paneCaptureStatus    string
	directInputStatus    string
	directInputStatusErr bool
	retryOperation       string
}

func runtimeInfoFromEnv() runtimeInfo {
	info := runtimeInfo{
		RuntimeDir:    firstNonEmpty(os.Getenv("BROCCOLI_COMMS_RUNTIME_DIR"), config.GetString("", "paths", "runtime_dir")),
		TrackerSocket: firstNonEmpty(os.Getenv("AGENT_TRACKER_SOCKET"), config.GetString("", "paths", "agent_tracker_socket")),
		TmuxSocket:    firstNonEmpty(os.Getenv("BROCCOLI_COMMS_TMUX_SOCKET"), os.Getenv("AGENT_TRACKER_TMUX_SOCKET"), config.GetString("", "paths", "tmux_socket")),
	}
	info.AppRuntime = os.Getenv("BROCCOLI_COMMS_APP_RUNTIME") == "1" || info.RuntimeDir != "" || info.TmuxSocket != ""
	info.RemoteDirectInputEnabled = config.GetBool(false, "ui", "remote_pane_input_enabled") || envEnabled("BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED") || envEnabled("BROCCOLI_COMMS_REMOTE_PANE_INPUT_SEND_ENABLED") || envEnabled("AGENT_TRACKER_REMOTE_PANE_INPUT_SEND_ENABLED")
	if info.TrackerSocket == "" && info.RuntimeDir != "" {
		info.TrackerSocket = info.RuntimeDir + "/agent-tracker.sock"
	}
	return info
}

func envEnabled(name string) bool {
	switch strings.ToLower(os.Getenv(name)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func newModel(local localClient, ownName string) model {
	return model{
		local:             local,
		ownName:           ownName,
		runtime:           runtimeInfoFromEnv(),
		mode:              homeView,
		sentMessages:      map[string][]tracker.Message{},
		unreadRows:        map[string]bool{},
		unreadCounts:      map[string]int{},
		hiddenAgents:      map[string]bool{},
		showingConfigMenu: false,
	}
}
func (m model) Init() tea.Cmd {
	return ensureMailboxCmd(m.local, m.ownName)
}

func initialLoadCmds(m model) tea.Cmd {
	return tea.Batch(
		loadHealth(m.local),
		loadAgents(m.local),
		loadOutboxCmd(),
		loadSavedMessagesCmd(),
		loadHiddenAgentsCmd(),
		loadPromptsCmd(),
		loadConfigItemsCmd(m.local),
		loadUnreadCounts(m.local, m.ownName),
		tickRefresh(),
		tickCursorBlink(),
		waitEvents(m.local, 0),
	)
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
	case tea.MouseMsg:
		return m.handleMouse(msg)
	case tea.KeyMsg:
		return m.handleKeyMsg(msg)
	case directInputSent:
		return m.handleDirectInputSent(msg)
	case clearDirectInputStatusTick:
		return m.handleClearDirectInputStatus()
	case paneCaptured:
		return m.handlePaneCaptured(msg)
	case clearPaneCaptureStatusTick:
		return m.handleClearPaneCaptureStatus()
	case refreshTick:
		return m.handleRefreshTick()
	case cursorBlinkTick:
		return m.handleCursorBlinkTick()
	case agentListSpinnerTick:
		return m.handleAgentListSpinnerTick()
	case retryEvents:
		return m, waitEvents(m.local, m.eventSeq)
	case mailboxEnsured:
		return m.handleMailboxEnsured(msg)
	case healthLoaded:
		return m.handleHealthLoaded(msg)
	case agentsLoaded:
		return m.handleAgentsLoaded(msg)
	case inboxLoaded:
		return m.handleInboxLoaded(msg)
	case allInboxLoaded:
		return m.handleAllInboxLoaded(msg)
	case restartRequested:
		return m.handleRestartRequested(msg)
	case messageSent:
		return m.handleMessageSent(msg)
	case eventsLoaded:
		return m.handleEventsLoaded(msg)
	case unreadCountsLoaded:
		return m.handleUnreadCountsLoaded(msg)
	case promptsLoaded:
		return m.handlePromptsLoaded(msg)
	case hiddenAgentsLoaded:
		return m.handleHiddenAgentsLoaded(msg)
	case hiddenAgentsSaved:
		m.err = msg.Err
	case savedMessagesLoaded:
		return m.handleSavedMessagesLoaded(msg)
	case savedMessagesSaved:
		m.err = msg.Err
	case outboxLoaded:
		return m.handleOutboxLoaded(msg)
	case paneSwitched:
		m.err = msg.Err
	case configItemsLoaded:
		return m.handleConfigItemsLoaded(msg)
	case editorClosed:
		m.err = msg.Err
	case promptEdited:
		return m.handlePromptEdited(msg)
	case agentSaved:
		m.err = msg.Err
	case agentConfigSpun:
		m.err = msg.Err
		if msg.Err == nil {
			m.directInputStatus = "Started " + msg.Name
			m.directInputStatusErr = false
			return m, tea.Batch(loadAgents(m.local), loadConfigItemsCmd(m.local), tea.Tick(4*time.Second, func(time.Time) tea.Msg { return clearDirectInputStatusTick{} }))
		}
	case approvalReviewResult:
		m.err = msg.Err
		if msg.Err == nil {
			return m, m.reloadMessages()
		}
	case memoryActionResult:
		m.err = msg.Err
		m.memoryErr = msg.Err
		m.memoryConfirm = memoryActionConfirmation{}
		if msg.Err == nil {
			return m, tea.Batch(m.reloadMessages(), loadMemoryApprovalsCmd(m.local))
		}
		m.memoryLoading = false
	case memoryApprovalsLoaded:
		previousID := ""
		if mem, ok := m.selectedMemoryRecord(); ok {
			previousID = mem.MemoryID
		}
		m.memoryLoading = false
		m.memoryErr = msg.Err
		if msg.Err == nil {
			m.memoryItems = msg.Items
			m.preserveMemorySelection(previousID)
		}
	case memoryEditClosed:
		m.err = msg.Err
		m.memoryErr = msg.Err
		if msg.Err == nil {
			return m, loadMemoryApprovalsCmd(m.local)
		}
	case memoryFormSubmitted:
		m.err = msg.Err
		m.memoryErr = msg.Err
		m.memoryLoading = msg.Err == nil
		if msg.Err == nil {
			m.memoryForm = memoryFormState{}
			return m, loadMemoryApprovalsCmd(m.local)
		}
	case tasksLoaded:
		m.err = msg.Err
		m.tasksErr = msg.Err
		m.tasksLoading = false
		if msg.Err == nil {
			m.tasksItems = msg.Tasks
			m.tasksStates = msg.States
			m.tasksApprovals = msg.Approvals
			m.clampTaskSelection()
		}
	case taskActionResult:
		m.err = msg.Err
		m.tasksErr = msg.Err
		m.tasksLoading = msg.Err == nil
		m.tasksConfirm = taskActionConfirmation{}
		if msg.Status != "" {
			m.directInputStatus = msg.Status
			m.directInputStatusErr = msg.Err != nil
		}
		if msg.Err == nil {
			return m, loadTasksCmd()
		}
	case taskEditClosed:
		m.err = msg.Err
		m.tasksErr = msg.Err
		m.tasksLoading = msg.Err == nil && !strings.Contains(msg.Status, "unchanged")
		if msg.Status != "" {
			m.directInputStatus = msg.Status
			m.directInputStatusErr = msg.Err != nil
		}
		if msg.Err == nil && !strings.Contains(msg.Status, "unchanged") {
			return m, loadTasksCmd()
		}
	}
	return m, nil
}

func (m model) currentRow() agentRow {
	if len(m.rows) == 0 || m.selected < 0 || m.selected >= len(m.rows) {
		return agentRow{}
	}
	return m.rows[m.selected]
}

func (m model) canSendCurrent() bool {
	_, ok := m.currentSendTarget()
	return ok && !m.agentListStale
}

func (m model) currentSendTarget() (agentRow, bool) {
	switch m.mode {
	case simpleView, advancedView:
		row := m.currentRow()
		return row, rowTarget(row) != ""
	default:
		return agentRow{}, false
	}
}

func (m model) retryCurrentOperation() tea.Cmd {
	switch m.retryOperation {
	case "mailbox":
		return ensureMailboxCmd(m.local, m.ownName)
	case "agents":
		return loadAgents(m.local)
	case "inbox":
		return m.reloadMessages()
	case "all_inbox":
		return loadAllInbox(m.local, m.ownName)
	default:
		return nil
	}
}

func (m *model) selectLatestMessage() {
	m.messageSelected = selectableMessageIndex(m.displayOrderedMessages(), 0, 1)
	m.messageOffset = 0
}

func selectableMessageIndex(messages []tracker.Message, start, delta int) int {
	if len(messages) == 0 {
		return 0
	}
	if delta == 0 {
		delta = 1
	}
	if start < 0 {
		start = 0
	}
	if start >= len(messages) {
		start = len(messages) - 1
	}
	for i := start; i >= 0 && i < len(messages); i += delta {
		if isSelectableTimelineMessage(messages[i]) {
			return i
		}
	}
	for i := start - delta; i >= 0 && i < len(messages); i -= delta {
		if isSelectableTimelineMessage(messages[i]) {
			return i
		}
	}
	return clampSelectedMessage(start, len(messages))
}

func clampSelectedMessage(selected, count int) int {
	if count <= 0 {
		return 0
	}
	if selected >= count {
		return count - 1
	}
	return max(0, selected)
}

func (m model) mergeSentMessages(row agentRow, inbound []tracker.Message) []tracker.Message {
	merged := append([]tracker.Message{}, inbound...)
	key := conversationKey(row)
	for _, rec := range m.outbox {
		if outboxRecordMatchesRow(rec, row) {
			merged = append(merged, outboxMessage(rec, false))
		}
	}
	for _, sent := range m.sentMessages[key] {
		if !messageIDExists(merged, sent.MessageID) {
			merged = append(merged, sent)
		}
	}
	return uniqueMessagesByID(sortMessagesByTimestamp(merged))
}

func uniqueMessagesByID(messages []tracker.Message) []tracker.Message {
	seen := map[string]bool{}
	out := messages[:0]
	for _, msg := range messages {
		if msg.MessageID != "" {
			if seen[msg.MessageID] {
				continue
			}
			seen[msg.MessageID] = true
		}
		out = append(out, msg)
	}
	return out
}

func sortMessagesByTimestamp(messages []tracker.Message) []tracker.Message {
	sort.SliceStable(messages, func(i, j int) bool {
		ti, okI := parseMessageTime(messages[i].Timestamp)
		tj, okJ := parseMessageTime(messages[j].Timestamp)
		if !okI || !okJ || ti.Equal(tj) {
			return false
		}
		return ti.Before(tj)
	})
	return messages
}

func parseMessageTime(value string) (time.Time, bool) {
	if value == "" {
		return time.Time{}, false
	}
	if t, err := time.Parse(time.RFC3339Nano, value); err == nil {
		return t, true
	}
	if t, err := time.Parse(time.RFC3339, value); err == nil {
		return t, true
	}
	return time.Time{}, false
}

func (m model) inboundMessagesForCurrent() []tracker.Message {
	row := m.currentRow()
	key := conversationKey(row)
	var inbound []tracker.Message
	for _, msg := range m.messages {
		isSent := false
		for _, sent := range m.sentMessages[key] {
			if msg.MessageID != "" && msg.MessageID == sent.MessageID {
				isSent = true
				break
			}
			if msg.MessageID == sent.MessageID && msg.Timestamp == sent.Timestamp && msg.Body == sent.Body {
				isSent = true
				break
			}
		}
		if !isSent {
			inbound = append(inbound, msg)
		}
	}
	return inbound
}

func (m *model) appendSentMessage(row agentRow, rec outboxRecord) {
	if rowTarget(row) == "" || rec.ID == "" {
		return
	}
	if m.sentMessages == nil {
		m.sentMessages = map[string][]tracker.Message{}
	}
	key := conversationKey(row)
	if messageIDExists(m.sentMessages[key], rec.ID) {
		return
	}
	m.sentMessages[key] = append(m.sentMessages[key], outboxMessage(rec, false))
}

func (m *model) removeSentMessage(row agentRow, id string) {
	if id == "" || m.sentMessages == nil {
		return
	}
	key := conversationKey(row)
	kept := m.sentMessages[key][:0]
	for _, msg := range m.sentMessages[key] {
		if msg.MessageID != id {
			kept = append(kept, msg)
		}
	}
	m.sentMessages[key] = kept
}
