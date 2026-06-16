package main

import (
	"context"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestCommandPaletteOpensMemoryManagementTab(t *testing.T) {
	m := model{commandPalette: commandPaletteState{Open: true, Query: []rune("memory")}}
	updated, cmd := m.updateCommandPalette(tea.KeyMsg{Type: tea.KeyEnter})
	if updated.mode != memoryView {
		t.Fatalf("memory action should switch to memory tab, got mode %v", updated.mode)
	}
	if updated.commandPalette.Open {
		t.Fatalf("command palette should close after opening memory management")
	}
	if !updated.memoryLoading || cmd == nil {
		t.Fatalf("opening memory management should load memory records, loading=%v cmd=%v", updated.memoryLoading, cmd)
	}
}

func TestMemoryEditorCommandDefaultsToNvimAndHonorsEditor(t *testing.T) {
	t.Setenv("EDITOR", "")
	if got := memoryEditorCommandName(); got != "nvim" {
		t.Fatalf("default editor=%q want nvim", got)
	}
	t.Setenv("EDITOR", "nano")
	if got := memoryEditorCommandName(); got != "nano" {
		t.Fatalf("EDITOR override=%q want nano", got)
	}
}

type mockMemoryListClient struct {
	localClient
	calls []map[string]any
}

func (m *mockMemoryListClient) MemoryList(ctx context.Context, params map[string]any) ([]tracker.MemoryRecord, error) {
	m.calls = append(m.calls, params)
	if params["status"] == "pending" {
		return []tracker.MemoryRecord{{MemoryID: "mem-p", Status: "pending", Version: 1}}, nil
	}
	if params["status"] == "active" {
		return []tracker.MemoryRecord{{MemoryID: "mem-a", Status: "active", Version: 2}}, nil
	}
	return nil, nil
}

func TestLoadMemoryApprovalsUsesApprovalsBackend(t *testing.T) {
	mock := &mockMemoryListClient{}
	msg := loadMemoryApprovalsCmd(mock)().(memoryApprovalsLoaded)
	if msg.Err != nil {
		t.Fatalf("loadMemoryApprovalsCmd error: %v", msg.Err)
	}
	if len(msg.Items) != 2 || msg.Items[0].MemoryID != "mem-p" || msg.Items[1].MemoryID != "mem-a" {
		t.Fatalf("loaded items = %#v", msg.Items)
	}
	if len(mock.calls) != 2 {
		t.Fatalf("expected 2 calls, got %d", len(mock.calls))
	}
	if mock.calls[0]["status"] != "pending" || mock.calls[1]["status"] != "active" {
		t.Fatalf("call params = %#v", mock.calls)
	}
}

func TestMemoryManagementTabEscDoesNotLeaveTab(t *testing.T) {
	m := model{mode: memoryView}
	updated, cmd := m.handleKeyMsg(tea.KeyMsg{Type: tea.KeyEsc})
	if updated.mode != memoryView || cmd != nil {
		t.Fatalf("esc in memory tab should only cancel local state, mode=%v cmd=%v", updated.mode, cmd)
	}
}
