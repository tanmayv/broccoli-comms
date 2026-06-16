package main

import tea "github.com/charmbracelet/bubbletea"

func (m model) updateMemoryManagement(msg tea.KeyMsg) (model, tea.Cmd) {
	if m.memoryFormActive() {
		return m.updateMemoryForm(msg)
	}
	visibleRows := memoryVisibleRowsForHeight(m.memoryListHeight())
	if m.memorySearchFocused {
		switch msg.Type {
		case tea.KeyEsc:
			m.memorySearchFocused = false
			m = m.clearMemoryConfirmation()
			return m, nil
		case tea.KeyBackspace:
			if len(m.memoryQuery) > 0 {
				previousID := m.currentMemorySelectionID()
				m.memoryQuery = m.memoryQuery[:len(m.memoryQuery)-1]
				m.preserveMemorySelection(previousID)
				m = m.clearMemoryConfirmation()
			}
			return m, nil
		case tea.KeyCtrlW:
			previousID := m.currentMemorySelectionID()
			m.memoryQuery = deletePreviousWord(m.memoryQuery)
			m.preserveMemorySelection(previousID)
			m = m.clearMemoryConfirmation()
			return m, nil
		case tea.KeyRunes:
			previousID := m.currentMemorySelectionID()
			m.memoryQuery = append(m.memoryQuery, msg.Runes...)
			m.preserveMemorySelection(previousID)
			m = m.clearMemoryConfirmation()
			return m, nil
		case tea.KeySpace:
			previousID := m.currentMemorySelectionID()
			m.memoryQuery = append(m.memoryQuery, ' ')
			m.preserveMemorySelection(previousID)
			m = m.clearMemoryConfirmation()
			return m, nil
		}
	}
	switch msg.Type {
	case tea.KeyUp, tea.KeyCtrlP:
		m.moveMemorySelection(-1, visibleRows)
		m = m.clearMemoryConfirmation()
		return m, nil
	case tea.KeyDown, tea.KeyCtrlN:
		m.moveMemorySelection(1, visibleRows)
		m = m.clearMemoryConfirmation()
		return m, nil
	case tea.KeyCtrlU, tea.KeyPgUp:
		m.moveMemorySelection(-visibleRows, visibleRows)
		m = m.clearMemoryConfirmation()
		return m, nil
	case tea.KeyCtrlD, tea.KeyPgDown:
		m.moveMemorySelection(visibleRows, visibleRows)
		m = m.clearMemoryConfirmation()
		return m, nil
	case tea.KeyEsc:
		m.memorySearchFocused = false
		m = m.clearMemoryConfirmation()
		return m, nil
	case tea.KeyRunes:
		switch string(msg.Runes) {
		case "/":
			m.memorySearchFocused = true
			m = m.clearMemoryConfirmation()
			return m, nil
		case "s":
			m.cycleMemoryStatusFilter()
			m = m.clearMemoryConfirmation()
			return m, nil
		case "t":
			m.cycleMemoryTypeFilter()
			m = m.clearMemoryConfirmation()
			return m, nil
		case "g":
			m.cycleMemoryAgentFilter()
			m = m.clearMemoryConfirmation()
			return m, nil
		case "n":
			m = m.clearMemoryConfirmation()
			m.openNewMemoryForm()
			return m, nil
		case "e":
			if mem, ok := m.selectedMemoryRecord(); ok {
				return m, editMemoryInEditor(m.local, mem)
			}
			return m, nil
		case "a":
			if mem, ok := m.selectedMemoryRecord(); ok && memoryActionAllowed(mem, "approve") {
				m.memoryLoading = true
				m = m.clearMemoryConfirmation()
				return m, memoryManagerActionCmd(m.local, mem, "approve")
			}
			return m, nil
		case "d":
			if mem, ok := m.selectedMemoryRecord(); ok {
				if action, ok := memoryActionForDelete(mem); ok {
					return m.confirmOrRunMemoryAction(mem, action)
				}
			}
			return m, nil
		case "R":
			if mem, ok := m.selectedMemoryRecord(); ok && memoryActionAllowed(mem, "rollback") {
				return m.confirmOrRunMemoryAction(mem, "rollback")
			}
			return m, nil
		case "r":
			m.memoryLoading = true
			m = m.clearMemoryConfirmation()
			return m, loadMemoryApprovalsCmd(m.local)
		}
	}
	return m, nil
}
