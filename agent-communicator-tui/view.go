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
	right := min(42, max(28, (m.width*32)/100))
	if m.width < 100 {
		right = min(34, max(24, m.width/3))
	}
	chat := max(10, m.width-right)
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
