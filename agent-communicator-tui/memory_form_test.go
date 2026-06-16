package main

import (
	"context"
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestMemoryNewFormOpenCancelAndValidation(t *testing.T) {
	m := model{mode: memoryView, ownName: "broccoli-agent"}
	updated, cmd := m.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("n")})
	if cmd != nil || updated.memoryForm.Mode != memoryFormNew {
		t.Fatalf("new form mode=%v cmd=%v", updated.memoryForm.Mode, cmd)
	}
	if got := updated.memoryForm.Inputs[memoryFormAgent].Value(); got != "broccoli-agent" {
		t.Fatalf("agent default=%q", got)
	}
	updated, cmd = updated.updateMemoryForm(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd != nil || updated.memoryForm.Err == nil || !strings.Contains(updated.memoryForm.Err.Error(), "title") {
		t.Fatalf("empty form should validate title, err=%v cmd=%v", updated.memoryForm.Err, cmd)
	}
	updated, cmd = updated.updateMemoryForm(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd != nil || updated.memoryForm.Mode != memoryFormNone {
		t.Fatalf("esc should cancel form mode=%v cmd=%v", updated.memoryForm.Mode, cmd)
	}
}

type mockMemoryFormClient struct {
	localClient
	proposeCalled bool
	proposeParams map[string]any
	proposeErr    error
}

func (m *mockMemoryFormClient) MemoryPropose(ctx context.Context, params map[string]any) (tracker.MemoryResult, error) {
	m.proposeCalled = true
	m.proposeParams = params
	return tracker.MemoryResult{}, m.proposeErr
}

func TestMemoryNewFormSubmitArgs(t *testing.T) {
	form := filledNewMemoryForm()
	mock := &mockMemoryFormClient{}
	_ = submitMemoryFormCmd(mock, form)().(memoryFormSubmitted)

	if !mock.proposeCalled {
		t.Fatalf("expected MemoryPropose to be called")
	}
	p := mock.proposeParams
	if p["type"] != "habit" || p["title"] != "Run tests" || p["body"] != "Always run go test" ||
		p["proposed_by"] != "broccoli-agent" || p["subject_agent"] != "broccoli-agent" ||
		p["source_task_id"] != "task-1" {
		t.Fatalf("unexpected propose params: %#v", p)
	}
	tags, ok := p["tags"].([]string)
	if !ok || len(tags) != 2 || tags[0] != "quality" || tags[1] != "tests" {
		t.Fatalf("unexpected tags: %#v", p["tags"])
	}
}

func TestMemoryFormCtrlTTrustedManualDoesNotSwitchTabs(t *testing.T) {
	m := model{mode: memoryView}
	m.openNewMemoryForm()
	updated, cmd := m.handleKeyMsg(tea.KeyMsg{Type: tea.KeyCtrlT})
	if cmd != nil {
		t.Fatalf("ctrl-t in memory form should not return tab switch command: %v", cmd)
	}
	if updated.mode != memoryView || updated.memoryForm.Mode != memoryFormNew || !updated.memoryForm.TrustedManual {
		t.Fatalf("ctrl-t should toggle trusted manual inside form, mode=%v form=%+v", updated.mode, updated.memoryForm)
	}
}

func TestMemoryFormHelpDoesNotAdvertiseInlineEditProposal(t *testing.T) {
	m := model{}
	m.openNewMemoryForm()
	view := m.memoryFormView(100, 30)
	if strings.Contains(view, "proposal edit") || strings.Contains(view, "propose-edit") {
		t.Fatalf("new-memory form should not advertise stale inline edit/propose-edit help:\n%s", view)
	}
}

func TestMemoryTabEditUsesEditorInsteadOfInlineForm(t *testing.T) {
	m := model{mode: memoryView, memoryItems: []memoryRecord{{MemoryID: "mem-1", Version: 4, Type: "fact", Title: "Endpoint", Body: "Old body"}}}
	updated, cmd := m.updateMemoryManagement(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("e")})
	if cmd == nil {
		t.Fatalf("edit key should launch editor command")
	}
	if updated.memoryForm.Mode != memoryFormNone {
		t.Fatalf("edit key should not open inline memory form: %+v", updated.memoryForm)
	}
}

func TestMemoryFormSubmitSuccessClosesAndRefreshes(t *testing.T) {
	mock := &mockMemoryFormClient{}
	m := model{memoryForm: filledNewMemoryForm(), local: mock}
	msg := submitMemoryFormCmd(mock, m.memoryForm)().(memoryFormSubmitted)
	updatedModel, cmd := m.Update(msg)
	updated := updatedModel.(model)
	if msg.Err != nil || updated.memoryForm.Mode != memoryFormNone || !updated.memoryLoading || cmd == nil {
		t.Fatalf("success should close/loading/refresh msg=%+v updated=%+v cmd=%v", msg, updated.memoryForm, cmd)
	}
}

func TestMemoryFormSubmitErrorPreservesInput(t *testing.T) {
	mock := &mockMemoryFormClient{proposeErr: errors.New("boom")}
	m := model{memoryForm: filledNewMemoryForm(), local: mock}
	msg := submitMemoryFormCmd(mock, m.memoryForm)().(memoryFormSubmitted)
	updatedModel, cmd := m.Update(msg)
	updated := updatedModel.(model)
	if cmd != nil || updated.memoryForm.Mode != memoryFormNew || updated.memoryForm.Inputs[memoryFormTitle].Value() != "Run tests" || updated.memoryErr == nil {
		t.Fatalf("error should preserve form/input and set error, form=%+v err=%v cmd=%v", updated.memoryForm, updated.memoryErr, cmd)
	}
}

func filledNewMemoryForm() memoryFormState {
	m := model{ownName: "broccoli-agent"}
	m.openNewMemoryForm()
	m.memoryForm.Inputs[memoryFormType].SetValue("habit")
	m.memoryForm.Inputs[memoryFormTitle].SetValue("Run tests")
	m.memoryForm.Inputs[memoryFormBody].SetValue("Always run go test")
	m.memoryForm.Inputs[memoryFormAgent].SetValue("broccoli-agent")
	m.memoryForm.Inputs[memoryFormSubjectAgent].SetValue("broccoli-agent")
	m.memoryForm.Inputs[memoryFormTags].SetValue("quality, tests")
	m.memoryForm.Inputs[memoryFormSourceTask].SetValue("task-1")
	return m.memoryForm
}
