package main

import (
	"strings"
	"testing"

	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestAdvancedComposerShowsMessageModeInline(t *testing.T) {
	m := model{mode: advancedView, rows: []agentRow{{Name: "alpha"}}}
	view := m.composerView(80)
	if !strings.Contains(view, "/msg") || strings.Contains(view, "@alpha") {
		t.Fatalf("composer should show inline mode without repeating receiver: %q", view)
	}
	// In default message mode, placeholder is empty, so we just verify cursor exists
	if !strings.Contains(view, "█") {
		t.Fatalf("focused composer missing cursor: %q", view)
	}
	// Switch to text mode to verify non-empty placeholder existence and cursor order
	m.inputMode = inputModeText
	viewText := m.composerView(80)
	if !strings.Contains(viewText, "type pane") {
		t.Fatalf("composer in text mode missing placeholder: %q", viewText)
	}
	if strings.Index(viewText, "█") > strings.Index(viewText, "type pane") {
		t.Fatalf("cursor should appear before placeholder in text mode: %q", viewText)
	}
}

func TestAdvancedViewUsesAgentListAndConversationPanels(t *testing.T) {
	m := model{mode: advancedView, width: 100, height: 20, rows: []agentRow{{Name: "alpha", Scope: "local"}}, allMessages: []tracker.Message{{Sender: "beta", Body: "hello"}}}
	view := m.View()
	for _, want := range []string{"Switch agent", "Advanced Chat", "alpha", "hello"} {
		if !strings.Contains(view, want) {
			t.Fatalf("advanced view missing %q:\n%s", want, view)
		}
	}
	if strings.Contains(view, "Simple View") || strings.Contains(view, "Advanced View") {
		t.Fatalf("advanced view should not show old mode heading:\n%s", view)
	}
}

func TestAdvancedViewAggregatesInboundAndSentMessages(t *testing.T) {
	m := model{mode: advancedView, width: 100, height: 20, ownName: "agent-communicator", rows: []agentRow{{Name: "alpha", TargetAddress: "alpha"}}, sentMessages: map[string][]tracker.Message{
		"alpha": {{Sender: "You", Body: "out", Timestamp: "2026-05-19T12:01:00Z"}},
	}}
	m.allMessages = m.mergeAllMessages([]tracker.Message{{Sender: "beta", Body: "in", Timestamp: "2026-05-19T12:00:00Z"}})
	view := m.messageView(100)
	for _, want := range []string{"beta → agent-communicator", "to alpha", "in", "out"} {
		if !strings.Contains(view, want) {
			t.Fatalf("advanced view missing %q:\n%s", want, view)
		}
	}
}
