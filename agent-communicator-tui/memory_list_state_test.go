package main

import (
	"context"
	"fmt"
	"reflect"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func sampleMemoryRecords() []memoryRecord {
	return []memoryRecord{
		{MemoryID: "mem-1", Status: "pending", Version: 1, Type: "habit", SubjectAgent: "broccoli-agent", Title: "Run tests", Body: "Always run go test", Tags: []string{"quality", "tests"}},
		{MemoryID: "mem-2", Status: "active", Version: 2, Type: "fact", ProposedBy: "reviewer", Title: "Endpoint", Body: "Searchable API body", Tags: []string{"api"}},
		{MemoryID: "mem-3", Status: "active", Version: 1, Type: "skill", SubjectAgent: "coder", Title: "Deploy", Body: "Nix deployment", Tags: []string{"deploy"}},
	}
}

func TestFilteredMemoryItemsSearchesTitleBodyTypeTagsAgentStatus(t *testing.T) {
	m := model{memoryItems: sampleMemoryRecords()}
	cases := []struct {
		query string
		want  []string
	}{
		{query: "run tests", want: []string{"mem-1"}},
		{query: "api body", want: []string{"mem-2"}},
		{query: "skill deploy", want: []string{"mem-3"}},
		{query: "broccoli pending", want: []string{"mem-1"}},
	}
	for _, tc := range cases {
		m.memoryQuery = []rune(tc.query)
		got := memoryIDs(m.filteredMemoryItems())
		if !reflect.DeepEqual(got, tc.want) {
			t.Fatalf("query %q got %v want %v", tc.query, got, tc.want)
		}
	}
}

func TestFilteredMemoryItemsFiltersStatusTypeAndAgent(t *testing.T) {
	m := model{memoryItems: sampleMemoryRecords(), memoryStatusFilter: "active", memoryTypeFilter: "fact", memoryAgentFilter: "review"}
	got := memoryIDs(m.filteredMemoryItems())
	want := []string{"mem-2"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("filtered ids = %v, want %v", got, want)
	}
}

func TestMemorySearchPreservesSelectionWhenStillVisible(t *testing.T) {
	m := model{mode: memoryView, width: 100, height: 30, memoryItems: sampleMemoryRecords(), memorySelected: 1, memorySearchFocused: true}
	updated, cmd := m.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("api")})
	if cmd != nil {
		t.Fatalf("search typing should not return command: %v", cmd)
	}
	if updated.memorySelected != 0 {
		t.Fatalf("filtered selection index = %d, want 0", updated.memorySelected)
	}
	if mem, ok := updated.selectedMemoryRecord(); !ok || mem.MemoryID != "mem-2" {
		t.Fatalf("selected memory after search = %+v ok=%v, want mem-2", mem, ok)
	}
}

func TestMemorySelectionAndScrollClampForLargeList(t *testing.T) {
	m := model{mode: memoryView, width: 100, height: 20, memoryItems: makeLargeMemoryRecords(100)}
	m.moveMemorySelection(50, 5)
	if m.memorySelected != 50 {
		t.Fatalf("selected=%d want 50", m.memorySelected)
	}
	if m.memoryOffset > m.memorySelected || m.memoryOffset+5 <= m.memorySelected {
		t.Fatalf("offset=%d does not keep selected=%d visible", m.memoryOffset, m.memorySelected)
	}
	m.moveMemorySelection(1000, 5)
	if m.memorySelected != 99 {
		t.Fatalf("selected after large move=%d want 99", m.memorySelected)
	}
	m.moveMemorySelection(-1000, 5)
	if m.memorySelected != 0 || m.memoryOffset != 0 {
		t.Fatalf("after moving to top selected=%d offset=%d, want 0/0", m.memorySelected, m.memoryOffset)
	}
}

func TestMemoryListViewRendersVisibleWindowOnly(t *testing.T) {
	m := model{mode: memoryView, width: 100, height: 20, memoryItems: makeLargeMemoryRecords(50), memorySelected: 20, memoryOffset: 20}
	view := m.memoryListView(80, 6, false)
	if !strings.Contains(view, "mem-20") {
		t.Fatalf("visible window should include offset item mem-20:\n%s", view)
	}
	if strings.Contains(view, "mem-0") || strings.Contains(view, "mem-49") {
		t.Fatalf("view should not render all rows for large list:\n%s", view)
	}
}

func TestMemoryListViewAddsGapBetweenMemoryRows(t *testing.T) {
	m := model{mode: memoryView, width: 100, height: 20, memoryItems: sampleMemoryRecords(), memorySelected: 0}
	view := m.memoryListView(80, 7, false)
	lines := strings.Split(view, "\n")
	if len(lines) < 5 {
		t.Fatalf("memory list should include at least first row, gap, and second row; got %d lines:\n%s", len(lines), view)
	}
	if !strings.Contains(lines[0], "Run tests") || !strings.Contains(lines[4], "Endpoint") {
		t.Fatalf("expected first and second memory rows around gap:\n%s", view)
	}
	if strings.Contains(lines[3], "Run tests") || strings.Contains(lines[3], "Endpoint") || strings.Contains(lines[3], "mem-") {
		t.Fatalf("line between memory rows should be a blank spacer, got %q in:\n%s", lines[3], view)
	}
	if got := lipgloss.Width(lines[3]); got != 80 {
		t.Fatalf("memory row spacer width=%d want 80: %q", got, lines[3])
	}
}

type mockMemoryRefreshClient struct {
	localClient
	calls []map[string]any
}

func (m *mockMemoryRefreshClient) MemoryList(ctx context.Context, params map[string]any) ([]tracker.MemoryRecord, error) {
	m.calls = append(m.calls, params)
	if params["status"] == "pending" {
		return []tracker.MemoryRecord{{MemoryID: "mem-p", Status: "pending"}}, nil
	}
	if params["status"] == "active" {
		return []tracker.MemoryRecord{{MemoryID: "mem-a", Status: "active"}}, nil
	}
	return nil, nil
}

func TestMemoryTabRefreshSetsLoadingAndLoadsRecords(t *testing.T) {
	mock := &mockMemoryRefreshClient{}
	m := model{mode: memoryView, local: mock}
	updated, cmd := m.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("r")})
	if !updated.memoryLoading || cmd == nil {
		t.Fatalf("refresh should set loading and return command, loading=%v cmd=%v", updated.memoryLoading, cmd)
	}
	msg := cmd().(memoryApprovalsLoaded)
	if msg.Err != nil || len(msg.Items) != 2 {
		t.Fatalf("load result err=%v items=%v", msg.Err, msg.Items)
	}
	if len(mock.calls) != 2 {
		t.Fatalf("expected 2 calls, got %d", len(mock.calls))
	}
}

func TestMemoryApprovalsLoadedClearsLoadingAndPreservesSelection(t *testing.T) {
	m := model{memoryLoading: true, memoryItems: sampleMemoryRecords(), memorySelected: 1}
	updatedModel, _ := m.Update(memoryApprovalsLoaded{Items: []memoryRecord{sampleMemoryRecords()[0], sampleMemoryRecords()[1]}})
	updated := updatedModel.(model)
	if updated.memoryLoading {
		t.Fatal("memory loading should clear after load")
	}
	if mem, ok := updated.selectedMemoryRecord(); !ok || mem.MemoryID != "mem-2" {
		t.Fatalf("selection not preserved: %+v ok=%v", mem, ok)
	}
}

func TestMemoryFilterCyclingIncludesAgentTypeStatus(t *testing.T) {
	m := model{memoryItems: sampleMemoryRecords()}
	m.cycleMemoryStatusFilter()
	if m.memoryStatusFilter != "pending" {
		t.Fatalf("status filter = %q want pending", m.memoryStatusFilter)
	}
	m.cycleMemoryTypeFilter()
	if m.memoryTypeFilter != "fact" {
		t.Fatalf("type filter = %q want fact", m.memoryTypeFilter)
	}
	m.cycleMemoryAgentFilter()
	if m.memoryAgentFilter != "broccoli-agent" {
		t.Fatalf("agent filter = %q want broccoli-agent", m.memoryAgentFilter)
	}
}

func memoryIDs(items []memoryRecord) []string {
	ids := make([]string, 0, len(items))
	for _, item := range items {
		ids = append(ids, item.MemoryID)
	}
	return ids
}

func makeLargeMemoryRecords(count int) []memoryRecord {
	items := make([]memoryRecord, 0, count)
	for i := 0; i < count; i++ {
		items = append(items, memoryRecord{MemoryID: fmt.Sprintf("mem-%d", i), Status: "active", Version: 1, Type: "fact", Title: fmt.Sprintf("Memory %d", i)})
	}
	return items
}
