package main

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestSimpleInboxLoadSelectsLatestAtTop(t *testing.T) {
	m := model{width: 80, height: 10, rows: []agentRow{{Name: "alpha", Scope: "local"}}}
	updated, _ := m.Update(inboxLoaded{Messages: []tracker.Message{{Sender: "alpha", Body: "old"}, {Sender: "alpha", Body: "new"}}})
	m = updated.(model)
	if m.messageSelected != 0 {
		t.Fatalf("selected = %d, want latest at top", m.messageSelected)
	}
	if m.messageOffset != 0 {
		t.Fatalf("offset = %d, want top 0", m.messageOffset)
	}
}

func TestAdvancedInboxLoadSelectsLatestAtTop(t *testing.T) {
	m := model{mode: advancedView, width: 80, height: 10}
	updated, _ := m.Update(allInboxLoaded{Messages: []tracker.Message{{Sender: "a", Body: "old"}, {Sender: "b", Body: "new"}}})
	m = updated.(model)
	if m.messageSelected != 0 {
		t.Fatalf("selected = %d, want latest at top", m.messageSelected)
	}
}

func TestAgentSwitchReloadSelectsLatest(t *testing.T) {
	local := &fakeLocal{inbox: []tracker.Message{{Sender: "beta", Body: "old"}, {Sender: "beta", Body: "new"}}}
	m := model{width: 80, height: 10, selected: 0, rows: []agentRow{{Name: "alpha", Scope: "local"}, {Name: "beta", Scope: "local"}}, local: local}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlN})
	m = updated.(model)
	updated, _ = m.Update(cmd())
	m = updated.(model)
	if m.selected != 1 || m.messageSelected != 0 {
		t.Fatalf("agent=%d message=%d", m.selected, m.messageSelected)
	}
}

func TestAgentSwitchWrapsCyclically(t *testing.T) {
	local := &fakeLocal{inbox: []tracker.Message{{Sender: "alpha", Body: "latest"}}}
	m := model{width: 80, height: 10, selected: 1, rows: []agentRow{{Name: "alpha", Scope: "local"}, {Name: "beta", Scope: "local"}}, local: local}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlN})
	m = updated.(model)
	updated, _ = m.Update(cmd())
	m = updated.(model)
	if m.selected != 0 {
		t.Fatalf("ctrl+n selected=%d, want 0", m.selected)
	}

	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyCtrlP})
	m = updated.(model)
	updated, _ = m.Update(cmd())
	m = updated.(model)
	if m.selected != 1 {
		t.Fatalf("ctrl+p selected=%d, want 1", m.selected)
	}
}
