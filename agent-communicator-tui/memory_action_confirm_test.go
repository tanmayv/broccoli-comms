package main

import (
	"context"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestMemoryTabActionAvailabilityByStatus(t *testing.T) {
	pending := memoryRecord{MemoryID: "mem-p", Status: "pending", Version: 1}
	active := memoryRecord{MemoryID: "mem-a", Status: "active", Version: 3}
	revoked := memoryRecord{MemoryID: "mem-r", Status: "revoked", Version: 3}
	if !memoryActionAllowed(pending, "approve") || !memoryActionAllowed(pending, "reject") || memoryActionAllowed(pending, "revoke") {
		t.Fatalf("pending action availability is wrong")
	}
	if !memoryActionAllowed(active, "revoke") || !memoryActionAllowed(active, "rollback") || memoryActionAllowed(active, "reject") {
		t.Fatalf("active action availability is wrong")
	}
	if memoryActionAllowed(revoked, "approve") || memoryActionAllowed(revoked, "reject") || memoryActionAllowed(revoked, "rollback") {
		t.Fatalf("revoked memory should not allow management actions")
	}
}

type mockMemoryActionClient struct {
	localClient
	rejectCalled   bool
	rejectParams   struct{ ID, reason string; version int }
	revokeCalled   bool
	revokeParams   struct{ ID, reason string; version int }
	rollbackCalled bool
	rollbackParams struct{ ID string; targetVersion, expectedVersion int }
}

func (m *mockMemoryActionClient) MemoryReject(ctx context.Context, memoryID string, version int, reason string, actor string) (tracker.MemoryResult, error) {
	m.rejectCalled = true
	m.rejectParams = struct{ ID, reason string; version int }{memoryID, reason, version}
	return tracker.MemoryResult{}, nil
}

func (m *mockMemoryActionClient) MemoryRevoke(ctx context.Context, memoryID string, reason string, expectedVersion int, actor string) (tracker.MemoryResult, error) {
	m.revokeCalled = true
	m.revokeParams = struct{ ID, reason string; version int }{memoryID, reason, expectedVersion}
	return tracker.MemoryResult{}, nil
}

func (m *mockMemoryActionClient) MemoryRollback(ctx context.Context, memoryID string, targetVersion, expectedVersion int, actor string) (tracker.MemoryResult, error) {
	m.rollbackCalled = true
	m.rollbackParams = struct{ ID string; targetVersion, expectedVersion int }{memoryID, targetVersion, expectedVersion}
	return tracker.MemoryResult{}, nil
}

func TestMemoryTabDestructiveActionFirstKeyConfirmsSecondExecutes(t *testing.T) {
	mock := &mockMemoryActionClient{}
	m := model{mode: memoryView, local: mock, memoryItems: []memoryRecord{{MemoryID: "mem-p", Status: "pending", Version: 2}}}
	updated, cmd := m.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("d")})
	if cmd != nil || !updated.memoryConfirmationMatches(updated.memoryItems[0], "reject") || mock.rejectCalled {
		t.Fatalf("first delete should only arm confirmation, confirm=%+v cmd=%v called=%v", updated.memoryConfirm, cmd, mock.rejectCalled)
	}
	updated, cmd = updated.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("d")})
	if cmd == nil || updated.memoryConfirm.Active() || !updated.memoryLoading {
		t.Fatalf("second delete should execute and clear confirmation, confirm=%+v loading=%v cmd=%v", updated.memoryConfirm, updated.memoryLoading, cmd)
	}
	res := cmd().(memoryActionResult)
	if res.Err != nil {
		t.Fatalf("delete command error: %v", res.Err)
	}
	if !mock.rejectCalled {
		t.Fatalf("expected MemoryReject to be called")
	}
	if mock.rejectParams.ID != "mem-p" || mock.rejectParams.version != 2 || mock.rejectParams.reason != "removed from Memory Management tab" {
		t.Fatalf("unexpected reject params: %+v", mock.rejectParams)
	}
}

func TestMemoryTabDeleteActiveConfirmsThenRevokes(t *testing.T) {
	mock := &mockMemoryActionClient{}
	m := model{mode: memoryView, local: mock, memoryItems: []memoryRecord{{MemoryID: "mem-a", Status: "active", Version: 5}}}
	updated, cmd := m.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("d")})
	if cmd != nil || !updated.memoryConfirmationMatches(updated.memoryItems[0], "revoke") {
		t.Fatalf("first active delete should confirm revoke, confirm=%+v cmd=%v", updated.memoryConfirm, cmd)
	}
	_, cmd = updated.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("d")})
	if cmd == nil {
		t.Fatalf("second active delete should execute revoke")
	}
	res := cmd().(memoryActionResult)
	if res.Err != nil {
		t.Fatalf("revoke command error: %v", res.Err)
	}
	if !mock.revokeCalled {
		t.Fatalf("expected MemoryRevoke to be called")
	}
	if mock.revokeParams.ID != "mem-a" || mock.revokeParams.version != 5 || mock.revokeParams.reason != "removed from Memory Management tab" {
		t.Fatalf("unexpected revoke params: %+v", mock.revokeParams)
	}
}

func TestMemoryTabRollbackConfirmationAndArgs(t *testing.T) {
	mock := &mockMemoryActionClient{}
	m := model{mode: memoryView, local: mock, memoryItems: []memoryRecord{{MemoryID: "mem-a", Status: "active", Version: 4}}}
	updated, cmd := m.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("R")})
	if cmd != nil || !updated.memoryConfirmationMatches(updated.memoryItems[0], "rollback") {
		t.Fatalf("first rollback should confirm, confirm=%+v cmd=%v", updated.memoryConfirm, cmd)
	}
	_, cmd = updated.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("R")})
	if cmd == nil {
		t.Fatalf("second rollback should execute")
	}
	res := cmd().(memoryActionResult)
	if res.Err != nil {
		t.Fatalf("rollback command error: %v", res.Err)
	}
	if !mock.rollbackCalled {
		t.Fatalf("expected MemoryRollback to be called")
	}
	if mock.rollbackParams.ID != "mem-a" || mock.rollbackParams.targetVersion != 3 || mock.rollbackParams.expectedVersion != 4 {
		t.Fatalf("unexpected rollback params: %+v", mock.rollbackParams)
	}
}

func TestMemoryTabConfirmationCancelPaths(t *testing.T) {
	base := model{mode: memoryView, memoryItems: []memoryRecord{{MemoryID: "mem-1", Status: "active", Version: 2}, {MemoryID: "mem-2", Status: "active", Version: 2}}, memoryConfirm: memoryActionConfirmation{Action: "revoke", MemoryID: "mem-1"}}
	cases := []tea.KeyMsg{
		{Type: tea.KeyEsc},
		{Type: tea.KeyDown},
		{Type: tea.KeyRunes, Runes: []rune("/")},
		{Type: tea.KeyRunes, Runes: []rune("s")},
		{Type: tea.KeyRunes, Runes: []rune("r")},
		{Type: tea.KeyRunes, Runes: []rune("n")},
	}
	for _, key := range cases {
		updated, _ := base.updateMemoryManagement(key)
		if updated.memoryConfirm.Active() {
			t.Fatalf("key %#v should cancel confirmation, got %+v", key, updated.memoryConfirm)
		}
	}
}

func TestMemoryTabApproveExecutesWithoutConfirmation(t *testing.T) {
	m := model{mode: memoryView, memoryItems: []memoryRecord{{MemoryID: "mem-p", Status: "pending", Version: 2}}}
	updated, cmd := m.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a")})
	if cmd == nil || updated.memoryConfirm.Active() || !updated.memoryLoading {
		t.Fatalf("approve should execute immediately without confirmation, confirm=%+v loading=%v cmd=%v", updated.memoryConfirm, updated.memoryLoading, cmd)
	}
}

func TestMemoryActionSuccessRefreshesMemoryAndMessages(t *testing.T) {
	m := model{mode: memoryView, memoryLoading: true, memoryErr: assertErr("old")}
	updatedModel, cmd := m.Update(memoryActionResult{MemoryID: "mem-p", Action: "approve"})
	updated := updatedModel.(model)
	if cmd == nil || updated.memoryErr != nil {
		t.Fatalf("success should clear error and return refresh command, err=%v cmd=%v", updated.memoryErr, cmd)
	}
}

func TestMemoryApprovalsLoadedPreservesSelectedDetail(t *testing.T) {
	m := model{mode: memoryView, memoryItems: []memoryRecord{{MemoryID: "mem-1"}, {MemoryID: "mem-2"}}, memorySelected: 1}
	updatedModel, _ := m.Update(memoryApprovalsLoaded{Items: []memoryRecord{{MemoryID: "mem-3"}, {MemoryID: "mem-2", Title: "kept"}}})
	updated := updatedModel.(model)
	if updated.memorySelected != 1 {
		t.Fatalf("selection should stay on refreshed mem-2 detail, got %d", updated.memorySelected)
	}
	if mem, ok := updated.selectedMemoryRecord(); !ok || mem.MemoryID != "mem-2" || mem.Title != "kept" {
		t.Fatalf("selected detail not refreshed/preserved: mem=%+v ok=%v", mem, ok)
	}
}

func TestMemoryActionErrorStopsLoadingAndShowsError(t *testing.T) {
	m := model{mode: memoryView, memoryLoading: true, memoryConfirm: memoryActionConfirmation{Action: "reject", MemoryID: "mem-p"}}
	updatedModel, cmd := m.Update(memoryActionResult{MemoryID: "mem-p", Action: "reject", Err: assertErr("boom")})
	updated := updatedModel.(model)
	if cmd != nil || updated.memoryLoading || updated.memoryConfirm.Active() || updated.memoryErr == nil || !strings.Contains(updated.memoryErr.Error(), "boom") {
		t.Fatalf("error should stop loading, clear confirmation, and show error: loading=%v confirm=%+v err=%v cmd=%v", updated.memoryLoading, updated.memoryConfirm, updated.memoryErr, cmd)
	}
}

type assertErr string

func (e assertErr) Error() string { return string(e) }
