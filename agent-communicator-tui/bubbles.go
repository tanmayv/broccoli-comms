package main

import (
	"strconv"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

var bubbleBorder = lipgloss.RoundedBorder()

const taskApprovalContentType = "application/vnd.broccoli.task-approval+json"
const taskUpdateContentType = "application/vnd.broccoli.task-update+json"
const memoryProposalContentType = "application/vnd.broccoli.memory-proposal+json"

func isApprovalRequestMessage(msg tracker.Message) bool {
	return msg.ContentType == taskApprovalContentType || msg.Kind == "task_completion_approval_request"
}

func isTaskUpdateMessage(msg tracker.Message) bool {
	return msg.ContentType == taskUpdateContentType || msg.Kind == "task_update" || msg.Kind == "task_status_changed"
}

func isCompactTaskUpdateMessage(msg tracker.Message) bool {
	return isTaskUpdateMessage(msg) && msg.ApprovalID == ""
}

func isSelectableTimelineMessage(msg tracker.Message) bool {
	return !isCompactTaskUpdateMessage(msg)
}

func isMemoryProposalMessage(msg tracker.Message) bool {
	return msg.ContentType == memoryProposalContentType || msg.Kind == "memory_proposal"
}

func renderTaskUpdateBody(msg tracker.Message, width int, bg lipgloss.Color, highlighted bool) string {
	parts := []string{fgOnBg(colors.AccentStrong, bg).Bold(true).Render("Task")}
	if msg.TaskID != "" {
		parts = append(parts, fgOnBg(colors.Muted, bg).Render(msg.TaskID))
	}
	if msg.TaskTitle != "" {
		parts = append(parts, fgOnBg(colors.TextStrong, bg).Bold(true).Render(msg.TaskTitle))
	}
	if msg.TaskStatus != "" {
		parts = append(parts, fgOnBg(colors.Accent, bg).Render(msg.TaskStatus))
	}
	lines := []string{strings.Join(parts, fgOnBg(colors.Muted, bg).Render(" · "))}
	if highlighted && msg.ResultSummary != "" {
		lines = append(lines, msg.ResultSummary)
	} else if msg.TaskTitle == "" && msg.TaskStatus == "" && msg.Body != "" {
		lines = append(lines, msg.Body)
	}
	if msg.TaskNextStep != "" {
		lines = append(lines, fgOnBg(colors.Muted, bg).Render("Next: ")+msg.TaskNextStep)
	}
	return strings.Join(lines, "\n")
}

func renderMemoryProposalBody(msg tracker.Message, width int, bg lipgloss.Color) string {
	lines := []string{fgOnBg(colors.AccentStrong, bg).Bold(true).Render("Memory proposal")}
	if msg.MemoryID != "" {
		lines = append(lines, "Memory: `"+msg.MemoryID+"`")
	}
	if msg.MemoryType != "" {
		lines = append(lines, "Type: `"+msg.MemoryType+"`")
	}
	if msg.MemoryScope != "" {
		lines = append(lines, "Scope: `"+msg.MemoryScope+"`")
	}
	if msg.MemoryVersion > 0 {
		lines = append(lines, "Version: `"+strconv.Itoa(msg.MemoryVersion)+"`")
	}
	if msg.SourceTaskID != "" {
		lines = append(lines, "Source task: `"+msg.SourceTaskID+"`")
	}
	if msg.MemoryTitle != "" {
		lines = append(lines, "", "## "+msg.MemoryTitle)
	}
	if msg.Body != "" {
		lines = append(lines, "", msg.Body)
	}
	lines = append(lines, "", fgOnBg(colors.Warning, bg).Render("Use Command palette → Memory Management to review, approve, edit in EDITOR/nvim, reject/revoke, or roll back memory."))
	return renderMarkdown(strings.Join(lines, "\n"), width, bg)
}

func renderApprovalRequestBody(msg tracker.Message, width int, bg lipgloss.Color) string {
	lines := []string{fgOnBg(colors.Warning, bg).Bold(true).Render("Task-chain approval request")}
	if msg.ApprovalID != "" {
		lines = append(lines, "Approval: `"+msg.ApprovalID+"`")
	}
	if msg.TaskChainID != "" {
		lines = append(lines, "Task chain: `"+msg.TaskChainID+"`")
	}
	if msg.RootTaskID != "" {
		lines = append(lines, "Root task: `"+msg.RootTaskID+"`")
	}
	if msg.TaskID != "" {
		lines = append(lines, "Submitted task: `"+msg.TaskID+"`")
	}
	if msg.TaskTitle != "" {
		lines = append(lines, "Title: "+msg.TaskTitle)
	}
	if msg.TaskVersionAtSubmission > 0 {
		lines = append(lines, "Task version: `"+strconv.Itoa(msg.TaskVersionAtSubmission)+"`")
	}
	if msg.Source != "" {
		lines = append(lines, "Source: "+msg.Source)
	}
	lines = append(lines, fgOnBg(colors.Warning, bg).Render("Inbox approval metadata is an untrusted hint; fallback body is hidden. Use `/approval good|bad|need_improvements <approval_id>` to load the durable task-chain approval and review."))
	return renderMarkdown(strings.Join(lines, "\n"), width, bg)
}

func (m model) messageBubbleLines(msg tracker.Message, index, width int) []string {
	start := time.Now()
	defer func() {
		debugLogf("message_bubble duration=%s index=%d body_bytes=%d markdown=%t", time.Since(start), index, len(msg.Body), msg.ContentType == "" || msg.ContentType == "text/markdown")
	}()
	colorKey := m.messageColorKey(msg)
	body := msg.Body
	innerWidth := max(8, width-8)
	useBg := shouldRenderIncomingBubbleBackground(m.mode, msg) && width >= 70
	if isCompactTaskUpdateMessage(msg) {
		useBg = false
	}
	displayBody, isPaneCapture := paneCaptureDisplayBody(body)
	bodyBg := colors.BaseBg
	if useBg {
		bodyBg = colors.IncomingBubbleBg
		if (isTaskUpdateMessage(msg) && msg.ApprovalID != "") || isMemoryProposalMessage(msg) {
			bodyBg = colors.TaskUpdateBg
		} else if isPaneCapture {
			bodyBg = colors.CapturePaneBg
		}
	}
	if isPaneCapture {
		body = displayBody
	} else if isTaskUpdateMessage(msg) && msg.ApprovalID != "" {
		body = renderApprovalRequestBody(msg, innerWidth, bodyBg)
	} else if isTaskUpdateMessage(msg) {
		body = renderTaskUpdateBody(msg, innerWidth, bodyBg, index == m.messageSelected && isSelectableTimelineMessage(msg))
	} else if isMemoryProposalMessage(msg) {
		body = renderMemoryProposalBody(msg, innerWidth, bodyBg)
	} else if isApprovalRequestMessage(msg) {
		body = renderApprovalRequestBody(msg, innerWidth, bodyBg)
	} else if msg.ContentType == "" || msg.ContentType == "text/markdown" {
		body = renderMarkdown(body, innerWidth, bodyBg)
	}
	rowBg := colors.BaseBg
	if useBg {
		rowBg = bodyBg
	}
	compactTaskUpdate := isCompactTaskUpdateMessage(msg)
	railStyle := fgOnBg(colors.Muted, rowBg)
	if index == m.messageSelected && isSelectableTimelineMessage(msg) {
		railStyle = fgOnBg(colors.Accent, rowBg).Bold(true)
	}
	rail := railStyle.Render("┃")
	indent := bgSpaces(2, rowBg)
	if compactTaskUpdate {
		rail = ""
	}
	wrapWidth := innerWidth
	bubblePadX := 2
	if useBg {
		wrapWidth = max(6, innerWidth-(bubblePadX*2))
	}

	renderIncomingStyledBubbleLine := func(content string) string {
		if content == "" {
			return bgSpaces(innerWidth, bodyBg)
		}
		return bgSpaces(bubblePadX, bodyBg) + padStyledLine(content, wrapWidth, bodyBg) + bgSpaces(bubblePadX, bodyBg)
	}

	renderIncomingBubbleLine := func(content string) string {
		if content == "" {
			return renderIncomingStyledBubbleLine("")
		}
		fg := colors.Text
		if isPaneCapture {
			fg = colors.Success
		}
		content = fgOnBg(fg, bodyBg).Render(content)
		return renderIncomingStyledBubbleLine(content)
	}

	headerBg := colors.BaseBg
	headerWidth := innerWidth
	if useBg {
		headerBg = bodyBg
		headerWidth = wrapWidth
	}
	header := m.messageHeader(msg, index, colorKey, headerWidth, headerBg)
	out := []string{}
	if useBg {
		blank := renderIncomingBubbleLine("")
		out = append(out, padStyledLine(rail+indent+blank, width, rowBg))
		header = renderIncomingBubbleLine(header)
	}
	if !isCompactTaskUpdateMessage(msg) {
		out = append(out, padStyledLine(rail+indent+header, width, rowBg))
	}

	for _, line := range m.visibleBodyLines(bubbleBodyLines(body, wrapWidth), index) {
		line = truncateCells(line, wrapWidth)
		if useBg {
			line = renderIncomingBubbleLine(line)
		} else {
			line = fgOnBg(colors.Text, colors.BaseBg).Render(line)
		}
		out = append(out, padStyledLine(rail+indent+line, width, rowBg))
	}
	if receipt := sentReceiptLine(msg, bodyBg); receipt != "" {
		if useBg {
			receipt = renderIncomingStyledBubbleLine(receipt)
			out = append(out, padStyledLine(rail+indent+receipt, width, rowBg))
		} else {
			receipt = sentReceiptLine(msg, colors.BaseBg)
			out = append(out, padStyledLine(rail+indent+receipt, width, colors.BaseBg))
		}
	}
	if useBg {
		blank := renderIncomingBubbleLine("")
		out = append(out, padStyledLine(rail+indent+blank, width, rowBg))
	}
	return out
}

func (m model) messageHeader(msg tracker.Message, index int, colorKey string, width int, bg lipgloss.Color) string {
	sender := fallback(msg.Sender, "unknown")
	if m.mode == advancedView && !strings.Contains(sender, "→") && !strings.HasPrefix(sender, "to ") {
		sender += " → " + fallback(m.ownName, "agent-communicator")
	}
	saved := ""
	if m.isSavedMessage(msg) {
		saved = "★ "
	}
	label := messageSenderLabel(msg, sender)
	headerStyle := fgOnBg(colors.AgentColors[agentColorIndex(colorKey)], bg).Bold(true)
	header := headerStyle.Render(truncateCells(saved+label, max(1, width-25)))
	if ts := formatDisplayTime(msg.Timestamp); ts != "" && lipgloss.Width(header)+1 < width {
		if m.isSavedMessage(msg) {
			ts += " ★"
		}
		header += bgSpaces(1, bg) + fgOnBg(colors.Muted, bg).Render(truncateCells(ts, width-lipgloss.Width(header)-1))
	}
	return header
}

func messageSenderLabel(msg tracker.Message, fallbackSender string) string {
	if isSentMessage(msg) {
		return fallbackSender
	}
	parts := []string{}
	if badge := messageSenderBadge(msg); badge != "??" {
		parts = append(parts, badge)
	}
	parts = append(parts, fallback(fallbackSender, "unknown"))
	if host := strings.TrimSpace(msg.SenderHostname); host != "" {
		parts = append(parts, "@ "+host)
	}
	return strings.Join(parts, " ")
}

func messageSenderBadge(msg tracker.Message) string {
	return modelBadge(agentRow{ModelType: msg.SenderModelType, AgentCmd: fallback(msg.SenderAgentCmd, msg.SenderAgentType)})
}

func renderBubble(lines []string, innerWidth int, color lipgloss.Color, outgoing, selected bool) []string {
	border := lipgloss.NewStyle().Foreground(color)
	left, right := "│", "│"
	topLeft, topRight, bottomLeft, bottomRight, horizontal := "╭", "╮", "╰", "╯", "─"
	if outgoing {
		left = "║"
	} else {
		right = "║"
	}
	if selected {
		left, right = "║", "║"
		topLeft, topRight, bottomLeft, bottomRight, horizontal = "╔", "╗", "╚", "╝", "═"
	}
	out := []string{border.Render(topLeft + strings.Repeat(horizontal, innerWidth+2) + topRight)}
	for _, line := range lines {
		cell := lipgloss.PlaceHorizontal(innerWidth, lipgloss.Left, truncateCells(line, innerWidth))
		out = append(out, border.Render(left)+" "+cell+" "+border.Render(right))
	}
	out = append(out, border.Render(bottomLeft+strings.Repeat(horizontal, innerWidth+2)+bottomRight))
	return out
}

func indentLines(lines []string, spaces int) []string {
	if spaces <= 0 {
		return lines
	}
	prefix := strings.Repeat(" ", spaces)
	out := make([]string, len(lines))
	for i, line := range lines {
		out[i] = prefix + line
	}
	return out
}

func bubbleBodyLines(body string, wrapWidth int) []string {
	var out []string
	for _, line := range strings.Split(body, "\n") {
		out = append(out, wrapLine(line, wrapWidth)...)
	}
	return out
}

func isSentMessage(msg tracker.Message) bool {
	return msg.Sender == "You" || strings.Contains(msg.Sender, "→") || strings.HasPrefix(msg.Sender, "to ")
}

func shouldRenderIncomingBubbleBackground(mode viewMode, msg tracker.Message) bool {
	return !isSentMessage(msg)
}

func (m model) messageColorKey(msg tracker.Message) string {
	sender := strings.TrimSpace(msg.Sender)
	if strings.HasPrefix(sender, "to ") {
		return strings.TrimSpace(strings.TrimPrefix(sender, "to "))
	}
	if strings.Contains(sender, "→") {
		parts := strings.Split(sender, "→")
		if strings.TrimSpace(parts[0]) == m.ownName || strings.TrimSpace(parts[0]) == "agent-communicator" {
			return strings.TrimSpace(parts[len(parts)-1])
		}
	}
	return senderColorKey(sender)
}

func (m model) messageBorderColor(msg tracker.Message, colorKey string) lipgloss.Color {
	if m.isSavedMessage(msg) {
		return colors.Saved
	}
	if isSentMessage(msg) {
		return colors.Info
	}
	return colors.AgentColors[agentColorIndex(colorKey)]
}
