package main

import (
	"os"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestSavedMessagesPersist(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	records := []savedMessageRecord{{ID: "1", AgentName: "alice", Sender: "alice", Body: "hi"}}
	if err := writeSavedMessages(records); err != nil {
		t.Fatal(err)
	}
	loaded, err := loadSavedMessages()
	if err != nil || len(loaded) != 1 || loaded[0].Body != "hi" {
		t.Fatalf("loaded=%+v err=%v", loaded, err)
	}
	if _, err := os.Stat(savedMessagesPath()); err != nil {
		t.Fatal(err)
	}
}

func TestToggleSaveSelectedMessageSavesAndUnsaves(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	m := model{messages: []tracker.Message{{Sender: "alice", Body: "hi", MessageID: "m1"}}}
	cmd := m.toggleSaveSelectedMessage()
	if cmd == nil || len(m.savedMessages) != 1 || m.savedMessages[0].AgentName != "alice" {
		t.Fatalf("saved=%+v cmd=%v", m.savedMessages, cmd)
	}
	cmd = m.toggleSaveSelectedMessage()
	if cmd == nil || len(m.savedMessages) != 0 {
		t.Fatalf("saved=%+v cmd=%v", m.savedMessages, cmd)
	}
}

func TestOutgoingSavedMessagesGroupUnderYou(t *testing.T) {
	msg := tracker.Message{Sender: "You", Body: "out", MessageID: "m1"}
	rec := makeSavedRecord(msg)
	if rec.AgentName != "You" {
		t.Fatalf("agent=%q", rec.AgentName)
	}
}

func TestSavedViewRowsAndMessages(t *testing.T) {
	m := model{mode: savedView, savedMessages: []savedMessageRecord{{ID: "1", AgentName: "You", Sender: "You", Body: "mine"}, {ID: "2", AgentName: "alice", Sender: "alice", Body: "theirs"}}}
	rows := m.savedRows()
	if len(rows) != 2 || rows[0].Name != "You" {
		t.Fatalf("rows=%+v", rows)
	}
	if got := m.displayOrderedMessages(); len(got) != 1 || got[0].Body != "mine" {
		t.Fatalf("messages=%+v", got)
	}
	m.savedSelected = 1
	if got := m.displayOrderedMessages(); len(got) != 1 || got[0].Body != "theirs" {
		t.Fatalf("messages=%+v", got)
	}
}

func TestCtrlTCyclesToSavedView(t *testing.T) {
	m := model{}
	m.toggleMode()
	if m.mode != savedView {
		t.Fatalf("mode=%v", m.mode)
	}
}

func TestSavedViewRendersWithoutComposer(t *testing.T) {
	m := model{mode: savedView, width: 100, height: 20, savedMessages: []savedMessageRecord{{ID: "1", AgentName: "alice", Sender: "alice", Body: "saved body"}}}
	view := m.View()
	if !strings.Contains(view, "Saved Messages") || !strings.Contains(view, "saved body") || strings.Contains(view, "type message") {
		t.Fatalf("view=\n%s", view)
	}
}

func TestSavedMessagesHighlightedInConversation(t *testing.T) {
	msg := tracker.Message{Sender: "alice", Body: "hi", MessageID: "m1", Timestamp: "2026-05-20T10:24:51Z"}
	m := model{messages: []tracker.Message{msg}, savedMessages: []savedMessageRecord{makeSavedRecord(msg)}}
	view := strings.Join(m.messageLinesForWidth(80), "\n")
	if !strings.Contains(view, "★ alice") || !strings.Contains(view, "15:54 ★") {
		t.Fatalf("saved markers missing:\n%s", view)
	}
	if got := m.messageBorderColor(msg, "alice"); got != colors.Saved {
		t.Fatalf("border color=%s want %s", got, colors.Saved)
	}
}

func TestCtrlFInUpdateSavesMessage(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	m := model{messages: []tracker.Message{{Sender: "alice", Body: "hi", MessageID: "m1"}}}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlF})
	m = updated.(model)
	if cmd == nil || len(m.savedMessages) != 1 {
		t.Fatalf("saved=%+v cmd=%v", m.savedMessages, cmd)
	}
}
