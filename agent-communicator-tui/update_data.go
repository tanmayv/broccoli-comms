package main

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

func (m model) handleMailboxEnsured(msg mailboxEnsured) (model, tea.Cmd) {
	m.err = msg.Err
	if msg.Err != nil {
		m.retryOperation = "mailbox"
		return m, nil
	}
	m.retryOperation = ""
	return m, initialLoadCmds(m)
}

func (m model) handleHealthLoaded(msg healthLoaded) (model, tea.Cmd) {
	m.healthErr = msg.Err
	if msg.Err == nil {
		m.health = msg.Info
	}
	return m, nil
}

func (m model) handleAgentsLoaded(msg agentsLoaded) (model, tea.Cmd) {
	m.agentListLoading = false
	m.err = msg.Err
	if msg.Err != nil {
		m.retryOperation = "agents"
		m.agentListStale = true
		return m, nil
	}
	m.retryOperation = ""
	m.agentListStale = false
	preserveKey := conversationKey(m.currentRow())
	m.allRows = filterOwnAgent(msg.Rows, m.ownName)
	m.applyAgentVisibility(preserveKey)
	if m.selected >= len(m.rows) {
		m.selected = max(0, len(m.rows)-1)
	}
	m.applyInitialHiddenForNoHistory()
	m.applyAgentVisibility(preserveKey)
	m.scrollSelectedAgentIntoView()
	if len(m.rows) > 0 {
		m.selectLatestMessage()
		return m, m.reloadMessages()
	}
	m.messages = nil
	return m, nil
}

func (m model) handleInboxLoaded(msg inboxLoaded) (model, tea.Cmd) {
	if msg.Err != nil {
		m.err = msg.Err
		m.retryOperation = "inbox"
		return m, nil
	}
	m.err = nil
	m.retryOperation = ""
	m.messages = m.mergeSentMessages(m.currentRow(), msg.Messages)
	m.clearUnread(m.currentRow())
	m.selectLatestMessage()
	return m, loadUnreadCounts(m.local, m.ownName)
}

func (m model) handleAllInboxLoaded(msg allInboxLoaded) (model, tea.Cmd) {
	if msg.Err != nil {
		m.err = msg.Err
		m.retryOperation = "all_inbox"
		return m, nil
	}
	m.err = nil
	m.retryOperation = ""
	m.allMessages = m.mergeAllMessages(msg.Messages)
	m.selectLatestMessage()
	return m, loadUnreadCounts(m.local, m.ownName)
}


func (m model) handleRestartRequested(msg restartRequested) (model, tea.Cmd) {
	m.err = msg.Err
	if msg.Err != nil {
		m.directInputStatus = "Restart request failed: " + msg.Err.Error()
		m.directInputStatusErr = true
	} else {
		m.directInputStatus = "Restart triggered for " + msg.Target
		m.directInputStatusErr = false
	}
	return m, tea.Tick(4*time.Second, func(time.Time) tea.Msg { return clearDirectInputStatusTick{} })
}

func (m model) handleMessageSent(msg messageSent) (model, tea.Cmd) {
	m.err = msg.Err
	if msg.Err != nil {
		m.composer = []rune(msg.Body)
		m.removeSentMessage(msg.Row, msg.Record.ID)
		m.refreshMergedMessages()
		return m, nil
	}
	m.outbox = appendOrReplaceOutbox(m.outbox, msg.Record)
	unhideCmd := m.unhideAgent(msg.Row)
	m.clearUnread(msg.Row)
	m.appendSentMessage(msg.Row, msg.Record)
	m.refreshMergedMessages()
	m.selectLatestMessage()
	if len(m.rows) > 0 {
		return m, tea.Batch(unhideCmd, m.reloadMessages())
	}
	return m, unhideCmd
}

func (m model) handleEventsLoaded(msg eventsLoaded) (model, tea.Cmd) {
	if msg.Err == nil {
		m.eventSeq = msg.Result.LastSeq
		m.markUnreadFromEvents(msg.Result)
		m.applyStatusEvents(msg.Result)
		m.appendSystemEvents(msg.Result)
		cmds := []tea.Cmd{waitEvents(m.local, m.eventSeq), loadUnreadCounts(m.local, m.ownName)}
		if len(m.rows) > 0 && shouldReloadForEvents(m.ownName, m.rows[m.selected], msg.Result) {
			cmds = append(cmds, m.reloadMessages())
		}
		return m, tea.Batch(cmds...)
	}
	return m, retryWaitEvents()
}

func (m model) handleUnreadCountsLoaded(msg unreadCountsLoaded) (model, tea.Cmd) {
	if msg.Err != nil {
		m.err = msg.Err
	} else {
		m.unreadCounts = msg.Counts
		if m.unreadCounts == nil {
			m.unreadCounts = map[string]int{}
		}
	}
	return m, nil
}

func (m model) handlePromptsLoaded(msg promptsLoaded) (model, tea.Cmd) {
	m.err = msg.Err
	if msg.Err == nil {
		m.prompts = msg.Prompts
		if m.promptSelected >= len(m.prompts) {
			m.promptSelected = max(0, len(m.prompts)-1)
		}
	}
	return m, nil
}

func (m model) handleHiddenAgentsLoaded(msg hiddenAgentsLoaded) (model, tea.Cmd) {
	if msg.Err != nil {
		m.err = msg.Err
	} else {
		m.hiddenAgents = msg.Hidden
		m.applyAgentVisibility("")
		m.scrollSelectedAgentIntoView()
	}
	return m, nil
}

func (m model) handleSavedMessagesLoaded(msg savedMessagesLoaded) (model, tea.Cmd) {
	if msg.Err != nil {
		m.err = msg.Err
	} else {
		m.savedMessages = msg.Records
		m.clampSavedSelected()
	}
	return m, nil
}

func (m model) handleOutboxLoaded(msg outboxLoaded) (model, tea.Cmd) {
	if msg.Err != nil {
		m.err = msg.Err
	} else {
		m.outbox = msg.Records
		m.applyInitialHiddenForNoHistory()
		m.applyAgentVisibility(conversationKey(m.currentRow()))
		m.refreshMergedMessages()
	}
	return m, nil
}

func (m model) handleConfigItemsLoaded(msg configItemsLoaded) (model, tea.Cmd) {
	m.err = msg.Err
	if msg.Err == nil {
		m.configItems = msg.Items
		if m.configSelected >= len(m.configItems) {
			m.configSelected = max(0, len(m.configItems)-1)
		}
	}
	return m, nil
}

func (m model) handlePromptEdited(msg promptEdited) (model, tea.Cmd) {
	m.err = msg.Err
	if msg.Err == nil && msg.Saved && m.canSendCurrent() && strings.TrimSpace(msg.Body) != "" {
		row, _ := m.currentSendTarget()
		record := makeOutboxRecord(m.ownName, row, msg.Body)
		unhideCmd := m.unhideAgent(row)
		m.clearUnread(row)
		m.appendSentMessage(row, record)
		m.refreshMergedMessages()
		m.selectLatestMessage()
		return m, tea.Batch(unhideCmd, sendOutboxRecord(m.local, m.ownName, row, record))
	}
	return m, nil
}
