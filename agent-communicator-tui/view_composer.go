package main

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

func (m model) composerBox(width int) string {
	input := m.composerInputBox(width)
	controls := m.composerModeControls(width)
	if controls == "" {
		return input
	}
	return input + "\n" + controls
}

func (m model) composerInputBox(width int) string {
	padX := responsiveInputPadding(m.width)
	inner := max(1, width-(padX*2))
	return ComposerInputSurface{Width: width, TerminalWidth: m.width, Lines: m.composerLines(inner)}.View()
}
func (m model) composerView(width int) string {
	return lipgloss.NewStyle().Width(max(1, width)).Render(strings.Join(m.composerLines(width), "\n"))
}

func (m model) composerLines(width int) []string {
	lineWidth := max(1, width-1)
	prefix := m.composerPrefix()
	prefixW := lipgloss.Width(prefix)
	textStyle := fgOnBg(colors.Text, colors.InputBg)
	placeholderStyle := fgOnBg(colors.Muted, colors.InputBg)
	cursorStyle := fgOnBg(colors.Success, colors.InputBg).Bold(true)
	cursor := ""
	cursorW := 0
	if !m.messageFocused && !m.cursorHidden {
		cursor = cursorStyle.Render("█")
		cursorW = 1
	}

	if len(m.composer) == 0 {
		placeholder := m.composerPlaceholder()
		if m.agentListStale {
			placeholder = "agent tracker unavailable; sending disabled"
		}
		available := max(1, lineWidth-prefixW-cursorW)
		line := prefix + cursor + placeholderStyle.Render(truncateCells(placeholder, available))
		return []string{padStyledLine(line, lineWidth, colors.InputBg)}
	}

	chunks := wrapCells(string(m.composer), max(1, lineWidth-prefixW), lineWidth)
	if len(chunks) == 0 {
		chunks = []string{""}
	}
	if cursor != "" {
		last := len(chunks) - 1
		limit := lineWidth
		if last == 0 {
			limit = max(1, lineWidth-prefixW)
		}
		if lipgloss.Width(chunks[last])+cursorW > limit {
			chunks = append(chunks, "")
		}
	}

	lines := make([]string, 0, len(chunks))
	for i, chunk := range chunks {
		content := textStyle.Render(chunk)
		if i == len(chunks)-1 {
			content += cursor
		}
		line := content
		if i == 0 {
			line = prefix + content
		}
		lines = append(lines, padStyledLine(line, lineWidth, colors.InputBg))
	}
	bodyMaxLines := max(1, composerMaxLines)
	if len(lines) > bodyMaxLines {
		lines = lines[len(lines)-bodyMaxLines:]
	}
	return lines
}

func (m model) composerPrefix() string {
	label := "/" + m.activeComposerModeName()
	if label == "/key" {
		label = "/keys"
	}
	return lipgloss.NewStyle().Background(colors.InputBg).Foreground(colors.Accent).Bold(true).Padding(0, 1).Render(label) + bgSpaces(1, colors.InputBg)
}

func wrapCells(s string, firstWidth, nextWidth int) []string {
	if s == "" {
		return nil
	}
	widths := []int{max(1, firstWidth)}
	var chunks []string
	var b strings.Builder
	currentWidth := 0
	limit := widths[0]
	for _, r := range s {
		rw := lipgloss.Width(string(r))
		if currentWidth > 0 && currentWidth+rw > limit {
			chunks = append(chunks, b.String())
			b.Reset()
			currentWidth = 0
			limit = max(1, nextWidth)
		}
		b.WriteRune(r)
		currentWidth += rw
	}
	if b.Len() > 0 {
		chunks = append(chunks, b.String())
	}
	return chunks
}

func (m model) composerPlaceholder() string {
	if m.inputMode == inputModeText {
		return "type pane text…"
	}
	if m.inputMode == inputModeKeys {
		return "type key tokens…"
	}
	return ""
}

func (m model) disabledComposerText() string {
	return viewModeLabel(m.mode, false)
}

type inputModeButton struct {
	Mode  inputMode
	Name  string
	Label string
}

func inputModeButtons() []inputModeButton {
	return []inputModeButton{
		{Mode: inputModeMessage, Name: "msg", Label: "/msg"},
		{Mode: inputModeText, Name: "text", Label: "/text"},
		{Mode: inputModeKeys, Name: "key", Label: "/keys"},
	}
}

func (m model) composerModeHint(width int) string {
	return m.composerModeControls(width)
}

func (m model) activeComposerModeName() string {
	action := composerActionForMode(string(m.composer), m.inputMode)
	active := m.inputMode.name()
	if slashComposerCommand(string(m.composer)) {
		switch action.Kind {
		case "direct_text":
			active = "text"
		case "direct_keys":
			active = "key"
		default:
			active = "msg"
		}
	}
	return active
}

func (m model) composerModeControls(width int) string {
	leftText := "/msg sends an inbox message"
	left := fgOnBg(colors.Muted, colors.BaseBg).Render(leftText)
	right := fgOnBg(colors.Muted, colors.BaseBg).Render("Enter send")
	gap := max(1, width-lipgloss.Width(left)-lipgloss.Width(right))
	return left + bgSpaces(gap, colors.BaseBg) + right
}

func (m model) composerModeButtons(width int) string {
	active := m.activeComposerModeName()
	buttons := []string{}
	for i, button := range inputModeButtons() {
		if i > 0 {
			buttons = append(buttons, lipgloss.NewStyle().Width(1).Height(3).Render(""))
		}
		style := modeTabStyle
		if active == button.Name {
			style = activeModeTabStyle
		}
		buttons = append(buttons, style.Render(button.Label))
	}
	return lipgloss.JoinHorizontal(lipgloss.Top, buttons...)
}

func (m model) composerModeDescription(width int) string {
	description := "Send chat message."
	switch m.activeComposerModeName() {
	case "text":
		description = "Type into agent pane."
	case "key":
		description = "Send keys to agent pane."
	}
	return mutedStyle.Render(truncateCells(description, max(1, width-1)))
}
