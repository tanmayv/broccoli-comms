package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/config"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

type fakeLocal struct {
	localClient
	agents                map[string]tracker.Agent
	inbox                 []tracker.Message
	lastLimit             int
	lastSenderID          string
	lastTracker           string
	lastSender            string
	sentTo                string
	sentBody              string
	sentSender            string
	sentID                string
	sentSwarmContext      string
	sendErr               error
	restartTarget         string
	restartTimeout        string
	restartForce          bool
	restartErr            error
	directText            string
	directSubmit          bool
	directKeys            []string
	directTarget          string
	directErr             error
	events                tracker.WaitEventsResult
	unreadCounts          map[string]int
	swarms                []tracker.Swarm
	swarmMessages         []tracker.SwarmTimelineMessage
	lastSwarmName         string
	assignedSwarm         string
	assignedMain          string
	assignedSubagents     []string
	listSwarmsCalls       int
	getSwarmTimelineCalls int
	ensureName            string
	ensureErr             error
	listOptions           tracker.ListOptions
}

func (f *fakeLocal) EnsureMailbox(_ context.Context, agentName string) (tracker.EnsureMailboxResult, error) {
	f.ensureName = agentName
	return tracker.EnsureMailboxResult{Name: agentName, AgentID: "mailbox-id", UUID: "mailbox-id"}, f.ensureErr
}
func (f *fakeLocal) RequestStop(_ context.Context, target string, timeout string, force bool) (bool, error) {
	f.restartTarget = target
	f.restartTimeout = timeout
	f.restartForce = force
	return f.restartErr == nil, f.restartErr
}
func (f *fakeLocal) TrackerInfo(context.Context) (tracker.TrackerInfo, error) {
	return tracker.TrackerInfo{Status: "ok", AgentCount: len(f.agents), OnlineAgentCount: len(f.agents)}, nil
}
func (f *fakeLocal) List(context.Context) (map[string]tracker.Agent, error) { return f.agents, nil }
func (f *fakeLocal) ListWithOptions(_ context.Context, opts tracker.ListOptions) (map[string]tracker.Agent, error) {
	f.listOptions = opts
	return f.agents, nil
}
func (f *fakeLocal) ReadInbox(_ context.Context, _ string, limit int, _ bool) (tracker.ReadInboxResult, error) {
	f.lastLimit = limit
	return tracker.ReadInboxResult{Mode: "history", Messages: f.inbox}, nil
}
func (f *fakeLocal) ReadInboxForSender(_ context.Context, _ string, limit int, _ bool, senderAgentID, senderTrackerID, senderName string) (tracker.ReadInboxResult, error) {
	f.lastLimit = limit
	f.lastSenderID = senderAgentID
	f.lastTracker = senderTrackerID
	f.lastSender = senderName
	return tracker.ReadInboxResult{Mode: "history", Messages: f.inbox}, nil
}
func (f *fakeLocal) GetUnreadCounts(context.Context, string) (tracker.UnreadCountsResult, error) {
	total := 0
	for _, count := range f.unreadCounts {
		total += count
	}
	return tracker.UnreadCountsResult{Counts: f.unreadCounts, Total: total}, nil
}
func (f *fakeLocal) SendMessage(_ context.Context, target, body string, _ []tracker.Attachment) error {
	f.sentTo, f.sentBody = target, body
	return f.sendErr
}
func (f *fakeLocal) SendMessageFrom(_ context.Context, sender, target, body string, _ []tracker.Attachment) error {
	f.sentSender, f.sentTo, f.sentBody = sender, target, body
	return f.sendErr
}
func (f *fakeLocal) SendMessageWithID(_ context.Context, sender, target, body, id string, _ []tracker.Attachment) error {
	f.sentSender, f.sentTo, f.sentBody, f.sentID = sender, target, body, id
	return f.sendErr
}
func (f *fakeLocal) SendMessageWithContext(_ context.Context, sender, target, body, id, swarmContext string, _ []tracker.Attachment) error {
	f.sentSender, f.sentTo, f.sentBody, f.sentID, f.sentSwarmContext = sender, target, body, id, swarmContext
	return f.sendErr
}
func (f *fakeLocal) SendText(_ context.Context, target, text string, submit bool) error {
	f.directTarget, f.directText, f.directSubmit = target, text, submit
	return f.directErr
}
func (f *fakeLocal) SendKeys(_ context.Context, target string, keys []string) error {
	f.directTarget, f.directKeys = target, append([]string(nil), keys...)
	return f.directErr
}
func (f *fakeLocal) WaitEvents(context.Context, tracker.WaitOptions) (tracker.WaitEventsResult, error) {
	return f.events, nil
}
func (f *fakeLocal) ListTrackers(context.Context) ([]tracker.RemoteTracker, error) {
	return nil, nil
}
func (f *fakeLocal) PublishTrackerEvent(context.Context, string, string, any) error {
	return nil
}
func (f *fakeLocal) ListSwarms(context.Context) (tracker.ListSwarmsResult, error) {
	f.listSwarmsCalls++
	return tracker.ListSwarmsResult{Swarms: f.swarms}, nil
}
func (f *fakeLocal) GetSwarmTimeline(_ context.Context, swarmName string, _ int) (tracker.SwarmTimelineResult, error) {
	f.getSwarmTimelineCalls++
	f.lastSwarmName = swarmName
	return tracker.SwarmTimelineResult{Messages: f.swarmMessages}, nil
}
func (f *fakeLocal) AssignSwarm(_ context.Context, swarmName, main string, subagents []string) (tracker.AssignSwarmResult, error) {
	f.assignedSwarm, f.assignedMain, f.assignedSubagents = swarmName, main, append([]string(nil), subagents...)
	f.swarms = []tracker.Swarm{{Name: swarmName, Main: tracker.SwarmMember{Name: main, Role: "main", TargetAddress: main}, Members: []tracker.SwarmMember{{Name: main, Role: "main", TargetAddress: main}}}}
	for _, subagent := range subagents {
		f.swarms[0].Members = append(f.swarms[0].Members, tracker.SwarmMember{Name: subagent, Role: "subagent", TargetAddress: subagent})
	}
	return tracker.AssignSwarmResult{OK: true, Swarm: swarmName, Swarms: f.swarms}, nil
}

func TestRunPrintsVersion(t *testing.T) {
	var out bytes.Buffer
	if err := run(&out, []string{"--version"}); err != nil {
		t.Fatalf("run --version: %v", err)
	}
	got := out.String()
	if !strings.Contains(got, appName) || !strings.Contains(got, version) {
		t.Fatalf("version output = %q, want app name and version", got)
	}
}
func TestRunRejectsUnknownFlag(t *testing.T) {
	var out bytes.Buffer
	if err := run(&out, []string{"--unknown"}); err == nil {
		t.Fatal("run --unknown succeeded, want error")
	}
}

func TestRuntimeInfoFromEnvDetectsBroccoliRuntime(t *testing.T) {
	t.Setenv("BROCCOLI_COMMS_APP_RUNTIME", "1")
	t.Setenv("BROCCOLI_COMMS_RUNTIME_DIR", "/tmp/broccoli-runtime")
	t.Setenv("AGENT_TRACKER_SOCKET", "")
	t.Setenv("BROCCOLI_COMMS_TMUX_SOCKET", "/tmp/broccoli-runtime/tmux.sock")
	t.Setenv("BROCCOLI_COMMS_REMOTE_PANE_INPUT_SEND_ENABLED", "1")
	info := runtimeInfoFromEnv()
	if !info.AppRuntime || info.TrackerSocket != "/tmp/broccoli-runtime/agent-tracker.sock" || info.TmuxSocket != "/tmp/broccoli-runtime/tmux.sock" || !info.RemoteDirectInputEnabled {
		t.Fatalf("runtimeInfoFromEnv() = %+v", info)
	}
}

func TestRightStatusShowsRegistryOnly(t *testing.T) {
	m := model{
		width:   120,
		runtime: runtimeInfo{AppRuntime: true, TrackerSocket: "/tmp/broccoli-runtime/agent-tracker.sock"},
		rows:    []agentRow{{Name: "alpha", Scope: "local"}},
	}
	status := m.registryStatusLine()
	if status != "registry online" {
		t.Fatalf("registry status = %q", status)
	}
}
func TestCtrlNCtrlPNavigateAndCtrlOOpensPalette(t *testing.T) {
	m := model{messageOffset: 3, rows: []agentRow{{Name: "a", Scope: "local"}, {Name: "b", Scope: "local"}}, local: &fakeLocal{}}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlN})
	m = updated.(model)
	if m.selected != 1 {
		t.Fatalf("selected = %d, want 1", m.selected)
	}
	if cmd == nil {
		t.Fatal("down should request inbox load")
	}
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyCtrlP})
	m = updated.(model)
	if m.selected != 0 || m.commandPalette.Open || cmd == nil {
		t.Fatalf("ctrl+p should navigate previous, selected=%d open=%v cmd=%v", m.selected, m.commandPalette.Open, cmd)
	}
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyCtrlO})
	m = updated.(model)
	if !m.commandPalette.Open || cmd != nil {
		t.Fatalf("ctrl+o should open palette: open=%v cmd=%v", m.commandPalette.Open, cmd)
	}
}
func TestAgentsLoadedKeepsRowsAndRequestsInbox(t *testing.T) {
	m := model{selected: 5, local: &fakeLocal{}}
	updated, cmd := m.Update(agentsLoaded{Rows: []agentRow{{Name: "a", Scope: "local"}}})
	m = updated.(model)
	if m.selected != 0 || len(m.rows) != 1 {
		t.Fatalf("model = %+v", m)
	}
	if cmd == nil {
		t.Fatal("agentsLoaded should request inbox load")
	}
}
func TestLoadPromptTemplatesCreatesDirAndSortsMarkdown(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "prompts")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "zeta.md"), []byte("z"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "alpha.md"), []byte("a"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "ignore.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	prompts, err := loadPromptTemplates(dir)
	if err != nil {
		t.Fatalf("loadPromptTemplates: %v", err)
	}
	if len(prompts) != 2 {
		t.Fatalf("len(prompts) = %d, want 2", len(prompts))
	}
	if prompts[0].Name != "alpha" || prompts[1].Name != "zeta" {
		t.Fatalf("prompt order = %#v, want alpha,zeta", prompts)
	}
}

func TestLoadPromptTemplatesCreatesMissingDir(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "missing", "prompts")
	prompts, err := loadPromptTemplates(dir)
	if err != nil {
		t.Fatalf("loadPromptTemplates missing dir: %v", err)
	}
	if len(prompts) != 0 {
		t.Fatalf("len(prompts) = %d, want 0", len(prompts))
	}
	if _, err := os.Stat(dir); err != nil {
		t.Fatalf("prompt dir was not created: %v", err)
	}
}

func TestCtrlOOpensCommandPaletteNotPromptMenu(t *testing.T) {
	m := model{local: &fakeLocal{}}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlO})
	m = updated.(model)
	if !m.commandPalette.Open || m.showingPromptMenu || cmd != nil {
		t.Fatalf("ctrl+o should open command palette only: palette=%v prompt=%v cmd=%v", m.commandPalette.Open, m.showingPromptMenu, cmd)
	}
}

func TestCtrlQQuitsCtrlROpensConfigAndPlainQRTypes(t *testing.T) {
	m := model{local: &fakeLocal{}}
	_, quitCmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlQ})
	if quitCmd == nil {
		t.Fatal("ctrl+q should quit")
	}
	updated, configCmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlR})
	m = updated.(model)
	if !m.showingConfigMenu {
		t.Fatal("ctrl+r should open config menu")
	}
	if configCmd == nil {
		t.Fatal("ctrl+r should return a non-nil load command")
	}
	// Close the config menu by pressing Esc
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(model)
	if m.showingConfigMenu {
		t.Fatal("Esc should close config menu")
	}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("qr")})
	m = updated.(model)
	if cmd != nil || string(m.composer) != "qr" {
		t.Fatalf("plain q/r should type into composer, composer=%q cmd=%v", string(m.composer), cmd)
	}
}
func TestComposerAcceptsUnicodeBackspaceAndEnterSends(t *testing.T) {
	local := &fakeLocal{}
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("hé")})
	m = updated.(model)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("qr")})
	m = updated.(model)
	if string(m.composer) != "héqr" {
		t.Fatalf("composer = %q", string(m.composer))
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyBackspace})
	m = updated.(model)
	if string(m.composer) != "héq" {
		t.Fatalf("composer after backspace = %q", string(m.composer))
	}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	if string(m.composer) != "" || cmd == nil {
		t.Fatalf("composer=%q cmd=%v, want clear and send cmd", string(m.composer), cmd)
	}
	msg := cmd()
	if sent, ok := msg.(messageSent); !ok || sent.Err != nil {
		t.Fatalf("send msg = %#v", msg)
	}
	if local.sentTo != "alpha" || local.sentBody != "héq\n\n(PS: Reply in markdown format.)" {
		t.Fatalf("sent target/body = %q/%q", local.sentTo, local.sentBody)
	}
}

func TestComposerRestartCommand(t *testing.T) {
	local := &fakeLocal{}
	m := model{
		rows: []agentRow{{Name: "alpha", Scope: "local"}},
		local: local,
	}

	// 1. Test /restart with default timeout (20s)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/restart")})
	m = updated.(model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)

	if string(m.composer) != "" {
		t.Fatalf("expected composer to be cleared, got %q", string(m.composer))
	}
	if !strings.Contains(m.directInputStatus, "Triggering graceful restart for alpha (timeout 20s)") {
		t.Fatalf("unexpected status: %q", m.directInputStatus)
	}
	if cmd == nil {
		t.Fatalf("expected a non-nil tea.Cmd")
	}
	msg := cmd()
	req, ok := msg.(restartRequested)
	if !ok {
		t.Fatalf("expected restartRequested msg, got %#v", msg)
	}
	if req.Err != nil {
		t.Fatalf("restart failed: %v", req.Err)
	}
	if local.restartTarget != "alpha" || local.restartTimeout != "20s" || local.restartForce != false {
		t.Fatalf("unexpected mock calls: target=%q timeout=%q force=%t", local.restartTarget, local.restartTimeout, local.restartForce)
	}

	// 2. Test /restart with custom timeout (15s)
	local = &fakeLocal{}
	m = model{
		rows: []agentRow{{Name: "alpha", Scope: "local"}},
		local: local,
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/restart 15")})
	m = updated.(model)
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)

	if cmd == nil {
		t.Fatalf("expected a non-nil tea.Cmd")
	}
	msg = cmd()
	req, ok = msg.(restartRequested)
	if !ok || req.Err != nil {
		t.Fatalf("restart failed or wrong msg: %#v", msg)
	}
	if local.restartTarget != "alpha" || local.restartTimeout != "15s" || local.restartForce != false {
		t.Fatalf("unexpected mock calls: target=%q timeout=%q force=%t", local.restartTarget, local.restartTimeout, local.restartForce)
	}

	// 3. Test handleRestartRequested updates model status
	updated, cmd = m.Update(req)
	m = updated.(model)
	if m.directInputStatus != "Graceful restart triggered for alpha (timeout 15s)" || m.directInputStatusErr {
		t.Fatalf("unexpected status after update: %q (err=%t)", m.directInputStatus, m.directInputStatusErr)
	}
}

func TestWrappedSendUsesCommunicatorSenderIdentity(t *testing.T) {
	local := &fakeLocal{}
	m := model{ownName: "agent-communicator", rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("hi")})
	m = updated.(model)
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	msg := cmd()
	if sent, ok := msg.(messageSent); !ok || sent.Err != nil {
		t.Fatalf("send msg = %#v", msg)
	}
	if local.sentSender != "agent-communicator" || local.sentTo != "alpha" {
		t.Fatalf("sender/target = %q/%q", local.sentSender, local.sentTo)
	}
	if local.sentBody != "hi\n\n(PS: Reply in markdown format.)" {
		t.Fatalf("sent body = %q", local.sentBody)
	}
	if local.sentSwarmContext != "" {
		t.Fatalf("simple chat should not include swarm context: %q", local.sentSwarmContext)
	}
}

func TestRemoteSendUsesHostQualifiedName(t *testing.T) {
	local := &fakeLocal{}
	row := agentRow{Name: "tanma/agent", TargetAddress: "tanmayvijay.c.googlers.com/agent", Scope: "remote"}
	cmd := sendCurrentMessage(local, "", row, "hello")
	msg := cmd()
	if sent, ok := msg.(messageSent); !ok || sent.Err != nil {
		t.Fatalf("send msg = %#v", msg)
	}
	if local.sentTo != "tanmayvijay.c.googlers.com/agent" || local.sentBody != "hello\n\n(PS: Reply in markdown format.)" {
		t.Fatalf("sent target/body = %q/%q", local.sentTo, local.sentBody)
	}
}

func TestSlashMsgSendsNormalMessageWithoutCommandPrefix(t *testing.T) {
	local := &fakeLocal{}
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/msg hello")})
	m = updated.(model)
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	msg := cmd()
	if sent, ok := msg.(messageSent); !ok || sent.Err != nil {
		t.Fatalf("send msg = %#v", msg)
	}
	if local.sentBody != "hello\n\n(PS: Reply in markdown format.)" || local.directText != "" {
		t.Fatalf("sentBody=%q directText=%q", local.sentBody, local.directText)
	}
}

func TestSlashTextSendsDirectPaneInputWithoutOutbox(t *testing.T) {
	local := &fakeLocal{}
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local, sentMessages: map[string][]tracker.Message{}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/text hello")})
	m = updated.(model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	if string(m.composer) != "" || cmd == nil {
		t.Fatalf("composer=%q cmd=%v", string(m.composer), cmd)
	}
	m, _ = mustUpdate(m, cmd())
	if local.directTarget != "alpha" || local.directText != "hello" || !local.directSubmit {
		t.Fatalf("direct target/text/submit = %q/%q/%v", local.directTarget, local.directText, local.directSubmit)
	}
	if local.sentBody != "" || len(m.outbox) != 0 || len(m.sentMessages["alpha"]) != 0 {
		t.Fatalf("normal send/outbox changed: sentBody=%q outbox=%+v sent=%+v", local.sentBody, m.outbox, m.sentMessages)
	}
	if !strings.Contains(m.directInputStatus, "Pane text sent") {
		t.Fatalf("directInputStatus = %q", m.directInputStatus)
	}
}

func TestSlashTextNoSubmitPreservesSubmitFalse(t *testing.T) {
	local := &fakeLocal{}
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/text --no-submit draft")})
	m = updated.(model)
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	_ = cmd()
	if local.directText != "draft" || local.directSubmit {
		t.Fatalf("direct text/submit = %q/%v", local.directText, local.directSubmit)
	}
}

func TestSlashKeySendsDirectKeys(t *testing.T) {
	local := &fakeLocal{}
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/key C-c Enter")})
	m = updated.(model)
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	_ = cmd()
	if local.directTarget != "alpha" || strings.Join(local.directKeys, ",") != "C-c,Enter" {
		t.Fatalf("direct target/keys = %q/%+v", local.directTarget, local.directKeys)
	}
}

func TestDirectInputToCommunicatorUIRejectedBeforeDispatch(t *testing.T) {
	local := &fakeLocal{}
	m := model{rows: []agentRow{{Name: "host/agent-communicator", AgentName: "agent-communicator", AgentType: "agent-communicator-ui", Scope: "remote", TargetAddress: "host/agent-communicator"}}, local: local, runtime: runtimeInfo{RemoteDirectInputEnabled: true}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/key Enter")})
	m = updated.(model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	m, _ = mustUpdate(m, cmd())
	if local.directTarget != "" || len(local.directKeys) != 0 {
		t.Fatalf("direct input dispatched to communicator UI: target=%q keys=%+v", local.directTarget, local.directKeys)
	}
	if string(m.composer) != "/key Enter" || !m.directInputStatusErr || !strings.Contains(m.directInputStatus, "Broccoli Comms UI") {
		t.Fatalf("composer=%q status=%q statusErr=%v", string(m.composer), m.directInputStatus, m.directInputStatusErr)
	}
}

func TestDirectInputFailureRestoresComposerAndDoesNotAppendOutbox(t *testing.T) {
	local := &fakeLocal{directErr: errors.New("boom")}
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local, sentMessages: map[string][]tracker.Message{}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/text hello")})
	m = updated.(model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	m, _ = mustUpdate(m, cmd())
	if string(m.composer) != "/text hello" || len(m.outbox) != 0 || len(m.sentMessages["alpha"]) != 0 {
		t.Fatalf("composer=%q outbox=%+v sent=%+v", string(m.composer), m.outbox, m.sentMessages)
	}
	if m.err != nil || !m.directInputStatusErr || !strings.Contains(m.directInputStatus, "Pane control failed") {
		t.Fatalf("err=%v status=%q statusErr=%v", m.err, m.directInputStatus, m.directInputStatusErr)
	}
}

func TestDirectInputFailureStatusClearsWithoutClearingUnrelatedError(t *testing.T) {
	unrelated := errors.New("pre-existing")
	m := model{err: unrelated, directInputStatus: "Pane control failed for alpha: boom", directInputStatusErr: true}
	updated, _ := m.Update(clearDirectInputStatusTick{})
	m = updated.(model)
	if m.directInputStatus != "" || m.directInputStatusErr {
		t.Fatalf("status=%q statusErr=%v", m.directInputStatus, m.directInputStatusErr)
	}
	if m.err != unrelated {
		t.Fatalf("err = %v, want preserved unrelated error", m.err)
	}
}

func TestRemoteDirectInputRejectedBeforeDispatch(t *testing.T) {
	local := &fakeLocal{}
	row := agentRow{Name: "host/alpha", Scope: "remote", TargetAddress: "host/alpha"}
	m := model{rows: []agentRow{row}, local: local}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/key C-c")})
	m = updated.(model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	m, _ = mustUpdate(m, cmd())
	if local.directTarget != "" || string(m.composer) != "/key C-c" || !m.directInputStatusErr {
		t.Fatalf("directTarget=%q composer=%q statusErr=%v", local.directTarget, string(m.composer), m.directInputStatusErr)
	}
	if !strings.Contains(m.directInputStatus, "remote direct pane input is disabled") {
		t.Fatalf("directInputStatus = %q", m.directInputStatus)
	}
}

func TestRemoteDirectInputEnabledDispatchesExactTargetAddress(t *testing.T) {
	local := &fakeLocal{}
	row := agentRow{Name: "r1/alpha", Scope: "remote", TargetAddress: "registry-1:host.example/alpha"}
	m := model{rows: []agentRow{row}, local: local, runtime: runtimeInfo{RemoteDirectInputEnabled: true}, sentMessages: map[string][]tracker.Message{}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/text remote hello")})
	m = updated.(model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	m, _ = mustUpdate(m, cmd())
	if local.directTarget != "registry-1:host.example/alpha" || local.directText != "remote hello" || !local.directSubmit {
		t.Fatalf("remote direct target/text/submit = %q/%q/%v", local.directTarget, local.directText, local.directSubmit)
	}
	if string(m.composer) != "" || len(m.outbox) != 0 || len(m.sentMessages[conversationKey(row)]) != 0 {
		t.Fatalf("composer=%q outbox=%+v sent=%+v", string(m.composer), m.outbox, m.sentMessages)
	}
	if m.directInputStatusErr || !strings.Contains(m.directInputStatus, "Pane text sent") || !strings.Contains(m.directInputStatus, "r1/alpha") {
		t.Fatalf("directInputStatus=%q err=%v", m.directInputStatus, m.directInputStatusErr)
	}
}

func TestRemoteDirectInputEnabledFailureRestoresComposer(t *testing.T) {
	local := &fakeLocal{directErr: errors.New("registry disabled")}
	row := agentRow{Name: "r1/alpha", Scope: "remote", TargetAddress: "registry-1:host.example/alpha"}
	m := model{rows: []agentRow{row}, local: local, runtime: runtimeInfo{RemoteDirectInputEnabled: true}, sentMessages: map[string][]tracker.Message{}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/key C-c")})
	m = updated.(model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	m, _ = mustUpdate(m, cmd())
	if local.directTarget != "registry-1:host.example/alpha" || strings.Join(local.directKeys, ",") != "C-c" {
		t.Fatalf("remote direct target/keys = %q/%+v", local.directTarget, local.directKeys)
	}
	if string(m.composer) != "/key C-c" || !m.directInputStatusErr || !strings.Contains(m.directInputStatus, "registry disabled") {
		t.Fatalf("composer=%q status=%q statusErr=%v", string(m.composer), m.directInputStatus, m.directInputStatusErr)
	}
}

func TestFooterOmitsUnsupportedShortcutHints(t *testing.T) {
	footer := model{width: 200}.footer(200)
	if strings.Contains(footer, "pane control") || strings.Contains(footer, "F1-F3") || strings.Contains(footer, "ctrl") {
		t.Fatalf("footer should be sparse: %q", footer)
	}
}

func TestSendFailureRestoresComposer(t *testing.T) {
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: &fakeLocal{sendErr: errors.New("boom")}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("hello")})
	m = updated.(model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	m, _ = mustUpdate(m, cmd())
	if string(m.composer) != "hello" || m.err == nil {
		t.Fatalf("composer=%q err=%v", string(m.composer), m.err)
	}
}

func TestMessageSentClearsStaleErrorAndReloadsInbox(t *testing.T) {
	m := model{err: context.Canceled, rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: &fakeLocal{}}
	updated, cmd := m.Update(messageSent{})
	m = updated.(model)
	if m.err != nil {
		t.Fatalf("err = %v, want nil", m.err)
	}
	if cmd == nil {
		t.Fatal("successful send should reload inbox")
	}
}

func TestWaitEventsCommandReturnsLoadedEvents(t *testing.T) {
	cmd := waitEvents(&fakeLocal{events: tracker.WaitEventsResult{LastSeq: 7}}, 3)
	if cmd == nil {
		t.Fatal("waitEvents should return a command")
	}
	msg, ok := cmd().(eventsLoaded)
	if !ok || msg.Err != nil || msg.Result.LastSeq != 7 {
		t.Fatalf("waitEvents message = %#v", msg)
	}
}

func TestEventsLoadedUpdatesSeqAndReloadsInbox(t *testing.T) {
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: &fakeLocal{}}
	updated, cmd := m.Update(eventsLoaded{Result: tracker.WaitEventsResult{LastSeq: 4, Events: []tracker.Event{{Seq: 4, Type: "message_delivered", TargetAgentName: "alpha"}}}})
	m = updated.(model)
	if m.eventSeq != 4 {
		t.Fatalf("eventSeq = %d, want 4", m.eventSeq)
	}
	if cmd == nil {
		t.Fatal("events should schedule wait and inbox reload")
	}
}

func TestEventsLoadedGapReloadsInbox(t *testing.T) {
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: &fakeLocal{}}
	updated, cmd := m.Update(eventsLoaded{Result: tracker.WaitEventsResult{LastSeq: 9, Gap: true}})
	m = updated.(model)
	if m.eventSeq != 9 || cmd == nil {
		t.Fatalf("eventSeq=%d cmd=%v", m.eventSeq, cmd)
	}
}

func TestShouldReloadForEventsIgnoresRemoteAndUnrelatedEvents(t *testing.T) {
	result := tracker.WaitEventsResult{Events: []tracker.Event{{TargetAgentName: "other"}}}
	if shouldReloadForEvents("", agentRow{Name: "alpha", Scope: "local"}, result) {
		t.Fatal("unrelated event should not reload selected inbox")
	}
	result.Events[0].TargetAgentName = "alpha"
	if shouldReloadForEvents("", agentRow{Name: "alpha", Scope: "remote"}, result) {
		t.Fatal("remote selection should not reload local inbox")
	}
}

func TestShouldReloadForEventsUsesOwnNameWhenWrapped(t *testing.T) {
	row := agentRow{Name: "selected-peer", Scope: "local"}
	result := tracker.WaitEventsResult{Events: []tracker.Event{{TargetAgentName: "agent-communicator"}}}
	if !shouldReloadForEvents("agent-communicator", row, result) {
		t.Fatal("event targeting communicator should reload selected conversation when wrapped")
	}
}

func TestEventsLoadedErrorSchedulesDelayedRetry(t *testing.T) {
	m := model{eventSeq: 3, local: &fakeLocal{events: tracker.WaitEventsResult{LastSeq: 4}}}
	_, cmd := m.Update(eventsLoaded{Err: errors.New("poll failed")})
	if cmd == nil {
		t.Fatal("wait error should schedule delayed retry")
	}
	if _, ok := cmd().(retryEvents); !ok {
		t.Fatal("error path should emit retryEvents delay marker before retrying waitEvents")
	}
}

func TestRetryEventsStartsWaitEvents(t *testing.T) {
	m := model{eventSeq: 3, local: &fakeLocal{events: tracker.WaitEventsResult{LastSeq: 4}}}
	_, cmd := m.Update(retryEvents{})
	if cmd == nil {
		t.Fatal("retryEvents should restart waitEvents")
	}
	msg, ok := cmd().(eventsLoaded)
	if !ok || msg.Result.LastSeq != 4 {
		t.Fatalf("retryEvents message = %#v", msg)
	}
}

func TestCtrlWDeletesPreviousWord(t *testing.T) {
	m := model{composer: []rune("hello world  ")}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyCtrlW})
	m = updated.(model)
	if string(m.composer) != "hello " {
		t.Fatalf("composer = %q, want hello-space", string(m.composer))
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlW})
	m = updated.(model)
	if string(m.composer) != "" {
		t.Fatalf("composer = %q, want empty", string(m.composer))
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlW})
	m = updated.(model)
	if string(m.composer) != "" {
		t.Fatalf("empty ctrl+w composer = %q", string(m.composer))
	}
}

func TestLoadInboxUsesOwnInboxAndFiltersBySelectedAgent(t *testing.T) {
	local := &fakeLocal{inbox: []tracker.Message{{Sender: "alpha", Body: "from alpha"}, {Sender: "beta", Body: "from beta"}}}
	msg := loadInbox(local, "agent-communicator", agentRow{Name: "alpha", Scope: "local"})()
	loaded := msg.(inboxLoaded)
	if len(loaded.Messages) != 1 || loaded.Messages[0].Body != "from alpha" {
		t.Fatalf("loaded messages = %+v", loaded.Messages)
	}
	if local.lastLimit != simpleInboxFetchLimit {
		t.Fatalf("ReadInbox limit = %d, want %d", local.lastLimit, simpleInboxFetchLimit)
	}
	if local.lastSender != "alpha" {
		t.Fatalf("ReadInbox sender filter = %q, want alpha", local.lastSender)
	}
}

func TestLoadInboxUsesStableLocalSenderFilters(t *testing.T) {
	local := &fakeLocal{inbox: []tracker.Message{{Sender: "alpha", SenderAgentID: "agent-1", SenderTrackerID: "local-tracker", Body: "from local"}}}
	row := agentRow{Name: "alpha", Scope: "local", AgentID: "agent-1", TrackerID: "local-tracker"}
	msg := loadInbox(local, "agent-communicator", row)()
	loaded := msg.(inboxLoaded)
	if len(loaded.Messages) != 1 {
		t.Fatalf("loaded messages = %+v", loaded.Messages)
	}
	if local.lastSenderID != "agent-1" || local.lastTracker != "local-tracker" || local.lastSender != "" {
		t.Fatalf("local sender filters id=%q tracker=%q name=%q", local.lastSenderID, local.lastTracker, local.lastSender)
	}
}

func TestLoadInboxUsesStableRemoteSenderFilters(t *testing.T) {
	local := &fakeLocal{inbox: []tracker.Message{{Sender: "alpha", SenderAgentID: "agent-1", SenderTrackerID: "tracker-1", Body: "from remote"}}}
	row := agentRow{Name: "host/alpha", Scope: "remote", AgentID: "agent-1", TrackerID: "tracker-1", Hostname: "host", AgentName: "alpha", TargetAddress: "host/alpha"}
	msg := loadInbox(local, "agent-communicator", row)()
	loaded := msg.(inboxLoaded)
	if len(loaded.Messages) != 1 {
		t.Fatalf("loaded messages = %+v", loaded.Messages)
	}
	if local.lastSenderID != "agent-1" || local.lastTracker != "tracker-1" || local.lastSender != "" {
		t.Fatalf("remote sender filters id=%q tracker=%q name=%q", local.lastSenderID, local.lastTracker, local.lastSender)
	}
}

func TestLoadInboxAvoidsExactSenderFilterForLegacyRemoteRows(t *testing.T) {
	local := &fakeLocal{inbox: []tracker.Message{{Sender: "alpha (via host.example)", Body: "legacy remote"}}}
	row := agentRow{Name: "host/alpha", Scope: "remote", Hostname: "host.example", AgentName: "alpha", TargetAddress: "host.example/alpha"}
	msg := loadInbox(local, "agent-communicator", row)()
	loaded := msg.(inboxLoaded)
	if len(loaded.Messages) != 1 {
		t.Fatalf("loaded messages = %+v", loaded.Messages)
	}
	if local.lastSenderID != "" || local.lastTracker != "" || local.lastSender != "" {
		t.Fatalf("legacy remote filters id=%q tracker=%q name=%q", local.lastSenderID, local.lastTracker, local.lastSender)
	}
}

func TestFilterConversationKeepsOnlyRelevantTaskUpdates(t *testing.T) {
	messages := []tracker.Message{
		{Sender: "other", Body: "not selected"},
		{Sender: "task-kernel", Body: "Task task-1 moved", Kind: "task_update", ContentType: taskUpdateContentType, TaskID: "task-1", TaskAssignedAgent: "alpha"},
		{Sender: "task-kernel", Body: "Task task-2 moved", Kind: "task_update", ContentType: taskUpdateContentType, TaskID: "task-2", TaskAssignedAgent: "beta"},
	}
	filtered := filterConversation(messages, agentRow{Name: "alpha", Scope: "local", CurrentTaskID: "task-1"})
	if len(filtered) != 1 || filtered[0].TaskID != "task-1" {
		t.Fatalf("filtered = %+v", filtered)
	}
}

func TestFilterConversationKeepsRemoteAssignedTaskUpdates(t *testing.T) {
	messages := []tracker.Message{{Sender: "task-kernel", Body: "Task task-1 moved", Kind: "task_update", ContentType: taskUpdateContentType, TaskID: "task-1", TaskAssignedAgent: "host/alpha"}}
	row := agentRow{Name: "host/alpha", Scope: "remote", Hostname: "host", AgentName: "alpha", TargetAddress: "host/alpha"}
	filtered := filterConversation(messages, row)
	if len(filtered) != 1 || filtered[0].TaskID != "task-1" {
		t.Fatalf("filtered = %+v", filtered)
	}
}

func TestFilterConversationRejectsTaskUpdateRemoteLocalNameCollisions(t *testing.T) {
	messages := []tracker.Message{
		{Sender: "task-kernel", Body: "local alpha", Kind: "task_update", ContentType: taskUpdateContentType, TaskID: "task-local", TaskAssignedAgent: "alpha"},
		{Sender: "task-kernel", Body: "host1 alpha", Kind: "task_update", ContentType: taskUpdateContentType, TaskID: "task-host1", TaskAssignedAgent: "host1/alpha"},
		{Sender: "task-kernel", Body: "host2 alpha", Kind: "task_update", ContentType: taskUpdateContentType, TaskID: "task-host2", TaskAssignedAgent: "host2/alpha"},
	}
	remote := filterConversation(messages, agentRow{Name: "host2/alpha", Scope: "remote", Hostname: "host2", AgentName: "alpha", TargetAddress: "host2/alpha"})
	if len(remote) != 1 || remote[0].TaskID != "task-host2" {
		t.Fatalf("remote filtered = %+v", remote)
	}
	local := filterConversation(messages, agentRow{Name: "alpha", Scope: "local", AgentName: "alpha"})
	if len(local) != 1 || local[0].TaskID != "task-local" {
		t.Fatalf("local filtered = %+v", local)
	}
}

func TestFilterConversationMatchesRemoteSenderFormat(t *testing.T) {
	messages := []tracker.Message{{Sender: "zv2-bmod-agent (via tanmayvijay.c.googlers.com)", Body: "remote"}, {Sender: "other (via tanmayvijay.c.googlers.com)", Body: "other"}}
	row := agentRow{Name: "tanma/zv2-bmod-agent", Scope: "remote", Hostname: "tanmayvijay.c.googlers.com", AgentName: "zv2-bmod-agent", TargetAddress: "tanmayvijay.c.googlers.com/zv2-bmod-agent"}
	filtered := filterConversation(messages, row)
	if len(filtered) != 1 || filtered[0].Body != "remote" {
		t.Fatalf("filtered = %+v", filtered)
	}
}

func TestRunNewAgentFlowUsesConfiguredProviderAndHost(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", tmp)
	configDir := filepath.Join(tmp, "broccoli-comms")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "config.toml"), []byte("[providers.pi]\ncmd = 'pi'\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	config.ResetForTest()
	defer config.ResetForTest()

	m := model{showingConfigMenu: true, configItems: []ConfigSelectionItem{{Name: "Run new agent on remote-host", IsNewAgent: true, IsRemote: true, Hostname: "remote-host", Launchable: true}}, local: &fakeLocal{}}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	if cmd != nil || !m.showingRunAgentForm || m.runAgentHost != "remote-host" || m.runAgentProvider != "pi" {
		t.Fatalf("new-agent form not opened correctly: host=%q provider=%q cmd=%v", m.runAgentHost, m.runAgentProvider, cmd)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("coder")})
	m = updated.(model)
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	if m.showingRunAgentForm || cmd == nil {
		t.Fatalf("enter should close form and return run command")
	}
	args, err := runNewAgentArgs("coder", "remote-host", "pi")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"run", "--host", "remote-host", "--json", "coder", "--", "pi"}
	if strings.Join(args, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestRunNewAgentFormAutocompleteProviderDropdownAndArgs(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", tmp)
	configDir := filepath.Join(tmp, "broccoli-comms")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "config.toml"), []byte("[providers.codex]\ncmd = 'codex'\n[providers.pi]\ncmd = 'pi'\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	config.ResetForTest()
	defer config.ResetForTest()

	m := model{configItems: []ConfigSelectionItem{
		{Name: "coder-main", Running: true, Hostname: "remote-host"},
		{Name: "local-only", Running: true, Hostname: localHostname()},
		{Name: "Run new agent on remote-host", IsNewAgent: true, IsRemote: true, Hostname: "remote-host", Launchable: true},
	}, local: &fakeLocal{}}
	m.openRunAgentForm(m.configItems[2])
	if got := strings.Join(m.runAgentSuggestions, ","); got != "coder-main" {
		t.Fatalf("suggestions = %q, want remote-host scoped coder-main", got)
	}

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("cod")})
	m = updated.(model)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyTab})
	m = updated.(model)
	if string(m.runAgentName) != "coder-main" || m.runAgentField != 0 {
		t.Fatalf("tab should autocomplete name before moving fields: name=%q field=%d", string(m.runAgentName), m.runAgentField)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyTab}) // Move from 0 (Name) to 1 (CWD)
	m = updated.(model)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyTab}) // Move from 1 (CWD) to 2 (Provider)
	m = updated.(model)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyDown}) // Cycle provider to "pi"
	m = updated.(model)
	if m.runAgentProvider != "pi" {
		t.Fatalf("provider dropdown did not cycle to pi: %q", m.runAgentProvider)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyTab}) // Move from 2 (Provider) to 3 (Args)
	m = updated.(model)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("--json --verbose")})
	m = updated.(model)
	args, err := runNewAgentArgs(string(m.runAgentName), m.runAgentHost, m.runAgentProvider, string(m.runAgentArgs))
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"run", "--host", "remote-host", "--json", "coder-main", "--", "pi", "--json", "--verbose"}
	if strings.Join(args, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestConfigMenuShowsFullRemoteHostname(t *testing.T) {
	m := model{showingConfigMenu: true, configItems: []ConfigSelectionItem{{Name: "Run new agent on tanmayvijay.c.googlers.com", Description: "provider pi", IsNewAgent: true, IsRemote: true, Hostname: "tanmayvijay.c.googlers.com", Launchable: true}}, local: &fakeLocal{}}
	view := m.renderConfigMenu(140, 12)
	if !strings.Contains(view, "tanmayvijay.c.googlers.com") || strings.Contains(view, "[tanma]") {
		t.Fatalf("config menu should render full remote hostname without fixed shortening:\n%s", view)
	}
}

func TestProviderNamesForHostPrefersRemoteHostCommands(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", tmp)
	configDir := filepath.Join(tmp, "broccoli-comms")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "config.toml"), []byte("[providers.codex]\ncmd = 'codex'\n[providers.pi]\ncmd = 'pi'\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	config.ResetForTest()
	defer config.ResetForTest()

	providers := providerNamesForHost([]ConfigSelectionItem{
		{Name: "remote-pi", Hostname: "remote-host", Command: "pi"},
		{Name: "other", Hostname: "other-host", Command: "codex"},
	}, "remote-host")
	if got := strings.Join(providers, ","); got != "pi" {
		t.Fatalf("providers = %q, want host-scoped pi", got)
	}
}

func TestAgentConfigMenuInteraction(t *testing.T) {
	m := model{
		local:             &fakeLocal{},
		showingConfigMenu: true,
		configItems: []ConfigSelectionItem{
			{Name: "jetski", Description: "Jetski agent", IsRemote: true, TrackerID: "t1"},
			{Name: "pi", Description: "Pi agent", IsRemote: true, TrackerID: "t2"},
		},
		configSelected: 0,
	}

	if !m.showingConfigMenu {
		t.Fatalf("expected showingConfigMenu to be true")
	}
	if m.configSelected != 0 {
		t.Fatalf("expected configSelected to be 0, got %d", m.configSelected)
	}

	// 2. Press KeyDown to go to next option
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyDown})
	m = updated.(model)
	if m.configSelected != 1 {
		t.Fatalf("expected configSelected to be 1, got %d", m.configSelected)
	}

	// 3. Press KeyDown again (should stay at index 1 because it's capped at len-1)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyDown})
	m = updated.(model)
	if m.configSelected != 1 {
		t.Fatalf("expected configSelected to be 1, got %d", m.configSelected)
	}

	// 4. Press KeyUp to go back to index 0
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyUp})
	m = updated.(model)
	if m.configSelected != 0 {
		t.Fatalf("expected configSelected to be 0, got %d", m.configSelected)
	}

	// 5. Press Enter to select the config (hides the menu and triggers spin)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	if m.showingConfigMenu {
		t.Fatalf("expected showingConfigMenu to be false after selection")
	}
	if cmd == nil {
		t.Fatalf("expected spin command, got nil")
	}

	// 6. Re-open and Press Esc to close
	m.showingConfigMenu = true
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(model)
	if m.showingConfigMenu {
		t.Fatalf("expected showingConfigMenu to be false after Esc")
	}
}

func TestCtrlXPaneCaptureTriggersAsyncCaptureAndClears(t *testing.T) {
	m := model{
		rows: []agentRow{
			{Name: "alice", Scope: "local", TargetAddress: "alice"},
		},
		selected: 0,
		local:    &fakeLocal{},
	}

	// 1. Send Ctrl+X KeyMsg
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlX})
	m = updated.(model)

	if m.paneCaptureStatus != "Capturing pane snapshot for alice..." {
		t.Fatalf("unexpected paneCaptureStatus: %q", m.paneCaptureStatus)
	}
	if cmd == nil {
		t.Fatal("ctrl+x should return a non-nil request command")
	}

	// 2. Send successful paneCaptured Msg
	updated, cmd = m.Update(paneCaptured{Target: "alice"})
	m = updated.(model)

	if m.paneCaptureStatus != "Pane snapshot for alice delivered successfully!" {
		t.Fatalf("unexpected paneCaptureStatus on success: %q", m.paneCaptureStatus)
	}
	if cmd == nil {
		t.Fatal("paneCaptured success should return a tick command to clear status")
	}

	// 3. Send clearPaneCaptureStatusTick Msg
	updated, cmd = m.Update(clearPaneCaptureStatusTick{})
	m = updated.(model)

	if m.paneCaptureStatus != "" {
		t.Fatalf("paneCaptureStatus was not cleared, got: %q", m.paneCaptureStatus)
	}
	if cmd != nil {
		t.Fatal("clearPaneCaptureStatusTick should return nil command")
	}
}

func TestInitEnsuresMailboxBeforeInitialLoads(t *testing.T) {
	local := &fakeLocal{}
	m := newModel(local, "agent-communicator")
	cmd := m.Init()
	if cmd == nil {
		t.Fatal("Init returned nil command")
	}
	msg := cmd()
	ensured, ok := msg.(mailboxEnsured)
	if !ok || ensured.Err != nil {
		t.Fatalf("Init msg = %#v", msg)
	}
	if local.ensureName != "agent-communicator" {
		t.Fatalf("ensureName = %q", local.ensureName)
	}
}

func TestMailboxEnsuredStartsInitialLoads(t *testing.T) {
	m := model{ownName: "agent-communicator", local: &fakeLocal{}}
	updated, cmd := m.Update(mailboxEnsured{})
	m = updated.(model)
	if m.err != nil || m.retryOperation != "" || cmd == nil {
		t.Fatalf("model err=%v retry=%q cmd=%v", m.err, m.retryOperation, cmd)
	}
}

func TestRetryKeyRetriesMailboxFailure(t *testing.T) {
	local := &fakeLocal{}
	m := model{ownName: "agent-communicator", local: local, err: errors.New("boom"), retryOperation: "mailbox"}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("r")})
	m = updated.(model)
	if cmd == nil {
		t.Fatal("retry key returned nil command")
	}
	msg := cmd()
	if _, ok := msg.(mailboxEnsured); !ok {
		t.Fatalf("retry msg = %#v", msg)
	}
	if string(m.composer) != "" || local.ensureName != "agent-communicator" {
		t.Fatalf("composer=%q ensureName=%q", string(m.composer), local.ensureName)
	}
}

func TestFooterShowsRetryHintForError(t *testing.T) {
	m := model{err: errors.New("boom"), retryOperation: "agents"}
	footer := m.footer(120)
	if !strings.Contains(footer, "error · boom · r retry") {
		t.Fatalf("footer missing retry hint: %q", footer)
	}
}

func TestExistingAgentRunOverrides(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", tmp)
	configDir := filepath.Join(tmp, "broccoli-comms")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "config.toml"), []byte("[providers.jetski]\ncmd = 'jetski'\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	config.ResetForTest()
	defer config.ResetForTest()

	m := model{
		configItems: []ConfigSelectionItem{
			{
				Name:        "concord-agent",
				ProfileName: "concord-agent",
				Description: "configured agent",
				Hostname:    localHostname(),
				Configured:  true,
				Launchable:  true,
				Command:     "jetski",
				CWD:         "/default/cwd/path",
			},
		},
		showingConfigMenu: true,
		configSelected:    0,
		local:             &fakeLocal{},
	}

	// 1. Press Enter in the config menu on the configured agent
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)

	if cmd != nil {
		t.Fatal("Enter on launchable configured agent should NOT return an execution command immediately")
	}
	if !m.showingRunAgentForm {
		t.Fatal("Enter should have opened the run agent form")
	}
	if !m.runAgentIsExisting {
		t.Fatal("Form should be marked as existing agent launcher")
	}
	if string(m.runAgentName) != "concord-agent" {
		t.Fatalf("Expected pre-populated name concord-agent, got %q", string(m.runAgentName))
	}
	if string(m.runAgentCWD) != "/default/cwd/path" {
		t.Fatalf("Expected pre-populated CWD /default/cwd/path, got %q", string(m.runAgentCWD))
	}
	if m.runAgentField != 1 {
		t.Fatalf("Focus should start at CWD override field (index 1) for existing agents, got %d", m.runAgentField)
	}

	// 2. Type CWD override characters (Backspace then type 'h-override')
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyBackspace})
	m = updated.(model)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("h-override")})
	m = updated.(model)

	if got := string(m.runAgentCWD); got != "/default/cwd/path-override" {
		t.Fatalf("CWD override mismatch: got %q, want %q", got, "/default/cwd/path-override")
	}

	// 3. Verify the arguments builder returns the correct arguments with the CWD override
	optionalArgs := strings.TrimSpace(string(m.runAgentArgs))
	args := runAgentWithOverridesArgs(
		m.runAgentIsExisting,
		m.runAgentProfileName,
		m.runAgentHost,
		string(m.runAgentCWD),
		m.runAgentDefaultCWD,
		m.runAgentProvider,
		m.runAgentDefaultProv,
		optionalArgs,
	)

	want := []string{"run", "--cwd", "/default/cwd/path-override", "--json", "concord-agent"}
	if strings.Join(args, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("args = %#v, want %#v", args, want)
	}
}

func TestAltShortcutsSwitchTabs(t *testing.T) {
	m := model{
		mode:  homeView,
		local: &fakeLocal{},
	}

	// 1. Press Alt-2 (should switch to simpleView)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("2"), Alt: true})
	m = updated.(model)
	if m.mode != simpleView {
		t.Fatalf("Alt-2 failed to switch to simpleView, got: %v", m.mode)
	}

	// 2. Press Alt-5 (should switch to tasksView)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("5"), Alt: true})
	m = updated.(model)
	if m.mode != tasksView {
		t.Fatalf("Alt-5 failed to switch to tasksView, got: %v", m.mode)
	}

	// 3. Press Alt-h (should switch to homeView)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("h"), Alt: true})
	m = updated.(model)
	if m.mode != homeView {
		t.Fatalf("Alt-h failed to switch to homeView, got: %v", m.mode)
	}

	// 4. Press Alt-m (should switch to memoryView)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("M"), Alt: true})
	m = updated.(model)
	if m.mode != memoryView {
		t.Fatalf("Alt-M failed to switch to memoryView, got: %v", m.mode)
	}
}
