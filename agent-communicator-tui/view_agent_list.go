package main

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

func (m model) sidebarView(width, height int) string {
	return m.rightColumn(width, height)
}

func (m model) rightColumn(width, height int) string {
	status := m.registryStatusLine()
	statusH := 2
	currentH := min(10, max(9, height/3))
	listH := max(1, height-currentH-statusH)
	current := m.currentAgentPanel(width, currentH)
	list := m.switcherPanel(width, listH)
	statusView := lipgloss.NewStyle().Width(width).Height(statusH).Padding(0, 2).Background(colors.RightColumnBg).Foreground(colors.Muted).Render(status)
	return lipgloss.JoinVertical(lipgloss.Left, current, list, statusView)
}

func (m model) currentAgentPanel(width, height int) string {
	row := m.currentRow()
	view := m.agentView(row)
	name := fallback(view.Name, "no agent selected")
	host := fallback(view.HostnameLabel, localHostname())
	provider := strings.ToLower(view.ModelBadge)
	if provider == "??" {
		provider = "unknown"
	}
	status := view.StatusLabel
	if row.Name == "" {
		status = "unknown"
	}
	heroW := max(1, width-4)
	heroInnerW := max(1, heroW-2)
	statusBadge := lipgloss.NewStyle().Background(colors.RightColumnBg).Foreground(colors.Accent).Padding(0, 1).Render(status)
	namePrefix := statusDotStyle(status).Background(colors.SelectedBg).Render("●") + bgSpaces(1, colors.SelectedBg)
	nameBudget := max(1, heroInnerW-lipgloss.Width(namePrefix)-lipgloss.Width(statusBadge)-1)
	nameText := fgOnBg(colors.SelectedFg, colors.SelectedBg).Render(truncateCells(name, nameBudget))
	gap := max(1, heroInnerW-lipgloss.Width(namePrefix)-lipgloss.Width(nameText)-lipgloss.Width(statusBadge))
	line1 := namePrefix + nameText + bgSpaces(gap, colors.SelectedBg) + statusBadge
	line2 := lipgloss.NewStyle().Foreground(colors.SelectedFg).Background(colors.SelectedBg).Faint(true).Render(truncateCells("  "+host+" · "+provider, heroInnerW))
	hero := lipgloss.NewStyle().Width(heroW).Background(colors.SelectedBg).Foreground(colors.SelectedFg).Bold(true).Padding(1, 1).Render(line1 + "\n" + line2)
	taskW := max(1, width-4)
	body := strings.Join([]string{
		shellTitleStyle.Render("Agent Communicator"),
		hero,
		currentTaskLine(row, taskW),
		nextTaskLine(row, taskW),
	}, "\n")
	return lipgloss.NewStyle().Width(width).Height(height).Padding(1, 1).Background(colors.RightColumnBg).Render(truncateLines(body, max(1, height-1)))
}

func currentTaskLine(row agentRow, width int) string {
	bg := colors.SelectedBg
	label := fgOnBg(colors.SelectedFg, bg).Bold(true).Render("Current")
	current := strings.TrimSpace(row.CurrentTask)
	if current == "" {
		value := fgOnBg(colors.Muted, bg).Faint(true).Render("No active task")
		return strings.Join([]string{
			padStyledLine(label, width, bg),
			padStyledLine(value, width, bg),
		}, "\n")
	}
	valueStyle := fgOnBg(currentTaskStatusColor(row.CurrentTaskStatus), bg).Bold(true)
	return wrappedAgentTaskBlock(label, current, width, 2, valueStyle, bg)
}

func nextTaskLine(row agentRow, width int) string {
	bg := colors.SelectedBg
	label := fgOnBg(colors.Accent, bg).Bold(true).Render("Next")
	next := strings.TrimSpace(row.CurrentTaskNextStep)
	if next == "" {
		next = "—"
	}
	return wrappedAgentTaskBlock(label, next, width, 2, fgOnBg(colors.Muted, bg), bg)
}

func currentTaskStatusColor(status string) lipgloss.Color {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "working", "ready", "active", "running":
		return colors.Success
	case "blocked", "error", "failed":
		return colors.Error
	case "review", "planning", "queued", "waiting", "pending":
		return colors.Warning
	case "done", "validated", "completed":
		return colors.Accent
	default:
		return colors.TextStrong
	}
}

func wrappedAgentTaskBlock(label, text string, width, maxValueLines int, valueStyle lipgloss.Style, bg lipgloss.Color) string {
	valueBudget := max(1, width)
	wrapped := wrapLine(text, valueBudget)
	if len(wrapped) > maxValueLines {
		wrapped = wrapped[:maxValueLines]
		wrapped[maxValueLines-1] = truncateCells(strings.TrimRight(wrapped[maxValueLines-1], " "), max(1, valueBudget-1)) + "…"
	}
	lines := []string{padStyledLine(label, width, bg)}
	for _, line := range wrapped {
		value := valueStyle.Render(truncateCells(line, valueBudget))
		lines = append(lines, padStyledLine(value, width, bg))
	}
	return strings.Join(lines, "\n")
}

func wrappedAgentTaskLine(label, separator, text string, width, maxLines int, valueStyle lipgloss.Style, bg lipgloss.Color) string {
	prefixWidth := lipgloss.Width(label) + lipgloss.Width(separator)
	valueBudget := max(1, width-prefixWidth)
	wrapped := wrapLine(text, valueBudget)
	if len(wrapped) > maxLines {
		wrapped = wrapped[:maxLines]
		wrapped[maxLines-1] = truncateCells(strings.TrimRight(wrapped[maxLines-1], " "), max(1, valueBudget-1)) + "…"
	}
	lines := make([]string, 0, len(wrapped))
	continuationPrefix := bgSpaces(prefixWidth, bg)
	for i, line := range wrapped {
		value := valueStyle.Render(truncateCells(line, valueBudget))
		if i == 0 {
			lines = append(lines, padStyledLine(label+separator+value, width, bg))
			continue
		}
		lines = append(lines, padStyledLine(continuationPrefix+value, width, bg))
	}
	return strings.Join(lines, "\n")
}

func (m model) switcherPanel(width, height int) string {
	hiddenRows := m.hiddenCount()
	shown := max(0, len(m.rows)-hiddenRows)
	hidden := hiddenRows + m.systemHiddenCount()
	headerRight := fmt.Sprintf("%d shown", shown)
	if hidden > 0 {
		headerRight = fmt.Sprintf("%d shown · %d hidden", shown, hidden)
	}
	headerTitle := fgOnBg(colors.Accent, colors.RightColumnBg).Bold(true).Render("Switch agent")
	headerCount := fgOnBg(colors.Muted, colors.RightColumnBg).Render(headerRight)
	headerGap := bgSpaces(max(1, width-4-lipgloss.Width("Switch agent")-lipgloss.Width(headerRight)), colors.RightColumnBg)
	header := headerTitle + headerGap + headerCount
	filter := lipgloss.NewStyle().Width(max(1, width-4)).Background(colors.PanelBg).Foreground(colors.Muted).Padding(0, 1).Render("⌕ filter agents…")
	list := m.agentList(width-2, max(1, height-lineCount(header)-lineCount(filter)-3))
	body := header + "\n" + filter + "\n" + list
	return lipgloss.NewStyle().Width(width).Height(height).Padding(1, 1).Background(colors.RightColumnBg).Render(truncateLines(body, max(1, height-1)))
}

func (m model) registryStatusLine() string {
	if m.healthErr != nil || m.agentListStale || m.err != nil {
		return "registry degraded"
	}
	if m.health.RegistryConnected != nil {
		if *m.health.RegistryConnected {
			return "registry online"
		}
		return "registry offline"
	}
	if m.health.Status != "" && m.health.Status != "ok" {
		return "registry " + m.health.Status
	}
	return "registry online"
}
func countOnlineRows(rows []agentRow) int {
	count := 0
	for _, row := range rows {
		switch strings.ToLower(strings.TrimSpace(row.Status)) {
		case "running", "active", "online", "idle", "ready":
			count++
		}
	}
	return count
}

func (m model) agentListTitle() string {
	title := "Agents"
	if m.mode == savedView {
		title = "Saved"
	}
	if m.agentListLoading {
		frames := []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}
		title += " " + mutedStyle.Render(frames[m.agentListFrame%len(frames)])
	}
	return titleStyle.Render(title)
}

func (m model) agentCard(row agentRow, selected bool, width int) string {
	cardWidth := max(8, width)
	inner := max(4, cardWidth-4)
	view := m.agentView(row)
	provider := strings.ToLower(view.ModelBadge)
	if provider == "??" {
		provider = "unknown"
	}

	bg := colors.RightColumnBg
	if selected {
		bg = colors.SelectedBg
	} else if m.hasUnread(row) {
		bg = colors.PanelBgAlt
	}

	unread := ""
	if view.UnreadCount > 0 {
		unread = bgSpaces(1, bg) + unreadCountBadge(view.UnreadCount)
	}

	limit := max(1, inner-2-lipgloss.Width(unread))
	suffix := ""
	if m.isHiddenAgent(row) {
		suffix = fgOnBg(colors.Muted, bg).Render(" ◌")
		limit = max(1, limit-2)
	}

	dot := agentStatusDotStyle(row).Background(bg).Render("●")
	space := bgSpaces(1, bg)

	nameStyle := fgOnBg(colors.Text, bg)
	if selected {
		nameStyle = fgOnBg(colors.SelectedFg, bg).Bold(true)
	} else if m.hasUnread(row) {
		nameStyle = fgOnBg(colors.TextStrong, bg).Bold(true)
	}
	nameStr := nameStyle.Render(truncateCells(view.Name, limit)) + suffix

	nameLine := dot + space + nameStr + unread

	metaLine := agentCardMetaLine(view, provider, bg, inner)

	contentW := max(1, cardWidth-2)
	return strings.Join([]string{
		bgSpaces(1, bg) + padStyledLine(nameLine, contentW, bg) + bgSpaces(1, bg),
		bgSpaces(1, bg) + padStyledLine(metaLine, contentW, bg) + bgSpaces(1, bg),
	}, "\n")
}

func agentCardMetaLine(view AgentView, provider string, bg lipgloss.Color, width int) string {
	host := fallback(view.HostnameLabel, localHostname())
	status := view.StatusLabel
	separator := fgOnBg(colors.Muted, bg).Render(" · ")
	statusText := fgOnBg(colors.Muted, bg).Render(status)
	available := max(1, width-lipgloss.Width(separator)-lipgloss.Width(statusText)-1)
	hostBudget := max(1, available-lipgloss.Width(provider))
	providerBudget := max(1, available-min(lipgloss.Width(host), hostBudget))
	hostText := fgOnBg(colors.AccentStrong, bg).Render(truncateCells(host, hostBudget))
	providerText := fgOnBg(colors.Accent, bg).Render(truncateCells(provider, providerBudget))
	left := hostText + separator + providerText
	gap := bgSpaces(max(1, width-lipgloss.Width(left)-lipgloss.Width(statusText)), bg)
	return left + gap + statusText
}

func (m model) hiddenSeparator(width int) string {
	return sectionHeaderStyle.Render(truncateCells("Hidden Agents", max(1, width-1)))
}

func compactCWD(cwd string) string {
	cwd = strings.TrimSpace(cwd)
	if cwd == "" || cwd == "unknown" || cwd == "unavailable" {
		return ""
	}
	cleaned := filepath.Clean(cwd)
	if cleaned == "." || cleaned == string(filepath.Separator) {
		return cleaned
	}
	parts := strings.FieldsFunc(cleaned, func(r rune) bool { return r == '/' || r == '\\' })
	kept := make([]string, 0, len(parts))
	for _, part := range parts {
		if part != "" {
			kept = append(kept, part)
		}
	}
	if len(kept) == 0 {
		return cleaned
	}
	if len(kept) > 2 {
		kept = kept[len(kept)-2:]
	}
	return strings.Join(kept, "/")
}

func (m model) agentList(width, height int) string {
	if m.mode == savedView {
		return m.savedAgentList(width, height)
	}
	if len(m.rows) == 0 {
		return mutedStyle.Render("no agents")
	}
	items := make([]struct {
		Index  int
		Row    agentRow
		Hidden bool
	}, 0, len(m.rows))
	activeLocalCount, activeRemoteCount, hiddenLocalCount, hiddenRemoteCount := 0, 0, 0, 0
	for i, row := range m.rows {
		hidden := m.isHiddenAgent(row)
		if row.Scope == "remote" {
			if hidden {
				hiddenRemoteCount++
			} else {
				activeRemoteCount++
			}
		} else if hidden {
			hiddenLocalCount++
		} else {
			activeLocalCount++
		}
		items = append(items, struct {
			Index  int
			Row    agentRow
			Hidden bool
		}{Index: i, Row: row, Hidden: hidden})
	}
	if len(items) == 0 {
		return mutedStyle.Render("no agents")
	}
	visible := max(1, height/agentCardHeight)
	offset := min(max(0, m.agentOffset), max(0, len(items)-visible))
	end := min(len(items), offset+visible)
	var b strings.Builder
	lastGroup := ""
	hiddenSeparatorWritten := false
	justWroteSeparator := false
	for pos := offset; pos < end; pos++ {
		item := items[pos]
		if item.Hidden && !hiddenSeparatorWritten {
			if b.Len() > 0 {
				b.WriteString("\n")
			}
			b.WriteString(m.hiddenSeparator(width) + "\n")
			hiddenSeparatorWritten = true
			justWroteSeparator = true
			lastGroup = ""
		}
		group := "LOCAL"
		count := activeLocalCount
		if item.Hidden {
			count = hiddenLocalCount
		}
		if item.Row.Scope == "remote" {
			group = "REMOTE"
			count = activeRemoteCount
			if item.Hidden {
				count = hiddenRemoteCount
			}
		}
		heading := fmt.Sprintf("%s (%d)", group, count)
		if heading != lastGroup {
			if b.Len() > 0 && !justWroteSeparator {
				b.WriteString("\n")
			}
			b.WriteString(sectionHeaderStyle.Render(truncateCells(heading, max(1, width-1))) + "\n")
			lastGroup = heading
		}
		justWroteSeparator = false
		b.WriteString(m.agentCard(item.Row, item.Index == m.selected, width-2))
		if pos < end-1 {
			b.WriteString("\n")
		}
	}
	if end < len(items) {
		b.WriteString("\n" + mutedStyle.Render("…"))
	}
	return truncateLines(b.String(), height)
}

func (m model) savedAgentList(width, height int) string {
	rows := m.savedRows()
	if len(rows) == 0 {
		return mutedStyle.Render("no saved messages")
	}
	visible := max(1, height/agentCardHeight)
	offset := min(max(0, m.agentOffset), max(0, len(rows)-visible))
	end := min(len(rows), offset+visible)
	var b strings.Builder
	for i := offset; i < end; i++ {
		b.WriteString(m.savedCard(rows[i], i == m.savedSelected, width-2))
		if i < end-1 {
			b.WriteString("\n")
		}
	}
	return truncateLines(b.String(), height)
}

func (m model) savedCard(row agentRow, selected bool, width int) string {
	count := 0
	for _, rec := range m.savedMessages {
		if fallback(rec.AgentName, rec.ConversationKey) == row.Name {
			count++
		}
	}
	copy := row
	copy.Scope = fmt.Sprintf("saved · %d", count)
	return m.agentCard(copy, selected, width)
}
