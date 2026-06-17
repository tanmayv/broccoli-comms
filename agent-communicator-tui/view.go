package main

import (
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/config"

	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

var composerBoxStyle = lipgloss.NewStyle().Background(colors.InputBg).Foreground(colors.Text).Padding(1, 2)
var panelBoxStyle = lipgloss.NewStyle().Background(colors.PanelBg).Padding(0, 1)
var mobileComposerBoxStyle = lipgloss.NewStyle().Background(colors.InputBg).Padding(1, 1)

const composerMaxLines = 5

func (m model) View() string {
	defer debugSince("view", time.Now())
	if m.width == 0 {
		return "loading..."
	}
	if m.commandPalette.Open {
		return m.commandPaletteView(m.width, m.height)
	}
	return m.baseView()
}

func (m model) baseView() string {
	fullH := max(1, m.height)
	if m.showingSaveForm {
		return m.renderSaveForm()
	}
	if m.showingPromptMenu {
		return m.renderPromptMenu(m.width, fullH)
	}
	if m.showingConfigMenu {
		return m.renderConfigMenu(m.width, fullH)
	}
	if m.showingRunAgentForm {
		return m.renderRunAgentForm(m.width, fullH)
	}
	tabs := m.bottomTabBar(m.width)
	status := m.footer(m.width)
	bottomH := lineCount(status) + lineCount(tabs)
	bodyH := max(1, fullH-bottomH)
	parts := []string{m.mainContentView(bodyH)}
	if status != "" {
		parts = append(parts, status)
	}
	if tabs != "" {
		parts = append(parts, tabs)
	}
	return truncateLines(lipgloss.JoinVertical(lipgloss.Left, parts...), fullH)
}

func (m model) mainContentView(bodyH int) string {
	if m.mode == homeView {
		return truncateLines(m.homePanel(m.width, bodyH), bodyH)
	}
	if m.mode == changelogView {
		return truncateLines(m.changelogPanel(m.width, bodyH), bodyH)
	}
	if m.mode == memoryView {
		return truncateLines(m.memoryManagementView(m.width, bodyH), bodyH)
	}
	if m.mode == tasksView {
		return truncateLines(m.taskManagementView(m.width, bodyH), bodyH)
	}
	if m.width < 70 {
		return truncateLines(m.conversationPanel(m.width, bodyH), bodyH)
	}
	chatW, rightW, _ := m.layoutWidths()
	chat := m.conversationPanel(chatW, bodyH)
	right := m.rightColumn(rightW, bodyH)
	return truncateLines(lipgloss.JoinHorizontal(lipgloss.Top, chat, right), bodyH)
}

func (m model) layoutWidths() (int, int, int) {
	chat, right := contentDetailLayoutWidths(m.width)
	return chat, right, 0
}

func (m model) footer(width int) string {
	lines := []string{}
	if m.paneCaptureStatus != "" {
		lines = append(lines, m.paneCaptureStatus)
	} else if m.directInputStatus != "" {
		statusLine := m.directInputStatus
		if m.directInputStatusErr {
			statusLine = errorBarStyle.Render(statusLine)
		}
		lines = append(lines, statusLine)
	}
	if m.err != nil {
		lines = append(lines, errorBarStyle.Render(m.errorStatusLine()))
	}
	for i, text := range lines {
		if lipgloss.Width(text) > width {
			lines[i] = truncateCells(text, max(1, width-1)) + "…"
		}
	}
	return mutedStyle.Render(strings.Join(lines, "\n"))
}

func (m model) versionStatusLine() string {
	trackerVersion := firstNonEmpty(m.health.Build.Display, buildDisplay(m.health.Version, m.health.Revision), "?")
	return "ui " + version + " · tracker " + trackerVersion
}

func buildDisplay(v, rev string) string {
	if v == "" {
		return ""
	}
	if rev != "" && rev != "unknown" {
		return v + "+" + rev
	}
	return v
}

func (m model) errorStatusLine() string {
	text := "error · " + m.err.Error()
	if rpcErr, ok := m.err.(*tracker.RPCError); ok && rpcErr.Data != nil {
		parts := []string{"error"}
		if rpcErr.Data.Operation != "" {
			parts = append(parts, rpcErr.Data.Operation)
		}
		if rpcErr.Data.Agent != "" {
			parts = append(parts, rpcErr.Data.Agent)
		}
		parts = append(parts, rpcErr.Message)
		text = strings.Join(parts, " · ")
	}
	if m.retryOperation != "" {
		text += " · r retry"
	}
	return text
}

func (m model) runtimeStatusLine() string {
	state := "rpc ok"
	if m.agentListLoading {
		state = "rpc refreshing"
	}
	if m.healthErr != nil || m.agentListStale || m.err != nil {
		state = "rpc degraded"
	}
	if m.health.Status != "" && m.health.Status != "ok" && state == "rpc ok" {
		state = "rpc " + m.health.Status
	}
	row := m.currentRow()
	active := "no agent"
	if row.Name != "" {
		activeParts := []string{row.Name}
		if badge := modelBadge(row); badge != "??" {
			activeParts = append(activeParts, badge)
		}
		if machine := rowMachineLabel(row); machine != "" {
			activeParts = append(activeParts, "@ "+machine)
		}
		active = strings.Join(activeParts, " ")
	}
	online, total := m.health.OnlineAgentCount, m.health.AgentCount
	if total == 0 && len(m.rows) > 0 {
		total = len(m.rows)
		online = countOnlineRows(m.rows)
	}
	registry := "registry unknown"
	if m.health.RegistryConnected != nil {
		if *m.health.RegistryConnected {
			registry = "registry online"
		} else {
			registry = "registry offline"
		}
	}
	details := []string{state, "active " + active, fmt.Sprintf("online %d/%d", online, total), registry}
	if m.health.RemoteTrackerCount > 0 {
		details = append(details, fmt.Sprintf("trackers %d/%d", m.health.OnlineRemoteTrackerCount, m.health.RemoteTrackerCount))
	}
	details = append(details, time.Now().In(displayLocation).Format("15:04"))
	if m.runtime.AppRuntime {
		details = append([]string{"Broccoli Comms runtime"}, details...)
	}
	if m.runtime.TrackerSocket != "" {
		details = append(details, "socket "+filepath.Base(m.runtime.TrackerSocket))
	}
	return strings.Join(details, " · ")
}

func (m model) sendingContextLine() string {
	row := m.currentRow()
	if row.Name == "" {
		return "no target"
	}
	parts := []string{row.Name}
	if badge := modelBadge(row); badge != "??" {
		parts = append(parts, badge)
	}
	if machine := rowMachineLabel(row); machine != "" {
		parts = append(parts, "@ "+machine)
	}
	return strings.Join(parts, " ")
}

func truncateLines(s string, height int) string {
	lines := strings.Split(s, "\n")
	if len(lines) > height {
		lines = lines[:height]
	}
	return strings.Join(lines, "\n")
}

func wrapLine(s string, width int) []string {
	if lipgloss.Width(s) <= width {
		return []string{s}
	}
	words := strings.Fields(s)
	if len(words) == 0 {
		return []string{""}
	}
	var lines []string
	current := ""
	for _, word := range words {
		candidate := strings.TrimSpace(current + " " + word)
		if lipgloss.Width(candidate) <= width {
			current = candidate
			continue
		}
		if current != "" {
			lines = append(lines, current)
		}
		for lipgloss.Width(word) > width {
			part := truncateCells(word, width)
			lines = append(lines, part)
			word = strings.TrimPrefix(word, part)
		}
		current = word
	}
	if current != "" {
		lines = append(lines, current)
	}
	return lines
}
func truncateCells(s string, width int) string {
	var b strings.Builder
	for _, r := range s {
		if lipgloss.Width(b.String()+string(r)) > width {
			break
		}
		b.WriteRune(r)
	}
	return b.String()
}
func marker(selected bool) string {
	if selected {
		return ">"
	}
	return " "
}
func panelInnerWidth(w int) int  { return max(1, w-4) }
func panelInnerHeight(h int) int { return max(1, h-2) }

func box(s string, w, h int) string {
	innerW := panelInnerWidth(w)
	innerH := panelInnerHeight(h)
	return panelBoxStyle.Width(innerW).Height(innerH).MaxWidth(max(1, w)).Render(s)
}
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func localHostname() string {
	if h := config.GetString("", "tracker", "hostname"); h != "" {
		return h
	} else if h := os.Getenv("AGENT_TRACKER_HOSTNAME"); h != "" {
		return h
	}
	if h, err := os.Hostname(); err == nil {
		return h
	}
	return "local"
}

func (m model) renderPromptMenu(width, height int) string {
	var body string
	if len(m.prompts) == 0 {
		body = lipgloss.NewStyle().
			Foreground(colors.Warning).
			Render("No prompt templates found.\nAdd <prompt-name>.md files in ~/.config/agent-communicator/prompts/")
	} else {
		var listLines []string
		for i, prompt := range m.prompts {
			style := lipgloss.NewStyle().Foreground(colors.Text)
			prefix := "  "
			if i == m.promptSelected {
				style = style.Background(colors.SelectedBg).Foreground(colors.SelectedFg)
				prefix = "> "
			}
			listLines = append(listLines, prefix+style.Render(prompt.Name))
		}
		body = strings.Join(listLines, "\n")
	}

	title := titleStyle.Render("Prompt Templates")
	help := mutedStyle.Render("enter edit/send · esc close · only saved edits are sent")
	boxContent := title + "\n" + help + "\n\n" + body
	return box(boxContent, width, height)
}

func (m model) renderRunAgentForm(width, height int) string {
	innerW := panelInnerWidth(width)
	
	// Base input box style
	var inputFieldStyle = lipgloss.NewStyle().
		Padding(0, 1).
		Border(lipgloss.NormalBorder()).
		BorderForeground(colors.Border)

	// Focused input box style
	var focusedInputFieldStyle = lipgloss.NewStyle().
		Padding(0, 1).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(colors.Success).
		Bold(true)

	// Label styles
	var formLabelStyle = lipgloss.NewStyle().
		Foreground(colors.TextStrong).
		Bold(false)

	var focusedFormLabelStyle = lipgloss.NewStyle().
		Foreground(colors.Success).
		Bold(true)

	renderField := func(index int, label string, value string, placeholder string, hint string) string {
		isFocused := m.runAgentField == index
		
		// Style label
		lblStyle := formLabelStyle
		if isFocused {
			lblStyle = focusedFormLabelStyle
		}
		lbl := lblStyle.Render(label)

		// Style value
		valStr := value
		if valStr == "" {
			valStr = lipgloss.NewStyle().Foreground(colors.Muted).Render(placeholder)
		} else {
			if isFocused {
				valStr = lipgloss.NewStyle().Foreground(colors.SelectedFg).Render(valStr)
			} else {
				valStr = lipgloss.NewStyle().Foreground(colors.Text).Render(valStr)
			}
		}

		// Apply box borders
		boxStyle := inputFieldStyle
		if isFocused {
			boxStyle = focusedInputFieldStyle
		}
		box := boxStyle.Render(valStr)

		// Render row
		rowStr := lbl + "\n" + box
		if hint != "" {
			rowStr += "\n" + lipgloss.NewStyle().Foreground(colors.Muted).Render(hint)
		}
		return rowStr
	}

	// 1. Name Row (Read-Only if existing)
	var nameRow string
	if m.runAgentIsExisting {
		lbl := formLabelStyle.Render("Agent Name (Read-Only)")
		val := lipgloss.NewStyle().Foreground(colors.TextStrong).Bold(true).Render(string(m.runAgentName))
		nameRow = lbl + "\n" + val
	} else {
		nameVal := string(m.runAgentName)
		suggestion := completeAgentName(nameVal, m.runAgentSuggestions)
		hint := ""
		if suggestion != "" && suggestion != nameVal {
			hint = "💡 Tab to autocomplete: " + suggestion
		}
		nameRow = renderField(0, "Agent Name", nameVal, "Enter agent name...", hint)
	}

	// 2. CWD Row
	cwdVal := string(m.runAgentCWD)
	cwdHint := ""
	if m.runAgentIsExisting && m.runAgentDefaultCWD != "" {
		cwdHint = "💡 Default: " + m.runAgentDefaultCWD
	} else {
		cwdHint = "💡 Leave empty for default"
	}
	cwdRow := renderField(1, "Working Directory (CWD Override)", cwdVal, "Enter working directory...", cwdHint)

	// 3. Provider Row
	provVal := fallback(m.runAgentProvider, "no configured provider")
	if m.runAgentField == 2 {
		provVal = "◀  " + provVal + "  ▶"
	}
	provRow := renderField(2, "Provider (Override)", provVal, "Select provider...", "💡 Use ↑/↓ arrow keys to cycle providers")

	// 4. Args Row
	argsVal := string(m.runAgentArgs)
	argsRow := renderField(3, "Optional Arguments", argsVal, "Enter optional arguments...", "")

	// Card Header
	headerText := "🚀 RUN NEW AGENT"
	if m.runAgentIsExisting {
		headerText = "🚀 RUN EXISTING AGENT: " + m.runAgentProfileName
	}
	header := lipgloss.NewStyle().Bold(true).Foreground(colors.Accent).Render(headerText) + "\n" +
		lipgloss.NewStyle().Foreground(colors.Muted).Render("Host: "+m.runAgentHost)

	// Divider
	divider := lipgloss.NewStyle().Foreground(colors.Border).Render(strings.Repeat("─", max(1, innerW)))

	// Card Footer
	footer := lipgloss.NewStyle().Foreground(colors.Muted).Render("Enter: Run Agent  ·  Tab: Next Field  ·  Esc: Cancel")

	// Card Body
	body := nameRow + "\n\n" + cwdRow + "\n\n" + provRow + "\n\n" + argsRow

	// Final Layout
	cardContent := header + "\n" + divider + "\n\n" + body + "\n\n" + divider + "\n" + footer
	return box(cardContent, width, height)
}

func (m model) renderConfigMenu(width, height int) string {
	var body string
	items := m.filteredConfigItems()
	query := string(m.configQuery)
	if len(m.configItems) == 0 {
		body = lipgloss.NewStyle().
			Foreground(colors.Error).
			Render("No configured, running, or remote agents found via broccoli-comms agent list.")
	} else if len(items) == 0 {
		body = lipgloss.NewStyle().Foreground(colors.Error).Render("No agents match search: " + query)
	} else {
		var listLines []string
		lastHost := ""
		isNewAgentSection := false

		for i, item := range items {
			// Section Header Logic
			if item.IsNewAgent && !isNewAgentSection {
				listLines = append(listLines, "\n"+sectionHeaderStyle.Render("🌐 NEW AGENTS"))
				isNewAgentSection = true
				lastHost = ""
			} else if !item.IsNewAgent {
				host := fallback(item.Hostname, localHostname())
				if host != lastHost {
					headerText := "🌐 LOCAL (" + host + ")"
					if item.IsRemote {
						headerText = "🌐 REMOTE (" + host + ")"
					}
					listLines = append(listLines, "\n"+sectionHeaderStyle.Render(headerText))
					lastHost = host
				}
			}
			style := lipgloss.NewStyle().Foreground(colors.Text)
			prefix := "  "
			if i == m.configSelected {
				style = style.Background(colors.SelectedBg).Foreground(colors.SelectedFg)
				prefix = "> "
			}

			scopePrefix := fmt.Sprintf("[%s] ", localHostname())
			if item.IsNewAgent {
				scopePrefix = fmt.Sprintf("[%s] new ", fallback(item.Hostname, localHostname()))
			} else if item.IsRemote {
				if item.Running {
					scopePrefix = fmt.Sprintf("[%s] remote running ", fallback(item.Hostname, "remote"))
				} else if item.Configured {
					scopePrefix = fmt.Sprintf("[%s] remote configured ", fallback(item.Hostname, "remote"))
				} else {
					scopePrefix = fmt.Sprintf("[%s] remote ", fallback(item.Hostname, "remote"))
				}
			} else if item.Running {
				scopePrefix = fmt.Sprintf("[%s] running ", localHostname())
			} else if item.Configured {
				scopePrefix = fmt.Sprintf("[%s] configured ", localHostname())
			}

			scopeStyle := lipgloss.NewStyle().Foreground(colors.Muted)
			if !item.IsRemote {
				scopeStyle = lipgloss.NewStyle().Foreground(colors.Success)
			}
			if i == m.configSelected {
				scopeStyle = scopeStyle.Background(colors.SelectedBg).Foreground(colors.SelectedFg)
			}

			action := "Enter: run/override"
			if item.IsNewAgent {
				action = "Enter: form"
			} else if item.IsRemote {
				if item.Running {
					action = "Enter: copy"
				} else if item.Launchable {
					action = "Enter: run/override (remote) · c: copy"
				} else {
					action = "Enter: copy"
				}
			} else if item.Running || !item.Launchable {
				action = "Enter: immutable copy"
			} else if item.Copyable {
				action = "Enter: run/override · c: copy"
			}
			line := prefix + scopeStyle.Render(scopePrefix) + style.Render(item.Name) + " - " + item.Description + mutedStyle.Render(" · "+action)
			listLines = append(listLines, truncateCells(line, panelInnerWidth(width)))
		}
		body = strings.Join(listLines, "\n")
	}

	title := titleStyle.Render("Agents (copy/run new)")
	boxContent := title + "\n" + mutedStyle.Render("Search: "+query+"  ·  Enter copies live agents or opens run-new form") + "\n\n" + body
	return box(boxContent, width, height)
}

func (m model) homePanel(width, height int) string {
	padX := 3
	if width < 70 {
		padX = 1
	}
	innerW := max(1, width-(padX*2))

	// ASCII Art (centered)
	ascii := []string{
		"  ____                             _ _   ____",
		" | __ ) _ __ ___   ___  ___  ___  | (_) / ___|___  _ __ ___  _ __ ___  ___",
		" |  _ \\| '__/ _ \\ / __|/ __|/ _ \\ | | |/ /   / _ \\| '_ ` _ \\| '_ ` _ \\/ __|",
		" | |_) | | | (_) | (__| (__| (_) || | | |__| (_) | | | | | | | | | | \\__ \\",
		" |____/|_|  \\___/ \\___|\\___|\\___/ |_|_|\\____\\___/|_| |_| |_|_| |_| |_|___/",
	}

	var asciiLines []string
	for _, line := range ascii {
		if len(line) < innerW {
			padding := (innerW - len(line)) / 2
			asciiLines = append(asciiLines, strings.Repeat(" ", padding)+line)
		} else {
			asciiLines = append(asciiLines, line)
		}
	}
	asciiText := lipgloss.NewStyle().Foreground(colors.Accent).Bold(true).Render(strings.Join(asciiLines, "\n"))

	// Tagline (centered)
	tagline := "Decentralized, task-oriented multi-agent communications protocol."
	var taglineText string
	taglineStyle := lipgloss.NewStyle().Foreground(colors.Muted).Italic(true)
	if len(tagline) < innerW {
		padding := (innerW - len(tagline)) / 2
		taglineText = strings.Repeat(" ", padding) + taglineStyle.Render(tagline)
	} else {
		taglineText = taglineStyle.Render(tagline)
	}

	// Content Builder
	var b strings.Builder
	b.WriteString(asciiText + "\n\n")
	b.WriteString(taglineText + "\n\n")

	// Divider
	divider := lipgloss.NewStyle().Foreground(colors.Border).Render(strings.Repeat("━", innerW))
	b.WriteString(divider + "\n\n")

	// Section 1: Welcome & Tmux Session
	titleStyle := lipgloss.NewStyle().Bold(true).Foreground(colors.Accent)
	subStyle := lipgloss.NewStyle().Bold(true).Foreground(colors.AccentAlt)
	boldStyle := lipgloss.NewStyle().Bold(true).Foreground(colors.TextStrong)
	italicStyle := lipgloss.NewStyle().Italic(true).Foreground(colors.TextSubtle)
	
	b.WriteString(titleStyle.Render("Welcome to Broccoli Comms TUI") + "\n\n")
	b.WriteString("Broccoli Comms is a task-oriented multi-agent coordination system.\n")
	b.WriteString("All active agents run inside a dedicated local tmux session named " + boldStyle.Render("AGENTS") + ".\n")
	b.WriteString("You can attach to it via " + italicStyle.Render("tmux a -t AGENTS") + " in your terminal, though the TUI\n")
	b.WriteString("provides full monitoring, control, and interaction capabilities.\n\n")

	// Section 2: Direct Tab Navigation (Alt Shortcuts)
	b.WriteString(subStyle.Render("Direct Tab Navigation (Alt Shortcuts):") + "\n")
	b.WriteString("  " + boldStyle.Render("Alt-1") + " or " + boldStyle.Render("Alt-h") + " : Go to " + boldStyle.Render("[Home]") + " (this user guide)\n")
	b.WriteString("  " + boldStyle.Render("Alt-2") + " or " + boldStyle.Render("Alt-c") + " : Go to " + boldStyle.Render("[Chat]") + " (Simple Chat & composer)\n")
	b.WriteString("  " + boldStyle.Render("Alt-3") + " or " + boldStyle.Render("Alt-s") + " : Go to " + boldStyle.Render("[Saved]") + " (Starred messages list)\n")
	b.WriteString("  " + boldStyle.Render("Alt-4") + " or " + boldStyle.Render("Alt-m") + " : Go to " + boldStyle.Render("[Memory]") + " (Memory Management & audits)\n")
	b.WriteString("  " + boldStyle.Render("Alt-5") + " or " + boldStyle.Render("Alt-t") + " : Go to " + boldStyle.Render("[Tasks]") + " (Manage task chains)\n")
	b.WriteString("  " + boldStyle.Render("Alt-6") + " or " + boldStyle.Render("Alt-l") + " : Go to " + boldStyle.Render("[Changelog]") + " (Release notes)\n")
	b.WriteString("  " + boldStyle.Render("Ctrl-t") + " / " + boldStyle.Render("Ctrl-y") + " : Cycle to Next / Previous Tab\n\n")

	// Section 3: Core Navigation & Scrolling Shortcuts
	b.WriteString(subStyle.Render("Core Navigation & Scrolling:") + "\n")
	b.WriteString("  " + boldStyle.Render("Ctrl-u / Ctrl-d") + " : Scroll viewport Up / Down (also PgUp/PgDn)\n")
	b.WriteString("  " + boldStyle.Render("Ctrl-n / Ctrl-p") + " : Navigate active/remote agent lists\n")
	b.WriteString("  " + boldStyle.Render("Tab / Shift-Tab") + " : Toggle focus between Active and Remote agent sidebars\n\n")

	// Section 4: Running and Managing Agents
	b.WriteString(subStyle.Render("Running and Managing Agents:") + "\n")
	b.WriteString("  - Highlight a remote agent in the sidebar and press " + boldStyle.Render("Enter") + " to open the launch form.\n")
	b.WriteString("  - Configure the agent's CWD, provider model, CLI args, and click \"Run Agent\".\n")
	b.WriteString("  - To stop or restart an active agent cooperatively, type " + boldStyle.Render("/restart") + " in the chat.\n")
	b.WriteString("  - Open the agent actions menu by pressing " + boldStyle.Render("Enter") + " on an active agent card or via " + boldStyle.Render("Ctrl-k") + ".\n")
	b.WriteString("  " + lipgloss.NewStyle().Foreground(colors.Warning).Bold(true).Render("💡 NOTE:") + " You need to press " + boldStyle.Render("C-o (Ctrl+o)") + " to open the Command Palette, and\n")
	b.WriteString("           select " + boldStyle.Render("\"Run agent\"") + " to open the 'run agent' menu first.\n\n")

	// Section 5: Durable Memory Management
	b.WriteString(subStyle.Render("Working with Durable Memory:") + "\n")
	b.WriteString("  - Agents persist lessons as " + italicStyle.Render("Facts, Habits, Skills, Episodes, or Expertise") + ".\n")
	b.WriteString("  - After completing tasks, agents propose memory candidates to the persistent database.\n")
	b.WriteString("  - Go to the " + boldStyle.Render("[Memory] Tab (Alt-5)") + " to audit, approve, or reject pending memory proposals.\n")
	b.WriteString("  - Keeping memory clean prevents agents from repeating mistakes and ensures high-quality runs.\n\n")

	// Section 6: Interactive Pane Control (in Chat Tab)
	b.WriteString(subStyle.Render("Interactive Pane Control (in Chat Tab):") + "\n")
	b.WriteString("  - Type " + boldStyle.Render("/text <text>") + " to send raw keystrokes directly to the agent's tmux pane.\n")
	b.WriteString("  - Type " + boldStyle.Render("/keys <keys>") + " to send special keys (e.g., " + boldStyle.Render("/keys Enter") + ", " + boldStyle.Render("/keys Ctrl-c") + ").\n")
	b.WriteString("  - Press " + boldStyle.Render("Ctrl-x") + " (C-x) to capture a high-fidelity snapshot of the agent's active pane.\n")
	b.WriteString("  - Press " + boldStyle.Render("Escape") + " to send a default interrupt signal to unstick a busy agent.\n")

	bodyText := b.String()
	bodyLines := strings.Split(bodyText, "\n")

	// Scroll limits & viewport height calculation
	visibleH := height - 2
	totalLines := len(bodyLines)
	
	contentH := visibleH
	showFooter := totalLines > visibleH
	if showFooter {
		contentH = visibleH - 1
	}

	maxOffset := max(0, totalLines-contentH)
	if m.messageOffset > maxOffset {
		m.messageOffset = maxOffset
	}
	if m.messageOffset < 0 {
		m.messageOffset = 0
	}

	endLine := min(totalLines, m.messageOffset+contentH)
	visibleLines := bodyLines[m.messageOffset:endLine]

	bgStyle := lipgloss.NewStyle().Background(colors.BaseBg)
	var paddedLines []string
	for _, line := range visibleLines {
		padded := padStyledLine(line, innerW, colors.BaseBg)
		paddedLines = append(paddedLines, bgStyle.Render(padded))
	}

	if showFooter {
		footerText := fmt.Sprintf(" -- scroll with C-u/C-d or PgUp/PgDn (%d-%d/%d) --", m.messageOffset+1, endLine, totalLines)
		footerRendered := mutedStyle.Italic(true).Render(footerText)
		padded := padStyledLine(footerRendered, innerW, colors.BaseBg)
		paddedLines = append(paddedLines, bgStyle.Render(padded))
	}

	// Pad vertical height with empty styled lines to fill the viewport
	targetBodyH := height - 1
	for len(paddedLines) < targetBodyH {
		padded := padStyledLine("", innerW, colors.BaseBg)
		paddedLines = append(paddedLines, bgStyle.Render(padded))
	}

	renderedBody := strings.Join(paddedLines, "\n")

	return lipgloss.NewStyle().
		Width(width).
		Height(height).
		MaxWidth(width).
		MaxHeight(height).
		Padding(1, padX, 0, padX).
		Background(colors.BaseBg).
		Render(renderedBody)
}

func (m model) changelogPanel(width, height int) string {
	padX := 3
	if width < 70 {
		padX = 1
	}
	innerW := max(1, width-(padX*2))

	// Styles
	titleStyle := lipgloss.NewStyle().Bold(true).Foreground(colors.Accent).Underline(true)
	versionStyle := lipgloss.NewStyle().Bold(true).Foreground(colors.AccentStrong)
	bulletStyle := lipgloss.NewStyle().Foreground(colors.Success)
	taglineStyle := lipgloss.NewStyle().Foreground(colors.Muted).Italic(true)
	boldStyle := lipgloss.NewStyle().Bold(true).Foreground(colors.TextStrong)

	var b strings.Builder

	// Title
	titleText := "Agent Communicator Changelog"
	if len(titleText) < innerW {
		padding := (innerW - len(titleText)) / 2
		b.WriteString(strings.Repeat(" ", padding) + titleStyle.Render(titleText) + "\n\n")
	} else {
		b.WriteString(titleStyle.Render(titleText) + "\n\n")
	}

	// Divider
	divider := lipgloss.NewStyle().Foreground(colors.Border).Render(strings.Repeat("━", innerW))
	b.WriteString(divider + "\n\n")

	// v0.1.6
	b.WriteString(versionStyle.Render("v0.1.6 (Latest Release)") + "\n")
	b.WriteString(taglineStyle.Render("Add --wait flag to broccoli-comms run command, preserving pane output on exit") + "\n\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Run --wait Flag") + ": Added `--wait` flag to `broccoli-comms run` to keep the agent tmux pane open after completion or failure.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Interactive Exit Prompt") + ": Implemented a 30-second countdown and keypress prompt inside the agent-wrapper when wait is enabled.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Remote Wait Propagation") + ": Supported passing the wait flag through remote run request payloads to remote trackers.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Comprehensive Documentation") + ": Documented the `--wait` flag in the root README.md file under quick reference and launch sections.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Version Upgrade") + ": Bumped project and Nix flake packages to version 0.1.6.\n\n")

	// Divider between releases
	b.WriteString(lipgloss.NewStyle().Foreground(colors.Border).Render(strings.Repeat("─", innerW)) + "\n\n")

	// v0.1.5
	b.WriteString(versionStyle.Render("v0.1.5") + "\n")
	b.WriteString(taglineStyle.Render("Roomier sidebar layout, Swarms tab removal, onboarding updates, and styling fixes") + "\n\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Swarms Tab Removal") + ": Completely removed the Swarms tab, its group coordination timeline views, and all related Alt-3/Alt-w shortcuts to streamline the interface.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Sidebar Width Increase") + ": Increased default TUI sidebar width to 35% across all remaining tabs (Home, Chat, Memory, Tasks) for enhanced readability.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Launcher Callout") + ": Added an explicit Note callout instructing users to press C-o (Ctrl+o) to open the Command Palette and launch agents.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Zero-Bleed Backgrounds") + ": Solved terminal background bleed bugs by explicitly styling every single viewport line in Home/Changelog tabs.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Version Upgrade") + ": Bumped project and Nix flake packages to version 0.1.5.\n\n")

	// Divider between releases
	b.WriteString(lipgloss.NewStyle().Foreground(colors.Border).Render(strings.Repeat("─", innerW)) + "\n\n")

	// v0.1.4
	b.WriteString(versionStyle.Render("v0.1.4") + "\n")
	b.WriteString(taglineStyle.Render("Direct Alt shortcuts, comprehensive onboarding guide, and UX polishing") + "\n\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Direct Tab Shortcuts") + ": Switch tabs instantly using Alt+1..7 or Alt+h/c/w/s/m/t/l (works while composing).\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Enhanced Home Tab") + ": Restructured into a complete user guide covering agent launching, memory, pane control, and shortcuts.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Tmux Session Context") + ": Documented the dedicated AGENTS tmux session where active agent runtimes execute.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Background Styling & Footer") + ": Solved right-side background viewport coloring issues and fixed scroll footer visibility.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Version Upgrade") + ": Bumped project and Nix flake packages to version 0.1.4.\n\n")

	// Divider between releases
	b.WriteString(lipgloss.NewStyle().Foreground(colors.Border).Render(strings.Repeat("─", innerW)) + "\n\n")

	// v0.1.3
	b.WriteString(versionStyle.Render("v0.1.3") + "\n")
	b.WriteString(taglineStyle.Render("Focus on onboarding, readability, and platform polishing") + "\n\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("TUI [Home] Tab") + ": Implemented a dedicated welcome screen and usage instructions.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Instruction Scrolling") + ": Added smooth vertical scrolling for long-form welcome text to prevent truncation.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Version Upgrade") + ": Bumped project and Nix flake packages to version 0.1.3.\n\n")

	// Divider between releases
	b.WriteString(lipgloss.NewStyle().Foreground(colors.Border).Render(strings.Repeat("─", innerW)) + "\n\n")

	// v0.1.2
	b.WriteString(versionStyle.Render("v0.1.2") + "\n")
	b.WriteString(taglineStyle.Render("Major feature release adding local tmux integration and TUI control loops") + "\n\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Tmux Status Bar & Click Actions") + ": Interactive session list, agent status tracking, and click-to-focus mouse binds.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Pane Borders & Titles") + ": Pane borders styled with active @agent_name and task titles.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Interactive Pane Control") + ": Added /text and /keys composer commands to send inputs directly to agent panes.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("High-Fidelity Pane Snapshots") + ": Added Ctrl-x (C-x) to capture and deliver high-fidelity active pane snapshots.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Default Interrupt Flag") + ": Enabled automatic Escape-key pre-delivery interrupts to unstick busy agents.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Cooperative Agent Restart") + ": Added TUI /restart slash command and tracker 'request-stop'/'restart' RPC handlers.\n")
	b.WriteString("  " + bulletStyle.Render("•") + " " + boldStyle.Render("Mouse Alternate Screen Scrolling") + ": Custom WheelUp/WheelDown mouse scrolling for alternate screen applications.\n\n")

	bodyText := b.String()
	bodyLines := strings.Split(bodyText, "\n")

	// Scroll limits & viewport height calculation
	visibleH := height - 2
	totalLines := len(bodyLines)
	
	contentH := visibleH
	showFooter := totalLines > visibleH
	if showFooter {
		contentH = visibleH - 1
	}

	maxOffset := max(0, totalLines-contentH)
	if m.messageOffset > maxOffset {
		m.messageOffset = maxOffset
	}
	if m.messageOffset < 0 {
		m.messageOffset = 0
	}

	endLine := min(totalLines, m.messageOffset+contentH)
	visibleLines := bodyLines[m.messageOffset:endLine]

	bgStyle := lipgloss.NewStyle().Background(colors.BaseBg)
	var paddedLines []string
	for _, line := range visibleLines {
		padded := padStyledLine(line, innerW, colors.BaseBg)
		paddedLines = append(paddedLines, bgStyle.Render(padded))
	}

	if showFooter {
		footerText := fmt.Sprintf(" -- scroll with C-u/C-d or PgUp/PgDn (%d-%d/%d) --", m.messageOffset+1, endLine, totalLines)
		footerRendered := mutedStyle.Italic(true).Render(footerText)
		padded := padStyledLine(footerRendered, innerW, colors.BaseBg)
		paddedLines = append(paddedLines, bgStyle.Render(padded))
	}

	// Pad vertical height with empty styled lines to fill the viewport
	targetBodyH := height - 1
	for len(paddedLines) < targetBodyH {
		padded := padStyledLine("", innerW, colors.BaseBg)
		paddedLines = append(paddedLines, bgStyle.Render(padded))
	}

	renderedBody := strings.Join(paddedLines, "\n")

	return lipgloss.NewStyle().
		Width(width).
		Height(height).
		MaxWidth(width).
		MaxHeight(height).
		Padding(1, padX, 0, padX).
		Background(colors.BaseBg).
		Render(renderedBody)
}
