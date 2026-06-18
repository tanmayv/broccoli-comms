package main

import (
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

func (m model) handleKeyMsg(msg tea.KeyMsg) (model, tea.Cmd) {
	keyStart := time.Now()
	debugLogf("key start type=%v runes=%d", msg.Type, len(msg.Runes))
	defer func() {
		debugLogf("key end type=%v duration=%s composer_len=%d", msg.Type, time.Since(keyStart), len(m.composer))
	}()
	if m.commandPalette.Open {
		return m.updateCommandPalette(msg)
	}
	if m.showingSaveForm {
		return m.updateSaveForm(msg)
	}
	if m.showingRunAgentForm {
		return m.handleRunAgentFormKey(msg)
	}
	if m.showingPromptMenu {
		return m.handlePromptMenuKey(msg)
	}
	if m.showingConfigMenu {
		return m.handleConfigMenuKey(msg)
	}
	if msg.Alt && len(msg.Runes) == 1 {
		r := msg.Runes[0]
		var targetMode viewMode
		found := false
		if r >= '1' && r <= '6' {
			idx := int(r - '1')
			tabs := appTabs()
			if idx < len(tabs) {
				targetMode = tabs[idx].Mode
				found = true
			}
		} else {
			switch r {
			case 'h', 'H':
				targetMode = homeView
				found = true
			case 'c', 'C':
				targetMode = simpleView
				found = true
			case 's', 'S':
				targetMode = savedView
				found = true
			case 'm', 'M':
				targetMode = memoryView
				found = true
			case 't', 'T':
				targetMode = tasksView
				found = true
			case 'l', 'L':
				targetMode = changelogView
				found = true
			}
		}
		if found {
			m.setMode(targetMode)
			if m.mode == memoryView {
				m.memoryLoading = true
			}
			if m.mode == tasksView {
				m.tasksLoading = true
			}
			m.selectLatestMessage()
			return m, m.loadActiveTabCmd()
		}
	}
	if isCommandPaletteOpenKey(msg) {
		m.commandPalette.Open = true
		m.commandPalette.Query = nil
		m.commandPalette.Selected = 0
		m.commandPalette.Offset = 0
		return m, nil
	}
	if m.mode == tasksView {
		if m.tasksPalette.Open {
			return m.updateTaskCommandPalette(msg)
		}
		if m.tasksForm.Active {
			switch msg.Type {
			case tea.KeyEsc:
				m.tasksForm = taskChainFormState{}
				return m, nil
			case tea.KeyEnter:
				title, agent, priority, depends, err := parseTaskChainForm(m.tasksForm.Text(), m.tasksForm, m.currentRow().Name)
				if err != nil {
					m.tasksForm.Err = err
					return m, nil
				}
				m.tasksForm = taskChainFormState{}
				m.tasksLoading = true
				return m, createTaskInChainCmd(title, agent, priority, depends)
			case tea.KeyTab:
				return m.completeTaskFormToken(), nil
			case tea.KeyBackspace:
				if len(m.tasksForm.Input) > 0 {
					m.tasksForm.Input = m.tasksForm.Input[:len(m.tasksForm.Input)-1]
				}
				return m, nil
			case tea.KeySpace:
				m.tasksForm.Input = append(m.tasksForm.Input, ' ')
				return m, nil
			case tea.KeyRunes:
				m.tasksForm.Input = append(m.tasksForm.Input, msg.Runes...)
				return m, nil
			}
			return m, nil
		}
		switch msg.Type {
		case tea.KeyCtrlC, tea.KeyCtrlQ:
			return m, tea.Quit
		case tea.KeyCtrlK:
			m.tasksAgentFilterFocused = false
			m.tasksPalette = taskCommandPaletteState{Open: true}
			return m, nil
		case tea.KeyTab:
			m.cycleTaskAgentFilter()
			return m, nil
		case tea.KeyCtrlP:
			if len(m.rows) > 0 {
				m.selectNextInSection(-1)
				m.scrollSelectedAgentIntoView()
				m.tasksSelected = 0
				m.tasksOffset = 0
				m.tasksChainFocused = false
				m.tasksChainSelected = 0
				m.tasksLoading = true
				return m, loadTasksCmd()
			}
		case tea.KeyCtrlN:
			if len(m.rows) > 0 {
				m.selectNextInSection(1)
				m.scrollSelectedAgentIntoView()
				m.tasksSelected = 0
				m.tasksOffset = 0
				m.tasksChainFocused = false
				m.tasksChainSelected = 0
				m.tasksLoading = true
				return m, loadTasksCmd()
			}
		case tea.KeyEsc:
			if m.tasksAgentFilterFocused {
				m.tasksAgentFilterFocused = false
				return m, nil
			}
			m.tasksConfirm = taskActionConfirmation{}
			if m.tasksChainFocused {
				m.tasksChainFocused = false
				m.tasksSelected = m.tasksChainSelected
				m.tasksOffset = m.tasksSelected
			}
			return m, nil
		case tea.KeyEnter:
			if m.tasksAgentFilterFocused {
				m.tasksAgentFilterFocused = false
				return m, nil
			}
			if m.tasksConfirm.Active() {
				if task, ok := m.selectedTaskRecord(); ok && m.taskConfirmationMatches(task, m.tasksConfirm.Action) {
					return m.confirmOrRunTaskAction(task, m.tasksConfirm.Action)
				}
				m.tasksConfirm = taskActionConfirmation{}
				m.directInputStatus = "Task confirmation expired; reopen the command palette"
				m.directInputStatusErr = true
				return m, nil
			}
			if !m.tasksChainFocused && len(m.taskData().Chains) > 0 {
				m.tasksChainFocused = true
				m.tasksChainSelected = m.tasksSelected
				m.tasksSelected = 0
				m.tasksOffset = 0
				return m, nil
			}
			m.tasksPalette = taskCommandPaletteState{Open: true}
			return m, nil
		case tea.KeyBackspace:
			if m.tasksAgentFilterFocused && len(m.tasksAgentFilter) > 0 {
				m.tasksAgentFilter = m.tasksAgentFilter[:len(m.tasksAgentFilter)-1]
				m.tasksSelected = 0
				m.tasksOffset = 0
				m.tasksChainSelected = 0
				m.tasksChainFocused = false
			}
			return m, nil
		case tea.KeySpace:
			if m.tasksAgentFilterFocused {
				m.tasksAgentFilter = append(m.tasksAgentFilter, ' ')
				return m, nil
			}
		case tea.KeyUp:
			m.moveTaskSelection(-1)
			return m, nil
		case tea.KeyDown:
			m.moveTaskSelection(1)
			return m, nil
		case tea.KeyCtrlT:
			m.toggleMode()
			if m.mode == memoryView {
				m.memoryLoading = true
			}
			if m.mode == tasksView {
				m.tasksLoading = true
			}
			m.selectLatestMessage()
			return m, m.loadActiveTabCmd()
		case tea.KeyCtrlY:
			m.selectTab(-1)
			if m.mode == memoryView {
				m.memoryLoading = true
			}
			if m.mode == tasksView {
				m.tasksLoading = true
			}
			m.selectLatestMessage()
			return m, m.loadActiveTabCmd()
		case tea.KeyRunes:
			if m.tasksAgentFilterFocused {
				m.tasksAgentFilter = append(m.tasksAgentFilter, msg.Runes...)
				m.tasksSelected = 0
				m.tasksOffset = 0
				m.tasksChainSelected = 0
				m.tasksChainFocused = false
				return m, nil
			}
			if len(msg.Runes) != 1 {
				return m, nil
			}
			switch msg.Runes[0] {
			case 'r', 'R':
				m.tasksLoading = true
				m.tasksErr = nil
				return m, loadTasksCmd()
			case '/', 'f':
				m.tasksAgentFilterFocused = true
				return m, nil
			case 'j':
				m.moveTaskSelection(1)
				return m, nil
			case 'k':
				m.moveTaskSelection(-1)
				return m, nil
			}
		}
		return m, nil
	}
	if m.mode == memoryView {
		if m.memoryFormActive() {
			return m.updateMemoryManagement(msg)
		}
		switch msg.Type {
		case tea.KeyCtrlC, tea.KeyCtrlQ:
			return m, tea.Quit
		case tea.KeyCtrlT:
			m.toggleMode()
			if m.mode == memoryView {
				m.memoryLoading = true
			}
			if m.mode == tasksView {
				m.tasksLoading = true
			}
			m.selectLatestMessage()
			return m, m.loadActiveTabCmd()
		case tea.KeyCtrlY:
			m.selectTab(-1)
			if m.mode == memoryView {
				m.memoryLoading = true
			}
			if m.mode == tasksView {
				m.tasksLoading = true
			}
			m.selectLatestMessage()
			return m, m.loadActiveTabCmd()
		}
		return m.updateMemoryManagement(msg)
	}
	switch msg.Type {
	case tea.KeyCtrlC, tea.KeyCtrlQ:
		return m, tea.Quit
	case tea.KeyCtrlR:
		m.showingConfigMenu = true
		m.configSelected = 0
		m.configQuery = nil
		return m, loadConfigItemsCmd(m.local)
	case tea.KeyCtrlO:
		m.showingPromptMenu = true
		m.promptSelected = 0
		return m, loadPromptsCmd()
	case tea.KeyCtrlS:
		m.initSaveForm()
		return m, nil
	case tea.KeyCtrlT:
		m.toggleMode()
		if m.mode == memoryView {
			m.memoryLoading = true
		}
		if m.mode == tasksView {
			m.tasksLoading = true
		}
		m.selectLatestMessage()
		return m, m.loadActiveTabCmd()
	case tea.KeyCtrlY:
		m.selectTab(-1)
		if m.mode == memoryView {
			m.memoryLoading = true
		}
		if m.mode == tasksView {
			m.tasksLoading = true
		}
		m.selectLatestMessage()
		return m, m.loadActiveTabCmd()
	case tea.KeyCtrlG:
		if len(m.rows) > 0 {
			m.toggleAgentSection()
			m.scrollSelectedAgentIntoView()
			m.selectLatestMessage()
			return m, m.reloadMessages()
		}
	case tea.KeyCtrlX:
		if len(m.rows) > 0 && m.selected >= 0 && m.selected < len(m.rows) {
			row := m.rows[m.selected]
			targetAddress := rowTarget(row)
			m.paneCaptureStatus = fmt.Sprintf("Capturing pane snapshot for %s...", row.Name)
			return m, requestPaneCaptureCmd(targetAddress)
		}
	case tea.KeyCtrlF:
		return m, m.toggleSaveSelectedMessage()
	case tea.KeyCtrlP:
		debugLogf("KeyCtrlP matched: mode=%v rows_len=%d", m.mode, len(m.rows))
		if m.mode == savedView {
			m.selectSavedRow(-1)
			m.selectLatestMessage()
			return m, nil
		}
		if len(m.rows) > 0 {
			m.selectNextInSection(-1)
			m.scrollSelectedAgentIntoView()
			m.selectLatestMessage()
			if m.mode == homeView || m.mode == changelogView {
				m.mode = simpleView
			}
			return m, m.reloadMessages()
		}
	case tea.KeyCtrlN:
		debugLogf("KeyCtrlN matched: mode=%v rows_len=%d", m.mode, len(m.rows))
		if m.mode == savedView {
			m.selectSavedRow(1)
			m.selectLatestMessage()
			return m, nil
		}
		if len(m.rows) > 0 {
			m.selectNextInSection(1)
			m.scrollSelectedAgentIntoView()
			m.selectLatestMessage()
			if m.mode == homeView || m.mode == changelogView {
				m.mode = simpleView
			}
			return m, m.reloadMessages()
		}
	case tea.KeyTab, tea.KeyShiftTab:
		if len(m.rows) > 0 {
			m.toggleAgentSection()
			m.scrollSelectedAgentIntoView()
			m.selectLatestMessage()
			return m, m.reloadMessages()
		}
	case tea.KeyCtrlH:
		if len(m.rows) > 0 {
			cmd := m.toggleHiddenCurrentAgent()
			m.selectLatestMessage()
			return m, tea.Batch(cmd, m.reloadMessages())
		}
	case tea.KeyCtrlA:
		if m.activeTabCanCompose() && len(m.rows) > 0 {
			m.clearUnread(m.rows[m.selected])
			return m, nil
		}
	case tea.KeyUp:
		m.messageFocused = true
		messages := m.displayOrderedMessages()
		if m.messageSelected > 0 {
			m.messageSelected = selectableMessageIndex(messages, m.messageSelected-1, -1)
			m.scrollSelectedMessageIntoView()
		}
	case tea.KeyDown:
		m.messageFocused = true
		messages := m.displayOrderedMessages()
		if m.messageSelected < len(messages)-1 {
			m.messageSelected = selectableMessageIndex(messages, m.messageSelected+1, 1)
			m.scrollSelectedMessageIntoView()
		}
	case tea.KeyPgUp, tea.KeyCtrlU:
		if m.mode == homeView || m.mode == changelogView {
			m.messageOffset = max(0, m.messageOffset-messagePageSize(m.height))
		} else {
			m.scrollMessageViewport(-messagePageSize(m.height))
		}
	case tea.KeyPgDown, tea.KeyCtrlD:
		if m.mode == homeView || m.mode == changelogView {
			m.messageOffset = m.messageOffset + messagePageSize(m.height)
		} else {
			m.scrollMessageViewport(messagePageSize(m.height))
		}
	case tea.KeyF1:
		if m.activeTabCanCompose() {
			m.inputMode = inputModeMessage
		}
		return m, nil
	case tea.KeyF2:
		if m.activeTabCanCompose() {
			m.inputMode = inputModeText
		}
		return m, nil
	case tea.KeyF3:
		if m.activeTabCanCompose() {
			m.inputMode = inputModeKeys
		}
		return m, nil
	case tea.KeyF4:
		return m, nil
	case tea.KeyEnter:
		return m.handleComposerSubmit()
	case tea.KeyBackspace:
		if m.activeTabCanCompose() {
			m.messageFocused = false
			if len(m.composer) > 0 {
				m.composer = m.composer[:len(m.composer)-1]
			}
		}
	case tea.KeyCtrlW:
		if m.activeTabCanCompose() {
			m.messageFocused = false
			m.composer = deletePreviousWord(m.composer)
		}
	case tea.KeyCtrlE:
		messages := m.displayOrderedMessages()
		if len(messages) > 0 {
			return m, openMessageInEditor(messages[m.messageSelected])
		}
	case tea.KeyRunes:
		if len(msg.Runes) == 1 && msg.Runes[0] == 'r' && len(m.composer) == 0 && m.err != nil && m.retryOperation != "" {
			return m, m.retryCurrentOperation()
		}
		if len(msg.Runes) == 1 && msg.Runes[0] == 'n' && len(m.composer) == 0 && (m.activeTabCanCompose() || m.mode == homeView || m.mode == changelogView) && m.selectNextUnread() {
			m.scrollSelectedAgentIntoView()
			m.selectLatestMessage()
			if m.mode == homeView || m.mode == changelogView {
				m.mode = simpleView
			}
			return m, m.reloadMessages()
		}
		if m.activeTabCanCompose() {
			m.messageFocused = false
			m.composer = append(m.composer, msg.Runes...)
			m.messageOffset = 0
		}
	case tea.KeySpace:
		if m.activeTabCanCompose() {
			m.messageFocused = false
			m.composer = append(m.composer, ' ')
			m.messageOffset = 0
		}
	}
	return m, nil
}

func (m model) handlePromptMenuKey(msg tea.KeyMsg) (model, tea.Cmd) {
	switch msg.Type {
	case tea.KeyCtrlC, tea.KeyCtrlQ:
		return m, tea.Quit
	case tea.KeyCtrlO, tea.KeyEsc:
		m.showingPromptMenu = false
		return m, nil
	case tea.KeyUp, tea.KeyCtrlP:
		if m.promptSelected > 0 {
			m.promptSelected--
		}
		return m, nil
	case tea.KeyDown, tea.KeyCtrlN:
		if m.promptSelected < len(m.prompts)-1 {
			m.promptSelected++
		}
		return m, nil
	case tea.KeyEnter:
		m.showingPromptMenu = false
		if len(m.prompts) > 0 && m.canSendCurrent() {
			return m, editPromptTemplate(m.prompts[m.promptSelected].Path)
		}
		return m, nil
	}
	return m, nil
}

func (m model) filteredConfigItems() []ConfigSelectionItem {
	query := strings.ToLower(strings.TrimSpace(string(m.configQuery)))
	if query == "" {
		return m.configItems
	}
	items := []ConfigSelectionItem{}
	for _, item := range m.configItems {
		haystack := strings.ToLower(strings.Join([]string{item.Name, item.Description, item.Hostname, item.TargetAddress}, " "))
		pos := 0
		matched := true
		for _, r := range query {
			idx := strings.IndexRune(haystack[pos:], r)
			if idx < 0 {
				matched = false
				break
			}
			pos += idx + 1
		}
		if matched {
			items = append(items, item)
		}
	}
	return items
}

func (m model) handleConfigMenuKey(msg tea.KeyMsg) (model, tea.Cmd) {
	items := m.filteredConfigItems()
	switch msg.Type {
	case tea.KeyCtrlC, tea.KeyCtrlQ:
		return m, tea.Quit
	case tea.KeyCtrlR, tea.KeyEsc:
		m.showingConfigMenu = false
		m.configQuery = nil
		return m, nil
	case tea.KeyBackspace:
		if len(m.configQuery) > 0 {
			m.configQuery = m.configQuery[:len(m.configQuery)-1]
			m.configSelected = min(m.configSelected, max(0, len(m.filteredConfigItems())-1))
		}
		return m, nil
	case tea.KeyUp, tea.KeyCtrlP:
		if m.configSelected > 0 {
			m.configSelected--
		}
		return m, nil
	case tea.KeyDown, tea.KeyCtrlN:
		if m.configSelected < len(items)-1 {
			m.configSelected++
		}
		return m, nil
	case tea.KeyRunes:
		if len(msg.Runes) == 1 && (msg.Runes[0] == 'c' || msg.Runes[0] == 'C') && len(items) > 0 {
			m.showingConfigMenu = false
			return m, copyAgentImmutableCmd(items[m.configSelected])
		}
		m.configQuery = append(m.configQuery, msg.Runes...)
		m.configSelected = 0
		return m, nil
	case tea.KeySpace:
		m.configQuery = append(m.configQuery, ' ')
		m.configSelected = 0
		return m, nil
	case tea.KeyEnter:
		m.showingConfigMenu = false
		if len(items) > 0 {
			item := items[m.configSelected]
			if item.IsNewAgent {
				m.openRunAgentForm(item)
				return m, nil
			}
			if item.IsRemote {
				if item.Running || !item.Launchable {
					return m, copyAgentImmutableCmd(item)
				}
				m.openRunAgentForm(item)
				return m, nil
			}
			if item.Running || !item.Launchable {
				return m, copyAgentImmutableCmd(item)
			}
			m.openRunAgentForm(item)
			return m, nil
		}
		return m, nil
	}
	return m, nil
}

func (m model) nextRunAgentField(delta int) int {
	numFields := 4
	next := (m.runAgentField + delta + numFields) % numFields
	if m.runAgentIsExisting && next == 0 {
		// Skip Name field for existing agents
		next = (next + delta + numFields) % numFields
	}
	return next
}

func (m model) handleRunAgentFormKey(msg tea.KeyMsg) (model, tea.Cmd) {
	switch msg.Type {
	case tea.KeyCtrlC, tea.KeyCtrlQ:
		return m, tea.Quit
	case tea.KeyEsc:
		m.showingRunAgentForm = false
		m.runAgentName = nil
		m.runAgentArgs = nil
		m.runAgentCWD = nil
		return m, nil
	case tea.KeyTab:
		if m.runAgentField == 0 {
			if completed := completeAgentName(string(m.runAgentName), m.runAgentSuggestions); completed != "" && completed != string(m.runAgentName) {
				m.runAgentName = []rune(completed)
				return m, nil
			}
		}
		m.runAgentField = m.nextRunAgentField(1)
		return m, nil
	case tea.KeyUp, tea.KeyCtrlP:
		if m.runAgentField == 2 {
			m.cycleRunAgentProvider(-1)
			return m, nil
		}
		m.runAgentField = m.nextRunAgentField(-1)
		return m, nil
	case tea.KeyDown, tea.KeyCtrlN:
		if m.runAgentField == 2 {
			m.cycleRunAgentProvider(1)
			return m, nil
		}
		m.runAgentField = m.nextRunAgentField(1)
		return m, nil
	case tea.KeyBackspace:
		if m.runAgentField == 0 {
			if !m.runAgentIsExisting && len(m.runAgentName) > 0 {
				m.runAgentName = m.runAgentName[:len(m.runAgentName)-1]
			}
		} else if m.runAgentField == 1 {
			if len(m.runAgentCWD) > 0 {
				m.runAgentCWD = m.runAgentCWD[:len(m.runAgentCWD)-1]
			}
		} else if m.runAgentField == 3 {
			if len(m.runAgentArgs) > 0 {
				m.runAgentArgs = m.runAgentArgs[:len(m.runAgentArgs)-1]
			}
		}
		return m, nil
	case tea.KeySpace:
		if m.runAgentField == 0 && !m.runAgentIsExisting {
			m.runAgentName = append(m.runAgentName, '-')
		} else if m.runAgentField == 1 {
			m.runAgentCWD = append(m.runAgentCWD, ' ')
		} else if m.runAgentField == 3 {
			m.runAgentArgs = append(m.runAgentArgs, ' ')
		}
		return m, nil
	case tea.KeyRunes:
		if m.runAgentField == 2 {
			return m, nil
		}
		if m.runAgentField == 0 && m.runAgentIsExisting {
			return m, nil
		}
		for _, r := range msg.Runes {
			if m.runAgentField == 0 && r == ' ' {
				r = '-'
			}
			if m.runAgentField == 0 {
				m.runAgentName = append(m.runAgentName, r)
			} else if m.runAgentField == 1 {
				m.runAgentCWD = append(m.runAgentCWD, r)
			} else if m.runAgentField == 3 {
				m.runAgentArgs = append(m.runAgentArgs, r)
			}
		}
		return m, nil
	case tea.KeyEnter:
		name := strings.TrimSpace(string(m.runAgentName))
		if name == "" {
			m.err = fmt.Errorf("agent name is required")
			return m, nil
		}
		profileName := m.runAgentProfileName
		if !m.runAgentIsExisting {
			profileName = name
		}
		m.showingRunAgentForm = false
		m.runAgentName = nil
		optionalArgs := strings.TrimSpace(string(m.runAgentArgs))
		m.runAgentArgs = nil
		cwdOverride := strings.TrimSpace(string(m.runAgentCWD))
		m.runAgentCWD = nil
		return m, runAgentWithOverridesCmd(
			m.runAgentIsExisting,
			profileName,
			m.runAgentHost,
			cwdOverride,
			m.runAgentDefaultCWD,
			m.runAgentProvider,
			m.runAgentDefaultProv,
			optionalArgs,
		)
	}
	return m, nil
}

func (m *model) cycleRunAgentProvider(delta int) {
	if len(m.runAgentProviders) == 0 {
		m.runAgentProvider = ""
		return
	}
	idx := 0
	for i, provider := range m.runAgentProviders {
		if provider == m.runAgentProvider {
			idx = i
			break
		}
	}
	idx = (idx + delta + len(m.runAgentProviders)) % len(m.runAgentProviders)
	m.runAgentProvider = m.runAgentProviders[idx]
}

func (m model) handleComposerSubmit() (model, tea.Cmd) {
	if !m.activeTabCanCompose() {
		return m, nil
	}
	if strings.TrimSpace(string(m.composer)) != "" {
		input := string(m.composer)
		action := composerActionForMode(input, m.inputMode)
		if action.Kind == "memory_action" {
			if action.Result != "approve" && action.Result != "reject" && action.Result != "edit" {
				m.err = fmt.Errorf("/memory requires approve|reject|edit")
				return m, nil
			}
			memoryID := action.MemoryID
			selected, hasSelected := selectedMemoryMessage(m)
			if memoryID == "" && hasSelected {
				memoryID = selected.MemoryID
			}
			if memoryID == "" {
				m.err = fmt.Errorf("memory id is required")
				return m, nil
			}
			m.composer = nil
			return m, memoryActionCmd(m.local, memoryMessageForAction(memoryID, selected), action.Result, action.Title, action.Body)
		}
		if action.Kind == "approval_review" {
			if action.Result == "" {
				m.err = fmt.Errorf("/approval requires good|bad|need_improvements")
				return m, nil
			}
			approvalID := action.ApprovalID
			selected, hasSelected := selectedApprovalMessage(m)
			if approvalID == "" && hasSelected {
				approvalID = selected.ApprovalID
			}
			if approvalID == "" {
				m.err = fmt.Errorf("approval id is required")
				return m, nil
			}
			m.composer = nil
			return m, approvalReviewCmd(approvalMessageForReview(approvalID, selected), action.Result)
		}
		row, ok := m.currentSendTarget()
		if !ok || m.agentListStale {
			return m, nil
		}
		if action.Kind == "restart" {
			m.composer = nil
			m.directInputStatus = fmt.Sprintf("Triggering restart for %s...", row.Name)
			m.directInputStatusErr = false
			return m, restartAgentCmd(m.local, rowTarget(row), action.Timeout)
		}
		if action.Kind == "broadcast" {
			m.directInputStatus = "Broadcast mode is disabled in this milestone; no message was sent"
			m.directInputStatusErr = true
			return m, tea.Tick(4*time.Second, func(time.Time) tea.Msg { return clearDirectInputStatusTick{} })
		}
		if action.Kind == "direct_text" || action.Kind == "direct_keys" {
			m.composer = nil
			m.directInputStatus = fmt.Sprintf("Sending pane control to %s...", row.Name)
			return m, sendDirectInput(m.local, row, action, m.runtime.RemoteDirectInputEnabled)
		}
		if strings.TrimSpace(action.Body) == "" {
			return m, nil
		}
		record := makeOutboxRecord(m.ownName, row, action.Body)

		m.composer = nil
		unhideCmd := m.unhideAgent(row)
		m.clearUnread(row)
		m.appendSentMessage(row, record)
		m.refreshMergedMessages()
		m.selectLatestMessage()
		return m, tea.Batch(unhideCmd, sendOutboxRecord(m.local, m.ownName, row, record))
	}
	return m, nil
}
