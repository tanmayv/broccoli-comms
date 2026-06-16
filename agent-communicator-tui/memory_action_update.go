package main

import tea "github.com/charmbracelet/bubbletea"

func (m model) confirmOrRunMemoryAction(mem memoryRecord, action string) (model, tea.Cmd) {
	if !memoryActionAllowed(mem, action) {
		return m, nil
	}
	if !m.memoryConfirmationMatches(mem, action) {
		m.memoryConfirm = memoryActionConfirmation{Action: action, MemoryID: mem.MemoryID}
		return m, nil
	}
	m.memoryLoading = true
	m = m.clearMemoryConfirmation()
	return m, memoryManagerActionCmd(m.local, mem, action)
}
