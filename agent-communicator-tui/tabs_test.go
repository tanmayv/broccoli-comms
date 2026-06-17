package main

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestTabBarRendersActiveMode(t *testing.T) {
	m := model{width: 160, mode: swarmView, health: tracker.TrackerInfo{Build: tracker.BuildInfo{Display: "0.1.0+abc1234"}}}
	oldVersion := version
	version = "0.1.0+ui"
	defer func() { version = oldVersion }()
	bar := m.bottomTabBar(m.width)
	if !strings.Contains(bar, "ui 0.1.0+ui") || !strings.Contains(bar, "tracker 0.1.0+abc1234") {
		t.Fatalf("tab bar missing version diagnostics: %q", bar)
	}
	for _, want := range []string{"Simple Chat", "Swarm Mode", "Saved Messages", "Memory Management", "Tasks"} {
		if !strings.Contains(bar, want) {
			t.Fatalf("tab bar missing %q: %q", want, bar)
		}
	}
	if strings.Contains(bar, "Advanced Chat") {
		t.Fatalf("tab bar should not render Advanced Chat: %q", bar)
	}
	swarmBar := bar
	m.mode = simpleView
	if simpleBar := m.bottomTabBar(m.width); simpleBar == swarmBar {
		t.Fatalf("active tab styling did not change between modes: %q", simpleBar)
	}
}

func TestAppTabSwitchingWithCtrlTAndCtrlY(t *testing.T) {
	m := model{local: &fakeLocal{}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyCtrlT})
	m = updated.(model)
	if m.mode != swarmView {
		t.Fatalf("ctrl-t from simple mode = %v, want swarm", m.mode)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlT})
	m = updated.(model)
	if m.mode != savedView {
		t.Fatalf("second ctrl-t mode = %v, want saved", m.mode)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlT})
	m = updated.(model)
	if m.mode != memoryView {
		t.Fatalf("third ctrl-t mode = %v, want memory", m.mode)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlT})
	m = updated.(model)
	if m.mode != tasksView {
		t.Fatalf("fourth ctrl-t mode = %v, want tasks", m.mode)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlT})
	m = updated.(model)
	if m.mode != simpleView {
		t.Fatalf("fifth ctrl-t mode = %v, want simple", m.mode)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlY})
	m = updated.(model)
	if m.mode != tasksView {
		t.Fatalf("ctrl-y from simple mode = %v, want tasks", m.mode)
	}
}

func TestCtrlTRemainsBackwardCompatible(t *testing.T) {
	m := model{local: &fakeLocal{}}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyCtrlT})
	m = updated.(model)
	if m.mode != swarmView {
		t.Fatalf("ctrl-t mode = %v, want swarm", m.mode)
	}
}

func TestMouseClickTabSwitchesMode(t *testing.T) {
	m := model{width: 120, height: 24, local: &fakeLocal{}}
	_, hits := m.bottomTabLayout(m.width)
	var x int
	for _, hit := range hits {
		if hit.Mode == savedView {
			x = hit.Start
			break
		}
	}
	updated, _ := m.handleMouse(tea.MouseMsg{X: x, Y: m.height - 1, Action: tea.MouseActionPress, Button: tea.MouseButtonLeft})
	m = updated.(model)
	if m.mode != savedView {
		t.Fatalf("mouse tab mode = %v, want saved", m.mode)
	}
}

func TestAppTabSwitchReloadsExpectedMessages(t *testing.T) {
	local := &fakeLocal{inbox: []tracker.Message{{Sender: "alpha", Body: "hello"}}}
	m := model{ownName: "agent-communicator", rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlT})
	m = updated.(model)
	if m.mode != swarmView || cmd == nil {
		t.Fatalf("swarm tab mode=%v cmd=%v", m.mode, cmd)
	}
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyCtrlT})
	m = updated.(model)
	if m.mode != savedView || cmd != nil {
		t.Fatalf("saved tab mode=%v cmd=%v, want nil reload", m.mode, cmd)
	}
}

func TestTabsAreDataDriven(t *testing.T) {
	oldTabs := registeredAppTabs
	defer func() { registeredAppTabs = oldTabs }()
	fakeMode := viewMode(99)
	registeredAppTabs = append(append([]appTab(nil), registeredAppTabs...), appTab{ID: "fake", Mode: fakeMode, Label: "Fake Tab", ShortLabel: "Fake"})
	m := model{mode: tasksView, width: 160}
	m.selectTab(1)
	if m.mode != fakeMode {
		t.Fatalf("data-driven tab cycle mode=%v want fake", m.mode)
	}
	if !strings.Contains(m.bottomTabBar(m.width), "Fake Tab") {
		t.Fatalf("tab bar did not render registered fake tab: %q", m.bottomTabBar(m.width))
	}
}

func TestBottomTabBarCompactsWhenManyTabsOrNarrowWidth(t *testing.T) {
	oldTabs := registeredAppTabs
	defer func() { registeredAppTabs = oldTabs }()
	registeredAppTabs = append([]appTab(nil), registeredAppTabs...)
	for i := 0; i < 8; i++ {
		registeredAppTabs = append(registeredAppTabs, appTab{ID: "extra", Mode: viewMode(100 + i), Label: "Extra Long Future Tab", ShortLabel: "Extra"})
	}
	m := model{width: 42}
	bar := m.bottomTabBar(m.width)
	if got := lipgloss.Width(bar); got > m.width {
		t.Fatalf("tab bar width=%d want <= %d: %q", got, m.width, bar)
	}
	if strings.Contains(bar, "Simple Chat") || !strings.Contains(bar, "Simple") {
		t.Fatalf("narrow tab bar should use compact labels: %q", bar)
	}
	for _, width := range []int{1, 5, 10} {
		m.width = width
		bar := m.bottomTabBar(width)
		if got := lipgloss.Width(bar); got > width {
			t.Fatalf("tiny tab bar width=%d want <= %d: %q", got, width, bar)
		}
	}
}

func TestCanComposeMetadataControlsComposerBehavior(t *testing.T) {
	oldTabs := registeredAppTabs
	defer func() { registeredAppTabs = oldTabs }()
	readOnlyMode := viewMode(98)
	registeredAppTabs = append(append([]appTab(nil), registeredAppTabs...), appTab{ID: "readonly", Mode: readOnlyMode, Label: "Read Only", ShortLabel: "Read", CanCompose: false})
	m := model{mode: readOnlyMode, width: 120, height: 24, rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: &fakeLocal{}, inputMode: inputModeMessage}
	if m.activeTabCanCompose() {
		t.Fatal("read-only tab should not allow compose")
	}
	panel := m.conversationPanel(80, 20)
	if !strings.Contains(panel, "Read Only") || strings.Contains(panel, "/msg") {
		t.Fatalf("read-only tab should show label instead of composer controls:\n%s", panel)
	}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("hello")})
	m = updated.(model)
	if cmd != nil || string(m.composer) != "" {
		t.Fatalf("typing in read-only tab changed composer=%q cmd=%v", string(m.composer), cmd)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyF2})
	m = updated.(model)
	if m.inputMode != inputModeMessage {
		t.Fatalf("F2 changed inputMode in read-only tab: %v", m.inputMode)
	}
}

func TestReadOnlyTabDoesNotSubmitStaleComposer(t *testing.T) {
	local := &fakeLocal{}
	m := model{mode: memoryView, composer: []rune("stale draft"), rows: []agentRow{{Name: "alpha", Scope: "local"}}, local: local}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(model)
	if cmd != nil {
		t.Fatalf("read-only tab returned submit command: %v", cmd)
	}
	if string(m.composer) != "stale draft" {
		t.Fatalf("read-only submit changed composer to %q", string(m.composer))
	}
	if local.sentTo != "" || local.sentBody != "" {
		t.Fatalf("read-only submit sent target/body = %q/%q", local.sentTo, local.sentBody)
	}
}
