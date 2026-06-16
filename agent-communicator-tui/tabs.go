package main

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

type appTab struct {
	ID         string
	Mode       viewMode
	Label      string
	ShortLabel string
	Help       string
	CanCompose bool
	Load       func(model) tea.Cmd
}

var registeredAppTabs = []appTab{
	{ID: "simple", Mode: simpleView, Label: "Simple Chat", ShortLabel: "Simple", Help: "focused conversation", CanCompose: true, Load: loadSimpleTabMessages},
	{ID: "swarm", Mode: swarmView, Label: "Swarm Mode", ShortLabel: "Swarm", Help: "coordinate agent groups", CanCompose: true, Load: loadSwarmTab},
	{ID: "saved", Mode: savedView, Label: "Saved Messages", ShortLabel: "Saved", Help: "starred messages", CanCompose: false, Load: loadSavedTabMessages},
	{ID: "memory", Mode: memoryView, Label: "Memory Management", ShortLabel: "Memory", Help: "review and maintain durable memory", CanCompose: false, Load: loadMemoryTab},
	{ID: "tasks", Mode: tasksView, Label: "Tasks", ShortLabel: "Tasks", Help: "manage task chains", CanCompose: false, Load: loadTasksTab},
}

func loadSimpleTabMessages(m model) tea.Cmd {
	return loadInbox(m.local, m.ownName, m.currentRow())
}

func loadSwarmTab(m model) tea.Cmd {
	return tea.Batch(loadSwarms(m.local), loadSelectedSwarmTimeline(m.local, m.selectedSwarmName()))
}

func loadSavedTabMessages(model) tea.Cmd { return nil }

func loadMemoryTab(m model) tea.Cmd { return loadMemoryApprovalsCmd(m.local) }

func appTabs() []appTab {
	return append([]appTab(nil), registeredAppTabs...)
}

func tabForMode(mode viewMode) (appTab, bool) {
	for _, tab := range appTabs() {
		if tab.Mode == mode {
			return tab, true
		}
	}
	return appTab{}, false
}

func viewModeLabel(mode viewMode, compact bool) string {
	if tab, ok := tabForMode(mode); ok {
		if compact && tab.ShortLabel != "" {
			return tab.ShortLabel
		}
		return tab.Label
	}
	if mode == advancedView {
		return "Advanced Chat"
	}
	return "Unknown"
}

func (m model) activeTabIndex() int {
	tabs := appTabs()
	for i, tab := range tabs {
		if tab.Mode == m.mode {
			return i
		}
	}
	return 0
}

func (m model) activeTab() appTab {
	tabs := appTabs()
	if len(tabs) == 0 {
		return appTab{}
	}
	idx := m.activeTabIndex()
	if idx < 0 || idx >= len(tabs) {
		idx = 0
	}
	return tabs[idx]
}

func (m model) activeTabCanCompose() bool {
	tab := m.activeTab()
	if !tab.CanCompose {
		return false
	}
	return true
}

func (m *model) setMode(mode viewMode) {
	m.mode = mode
	m.messageOffset = 0
	if mode == savedView {
		m.clampSavedSelected()
	}
	messages := m.displayOrderedMessages()
	m.messageSelected = selectableMessageIndex(messages, clampSelectedMessage(m.messageSelected, len(messages)), 1)
}

func (m *model) selectTab(delta int) {
	tabs := appTabs()
	if len(tabs) == 0 {
		return
	}
	idx := m.activeTabIndex()
	idx = (idx + delta) % len(tabs)
	if idx < 0 {
		idx += len(tabs)
	}
	m.setMode(tabs[idx].Mode)
}

type bottomTabHit struct {
	Mode  viewMode
	Start int
	End   int
	Text  string
}

var activeTopTabStyle = lipgloss.NewStyle().Foreground(colors.SelectedFg).Background(colors.SelectedBg).Bold(true)
var inactiveTopTabStyle = lipgloss.NewStyle().Foreground(colors.Muted).Background(colors.PanelBg)

func (m model) bottomTabBar(width int) string {
	line, _ := m.bottomTabLayout(width)
	return line
}

func (m model) bottomTabLayout(width int) (string, []bottomTabHit) {
	if width <= 0 {
		return "", nil
	}
	tabs := appTabs()
	compact := width < 70
	var b strings.Builder
	hits := make([]bottomTabHit, 0, len(tabs))
	cursor := 0
	versionText := mutedStyle.Render(fitTabText(m.versionStatusLine(), max(1, min(width, 34))))
	versionW := lipgloss.Width(versionText)
	if versionW > 0 && versionW < width {
		b.WriteString(versionText)
		cursor = versionW
	}
	for _, tab := range tabs {
		text := tabDisplayText(tab, compact)
		rendered := renderBottomTab(tab, text, tab.Mode == m.mode)
		segW := lipgloss.Width(rendered)
		sepW := 0
		if cursor > 0 {
			sepW = 1
		}
		if cursor+sepW+segW > width {
			if cursor == 0 {
				rendered = renderBottomTabFitted(tab, text, tab.Mode == m.mode, width)
				segW = lipgloss.Width(rendered)
				b.WriteString(rendered)
				hits = append(hits, bottomTabHit{Mode: tab.Mode, Start: 0, End: segW, Text: rendered})
			}
			break
		}
		if sepW > 0 {
			b.WriteRune(' ')
			cursor++
		}
		start := cursor
		b.WriteString(rendered)
		cursor += segW
		hits = append(hits, bottomTabHit{Mode: tab.Mode, Start: start, End: cursor, Text: rendered})
	}
	line := b.String()
	if lipgloss.Width(line) < width {
		line = padStyledLine(line, width, colors.BaseBg)
	}
	return line, hits
}

func tabDisplayText(tab appTab, compact bool) string {
	label := tab.Label
	if compact && tab.ShortLabel != "" {
		label = tab.ShortLabel
	}
	return "[ " + label + " ]"
}

func renderBottomTab(tab appTab, text string, active bool) string {
	return styleBottomTab(tabPlainText(text, active), active)
}

func renderBottomTabFitted(tab appTab, text string, active bool, width int) string {
	return styleBottomTab(fitTabText(tabPlainText(text, active), width), active)
}

func tabPlainText(text string, active bool) string {
	if active {
		return "▸ " + text
	}
	return "  " + text
}

func styleBottomTab(text string, active bool) string {
	if active {
		return activeTopTabStyle.Render(text)
	}
	return inactiveTopTabStyle.Render(text)
}

func fitTabText(text string, width int) string {
	if width <= 0 || lipgloss.Width(text) <= width {
		return text
	}
	if width == 1 {
		return "…"
	}
	return truncateCells(text, width-1) + "…"
}
