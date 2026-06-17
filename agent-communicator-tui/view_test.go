package main

import (
	"regexp"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestMessageLinesHighlightSenderAndSeparateMessages(t *testing.T) {
	m := model{messages: []tracker.Message{{Sender: "alice", Timestamp: "t1", Body: "**hello**"}, {Sender: "bob", Body: "second"}}}
	lines := strings.Join(m.messageLinesForWidth(80), "\n")
	for _, want := range []string{"alice", "t1", "hello", "┃", "bob", "second"} {
		if !strings.Contains(lines, want) {
			t.Fatalf("message lines missing %q:\n%s", want, lines)
		}
	}
}

func TestMarkdownTablesRenderAsAlignedRows(t *testing.T) {
	body := "# Test Markdown Report\n\n| ID | Name | Status |\n|---:|---|---|\n| 1 | Alpha | Complete |"
	view := model{height: 24, messages: []tracker.Message{{Sender: "agent", Body: body}}}.messageView(100)
	for _, want := range []string{"Test Markdown Report", "│ ID", "Alpha", "Complete", "├"} {
		if !strings.Contains(view, want) {
			t.Fatalf("rendered markdown missing %q:\n%s", want, view)
		}
	}
}

func TestMessageLinesWrapLongMessageBody(t *testing.T) {
	m := model{messages: []tracker.Message{{Sender: "alice", Body: "one two three four five six seven"}}}
	lines := m.messageLinesForWidth(16)
	joined := strings.Join(lines, "\n")
	if !strings.Contains(joined, "one") || !strings.Contains(joined, "four") {
		t.Fatalf("expected wrapped lines, got:\n%s", joined)
	}
	for _, line := range lines {
		if lipgloss.Width(line) > 16 {
			t.Fatalf("line width %d > 16: %q", lipgloss.Width(line), line)
		}
	}
}

func TestMessageViewportScrollsIndependently(t *testing.T) {
	m := model{height: 8, messageOffset: 3, messages: []tracker.Message{{Sender: "a", Body: "one\ntwo\nthree\nfour\nfive"}}}
	view := m.messageView(80)
	if strings.Contains(view, "one") || !strings.Contains(view, "three") {
		t.Fatalf("unexpected scrolled message view:\n%s", view)
	}
}

func TestTypingKeepsMessagesPinnedToTopAndAppendsComposer(t *testing.T) {
	m := model{height: 8, messageOffset: 2, messages: []tracker.Message{{Sender: "a", Body: "one\ntwo\nthree\nfour\nfive"}}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("x")})
	m = updated.(model)
	if string(m.composer) != "x" {
		t.Fatalf("composer = %q, want x", string(m.composer))
	}
	if m.messageOffset != 0 {
		t.Fatalf("messageOffset = %d, want top 0", m.messageOffset)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeySpace})
	m = updated.(model)
	if string(m.composer) != "x " {
		t.Fatalf("composer after space = %q, want x-space", string(m.composer))
	}
}

func TestCtrlUCtrlDClampMessageScroll(t *testing.T) {
	m := model{height: 8, messages: []tracker.Message{{Sender: "a", Body: "one\ntwo\nthree\nfour\nfive"}}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyCtrlD})
	m = updated.(model)
	wantOlder := clampMessageOffset(messagePageSize(m.height), len(m.messageLinesForWidth(m.messageContentWidth())), m.messageVisibleLines())
	if m.messageOffset != wantOlder {
		t.Fatalf("ctrl+d offset = %d, want clamped %d", m.messageOffset, wantOlder)
	}
	for i := 0; i < 10; i++ {
		updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlD})
		m = updated.(model)
	}
	wantMax := messageBottomOffset(len(m.messageLinesForWidth(m.messageContentWidth())), m.messageVisibleLines())
	if m.messageOffset != wantMax {
		t.Fatalf("repeated ctrl+d offset = %d, want %d", m.messageOffset, wantMax)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlU})
	m = updated.(model)
	if m.messageOffset != max(0, wantMax-messagePageSize(m.height)) {
		t.Fatalf("ctrl+u offset = %d", m.messageOffset)
	}
}

func TestViewWideAndNarrowIncludeCoreRegions(t *testing.T) {
	m := model{width: 120, height: 30, rows: []agentRow{{Name: "alpha", Scope: "local", Status: "idle", CWD: "/repo"}}, messages: []tracker.Message{{Sender: "agent", Body: "**hello**"}}}
	wide := m.View()
	for _, want := range []string{"Switch agent", "Simple Chat", "alpha", "hello"} {
		if !strings.Contains(wide, want) {
			t.Fatalf("wide view missing %q:\n%s", want, wide)
		}
	}
	if strings.Contains(wide, "Selected") {
		t.Fatalf("wide view should be two-panel only:\n%s", wide)
	}
	if got := maxRenderedLineWidth(wide); got > m.width {
		t.Fatalf("wide view width = %d, want <= %d", got, m.width)
	}
	m.width = 48
	narrow := m.View()
	if !strings.Contains(narrow, "Simple Chat") || strings.Contains(narrow, "Selected") {
		t.Fatalf("unexpected narrow view:\n%s", narrow)
	}
	if got := maxRenderedLineWidth(narrow); got > m.width {
		t.Fatalf("narrow view width = %d, want <= %d", got, m.width)
	}
}

func TestCurrentAgentPanelDoesNotRepeatStandaloneHostLine(t *testing.T) {
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local", Status: "idle", Hostname: "host-a", ModelType: "pi"}}}
	view := m.currentAgentPanel(80, 8)
	if strings.Count(view, "host-a") != 1 {
		t.Fatalf("current agent panel should show host only inside hero metadata, got %d occurrences:\n%s", strings.Count(view, "host-a"), view)
	}
}

func TestCurrentAgentPanelShowsCurrentTaskAndNextOnSeparateLine(t *testing.T) {
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local", Status: "working", Hostname: "host-a", ModelType: "pi", CurrentTask: "Implement selected-agent current task display", CurrentTaskNextStep: "Run focused tests and submit review"}}}
	view := m.currentAgentPanel(58, 10)
	if !strings.Contains(view, "Current") || !strings.Contains(view, "Implement selected-agent") || !strings.Contains(view, "Next") || !strings.Contains(view, "Run focused tests") {
		t.Fatalf("current agent panel missing task details:\n%s", view)
	}
	currentLabelLine := renderedLineContaining(t, view, "Current")
	currentLine := renderedLineContaining(t, view, "Implement selected-agent")
	nextLine := renderedLineContaining(t, view, "Run focused tests")
	if currentLabelLine == currentLine || strings.Contains(currentLabelLine, "Implement selected-agent") {
		t.Fatalf("current label and task name should render on different lines:\n%s", view)
	}
	if currentLine == nextLine || strings.Contains(currentLine, "Run focused tests") {
		t.Fatalf("next step should render on a separate line:\n%s", view)
	}
	for _, line := range strings.Split(view, "\n") {
		if got := lipgloss.Width(line); got > 58 {
			t.Fatalf("line width=%d want <= 58 line=%q view=\n%s", got, line, view)
		}
	}
}

func TestCurrentTaskStatusColorUsesSemanticTokens(t *testing.T) {
	if currentTaskStatusColor("working") != colors.Success {
		t.Fatalf("working current task should use success token")
	}
	if currentTaskStatusColor("blocked") != colors.Error {
		t.Fatalf("blocked current task should use error token")
	}
	if currentTaskStatusColor("review") != colors.Warning {
		t.Fatalf("review current task should use warning token")
	}
	if currentTaskStatusColor("validated") != colors.Accent {
		t.Fatalf("validated current task should use accent token")
	}
}

func TestCurrentAndNextTaskLinesWrapToMaxThreeLines(t *testing.T) {
	row := agentRow{
		CurrentTask:         "Implement selected agent card wrapping for a long task title that should span several visible lines without overflowing the panel width",
		CurrentTaskNextStep: "Run focused regression tests for wrapping behavior then submit the result for review and wait for approval",
	}
	current := currentTaskLine(row, 34)
	next := nextTaskLine(row, 34)
	if got := lineCount(current); got != 3 {
		t.Fatalf("current task line count=%d want 3:\n%s", got, current)
	}
	if got := lineCount(next); got != 3 {
		t.Fatalf("next task line count=%d want 3:\n%s", got, next)
	}
	for _, rendered := range []string{current, next} {
		for _, line := range strings.Split(rendered, "\n") {
			if got := lipgloss.Width(line); got > 34 {
				t.Fatalf("wrapped line width=%d want <=34 line=%q rendered=\n%s", got, line, rendered)
			}
		}
	}
}

func TestCurrentAgentPanelShowsNoActiveTaskState(t *testing.T) {
	m := model{rows: []agentRow{{Name: "alpha", Scope: "local", Status: "idle", Hostname: "host-a", ModelType: "pi"}}}
	view := m.currentAgentPanel(50, 10)
	if !strings.Contains(view, "No active task") || !strings.Contains(view, "Next") {
		t.Fatalf("current agent panel missing no-task state:\n%s", view)
	}
}

func TestWideComposerSitsBelowConversationHeader(t *testing.T) {
	m := model{width: 120, height: 30, rows: []agentRow{{Name: "alpha", Scope: "local"}}, messages: []tracker.Message{{Sender: "agent", Body: "hello"}}}
	view := m.View()
	titleIndex := strings.Index(view, "Simple Chat")
	composerIndex := strings.Index(view, "/msg")
	messageIndex := strings.Index(view, "hello")
	if titleIndex < 0 || composerIndex < 0 || messageIndex < 0 || composerIndex < titleIndex || messageIndex < composerIndex {
		t.Fatalf("composer should render directly below conversation header and above timeline:\n%s", view)
	}
}

func TestLayoutWidthsConsumeAvailableWidth(t *testing.T) {
	tests := []struct {
		width     int
		wantChat  int
		wantRight int
	}{
		{width: 160, wantChat: 118, wantRight: 42}, // Clamped at max 42
		{width: 100, wantChat: 65, wantRight: 35},   // 35% of 100 = 35
		{width: 80, wantChat: 52, wantRight: 28},    // 35% of 80 = 28 (narrow layout, clamped between 24 and 34)
	}

	for _, tt := range tests {
		m := model{width: tt.width}
		chat, right, extra := m.layoutWidths()
		if extra != 0 {
			t.Errorf("width %d: extra panel = %d, want 0 for two-column layout", tt.width, extra)
		}
		if got := chat + right; got != m.width {
			t.Errorf("width %d: two-column width = %d, want %d", tt.width, got, m.width)
		}
		if right != tt.wantRight || chat != tt.wantChat {
			t.Errorf("width %d: chat/right = %d/%d, want %d/%d", tt.width, chat, right, tt.wantChat, tt.wantRight)
		}
	}
}

func mustUpdate(m model, msg tea.Msg) (model, tea.Cmd) {
	updated, cmd := m.Update(msg)
	return updated.(model), cmd
}

func maxRenderedLineWidth(s string) int {
	maxWidth := 0
	for _, line := range strings.Split(s, "\n") {
		if width := lipgloss.Width(line); width > maxWidth {
			maxWidth = width
		}
	}
	return maxWidth
}

func TestHomeTabWelcomeScreenRendersASCIIAndShortcuts(t *testing.T) {
	m := model{width: 80, height: 80, mode: homeView}
	view := stripANSI(m.homePanel(80, 80))
	for _, want := range []string{"____", "Welcome to Broccoli Comms TUI", "Ctrl-t", "Direct Tab Navigation", "AGENTS", "Working with Durable Memory", "C-o (Ctrl+o)", "Run agent"} {
		if !strings.Contains(view, want) {
			t.Fatalf("Home tab rendering missing %q:\n%s", want, view)
		}
	}
}

func TestChangelogTabRendersReleaseNotes(t *testing.T) {
	m := model{width: 80, height: 80, mode: changelogView}
	view := stripANSI(m.changelogPanel(80, 80))
	for _, want := range []string{"Changelog", "v0.1.5", "v0.1.4", "v0.1.3", "TUI [Home] Tab", "v0.1.2"} {
		if !strings.Contains(view, want) {
			t.Fatalf("Changelog tab rendering missing %q:\n%s", want, view)
		}
	}
}

var ansiRegex = regexp.MustCompile(`\x1b\[[0-9;]*[a-zA-Z]`)

func stripANSI(s string) string {
	return ansiRegex.ReplaceAllString(s, "")
}
