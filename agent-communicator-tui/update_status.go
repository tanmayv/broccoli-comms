package main

import (
	"fmt"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

func (m model) handleDirectInputSent(msg directInputSent) (model, tea.Cmd) {
	if msg.Err != nil {
		m.composer = []rune(msg.Original)
		m.directInputStatus = fmt.Sprintf("Pane control failed for %s: %s", msg.Row.Name, msg.Err.Error())
		m.directInputStatusErr = true
	} else {
		m.directInputStatusErr = false
		if msg.Mode == "direct_text" {
			m.directInputStatus = fmt.Sprintf("Pane text sent to %s", msg.Row.Name)
		} else {
			m.directInputStatus = fmt.Sprintf("Pane key(s) sent to %s", msg.Row.Name)
		}
	}
	return m, tea.Tick(4*time.Second, func(time.Time) tea.Msg { return clearDirectInputStatusTick{} })
}

func (m model) handleClearDirectInputStatus() (model, tea.Cmd) {
	m.directInputStatus = ""
	m.directInputStatusErr = false
	return m, nil
}

func (m model) handlePaneCaptured(msg paneCaptured) (model, tea.Cmd) {
	if msg.Err != nil {
		m.paneCaptureStatus = fmt.Sprintf("Failed to capture %s: %s", msg.Target, msg.Err.Error())
	} else {
		m.paneCaptureStatus = fmt.Sprintf("Pane snapshot for %s delivered successfully!", msg.Target)
	}
	return m, tea.Tick(4*time.Second, func(time.Time) tea.Msg {
		return clearPaneCaptureStatusTick{}
	})
}

func (m model) handleClearPaneCaptureStatus() (model, tea.Cmd) {
	m.paneCaptureStatus = ""
	return m, nil
}

func (m model) handleRefreshTick() (model, tea.Cmd) {
	m.agentListLoading = true
	return m, tea.Batch(loadHealth(m.local), loadAgents(m.local), loadOutboxCmd(), loadUnreadCounts(m.local, m.ownName), tickRefresh(), tickAgentListSpinner())
}

func (m model) handleCursorBlinkTick() (model, tea.Cmd) {
	m.cursorHidden = !m.cursorHidden
	return m, tickCursorBlink()
}

func (m model) handleAgentListSpinnerTick() (model, tea.Cmd) {
	if m.agentListLoading {
		m.agentListFrame++
		return m, tickAgentListSpinner()
	}
	return m, nil
}
