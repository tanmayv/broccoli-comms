package main

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

func (m model) handleMouse(msg tea.MouseMsg) (tea.Model, tea.Cmd) {
	event := tea.MouseEvent(msg)
	if event.Action != tea.MouseActionPress || event.Button != tea.MouseButtonLeft {
		return m, nil
	}
	if mode, ok := m.mouseSelectBottomTab(event.X, event.Y); ok {
		m.setMode(mode)
		if m.mode == memoryView {
			m.memoryLoading = true
		}
		if m.mode == tasksView {
			m.tasksLoading = true
		}
		m.selectLatestMessage()
		return m, m.loadActiveTabCmd()
	}
	if m.mode == simpleView && m.mouseSelectAgent(event.X, event.Y) {
		m.scrollSelectedAgentIntoView()
		m.selectLatestMessage()
		return m, m.reloadMessages()
	}
	if m.activeTabCanCompose() {
		if mode, ok := m.mouseInputMode(event.X, event.Y); ok {
			m.inputMode = mode
			return m, nil
		}
	}
	return m, nil
}

func (m model) bottomContentHeight() int {
	return lineCount(m.footer(max(1, m.width))) + lineCount(m.bottomTabBar(max(1, m.width)))
}

func (m model) mouseSelectBottomTab(x, y int) (viewMode, bool) {
	if m.width <= 0 || m.height <= 0 || x < 0 {
		return simpleView, false
	}
	tabsH := lineCount(m.bottomTabBar(m.width))
	if tabsH == 0 || y < m.height-tabsH || y >= m.height {
		return simpleView, false
	}
	_, hits := m.bottomTabLayout(m.width)
	for _, hit := range hits {
		if x >= hit.Start && x < hit.End {
			return hit.Mode, true
		}
	}
	return simpleView, false
}

func (m *model) mouseSelectAgent(x, y int) bool {
	if m.width < 70 {
		return false
	}
	chatW, rightW, _ := m.layoutWidths()
	bodyH := max(3, m.height-m.bottomContentHeight())
	if x < chatW || x >= chatW+rightW || y < 0 || y >= bodyH || len(m.rows) == 0 {
		return false
	}
	currentH := min(7, max(5, bodyH/4))
	listY := y - currentH - 3 // top padding + current agent panel + switcher panel header + filter
	if listY < 0 {
		return false
	}
	return m.selectAgentAtListLine(listY, panelInnerWidth(rightW))
}

func (m *model) selectAgentAtListLine(listY, width int) bool {
	if len(m.rows) == 0 {
		return false
	}
	items := make([]struct {
		Index int
		Row   agentRow
	}, 0, len(m.rows))
	localCount, remoteCount := 0, 0
	for i, row := range m.rows {
		if row.Scope == "remote" {
			remoteCount++
		} else {
			localCount++
		}
		items = append(items, struct {
			Index int
			Row   agentRow
		}{Index: i, Row: row})
	}
	if len(items) == 0 {
		return false
	}
	visible := max(1, m.sidebarAgentListHeight()/agentCardHeight)
	offset := min(max(0, m.agentOffset), max(0, len(items)-visible))
	end := min(len(items), offset+visible)
	line := 0
	lastGroup := ""
	for pos := offset; pos < end; pos++ {
		item := items[pos]
		group := "LOCAL"
		count := localCount
		if item.Row.Scope == "remote" {
			group = "REMOTE"
			count = remoteCount
		}
		heading := fmt.Sprintf("%s (%d)", group, count)
		if heading != lastGroup {
			if line > 0 {
				line++
			}
			line++
			lastGroup = heading
		}
		cardLines := agentCardHeight
		if listY >= line && listY < line+cardLines {
			m.selected = item.Index
			return true
		}
		line += cardLines
	}
	return false
}

func (m model) sidebarAgentListHeight() int {
	bodyH := max(3, m.height-m.bottomContentHeight())
	currentH := min(7, max(5, bodyH/4))
	statusH := 2
	listH := max(1, bodyH-currentH-statusH)
	return max(1, listH-5)
}

func (m model) mouseInputMode(x, y int) (inputMode, bool) {
	if !m.activeTabCanCompose() || m.width == 0 || m.height == 0 || y >= m.height-m.bottomContentHeight() {
		return inputModeMessage, false
	}
	chatW, _, _ := m.layoutWidths()
	panelX := 0
	panelW := chatW
	padX := 3
	if m.width < 70 {
		panelW = m.width
		padX = 1
	}
	innerX := x - panelX - padX
	innerW := max(1, panelW-(padX*2))
	if innerX < 0 || innerX >= innerW {
		return inputModeMessage, false
	}
	titleH := lineCount(titleStyle.Render(m.conversationTitle()))
	composerTop := 1 + titleH
	if m.width < 70 {
		composerTop = titleH
	}
	inputH := lineCount(m.composerInputBox(innerW))
	buttonTop := composerTop + inputH
	buttonH := lineCount(m.composerModeButtons(innerW))
	if y < buttonTop || y >= buttonTop+buttonH {
		return inputModeMessage, false
	}
	return inputModeButtonAtX(innerX)
}

func inputModeButtonAtX(x int) (inputMode, bool) {
	cursor := 0
	for i, button := range inputModeButtons() {
		buttonWidth := lipgloss.Width(modeTabStyle.Render(button.Label))
		if x >= cursor && x < cursor+buttonWidth {
			return button.Mode, true
		}
		cursor += buttonWidth
		if i < len(inputModeButtons())-1 {
			cursor++
		}
	}
	return inputModeMessage, false
}
