package main

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func (m model) conversationPanel(width, height int) string {
	defer debugSince("conversation_panel", time.Now())
	padX := responsivePanelPaddingForWidth(width, width >= 70)
	innerW := max(1, width-(padX*2))
	title := titleStyle.Render(m.conversationTitle())
	composer := m.composerBox(innerW)
	if !m.activeTabCanCompose() {
		composer = mutedStyle.Render(m.disabledComposerText())
	}
	messageH := max(1, height-lineCount(title)-lineCount(composer)-3)
	messages := m.messageViewWithHeight(innerW, messageH)
	body := title + "\n" + composer + "\n" + bgSpaces(innerW, colors.BaseBg) + "\n" + messages
	return lipgloss.NewStyle().
		Width(width).
		Height(height).
		MaxWidth(width).
		MaxHeight(height).
		Padding(1, padX, 0, padX).
		Background(colors.BaseBg).
		Render(body)
}

func (m model) conversationTitle() string {
	return viewModeLabel(m.mode, false)
}
func (m model) messageView(width int) string {
	return m.messageViewWithHeight(width, m.messageVisibleLines())
}

func (m model) messageViewWithHeight(width, visible int) string {
	defer debugSince("message_view", time.Now())
	return m.messageViewportPanel(width, visible).View()
}

func (m model) messageViewportPanel(width, visible int) ViewportPanel {
	return NewViewportPanel(width, visible, m.messageOffset, colors.BaseBg, m.messageLinesForWidth(width))
}

func (m model) messageLinesForWidth(width int) []string {
	start := time.Now()
	wrapWidth := max(10, width)
	messages := m.displayOrderedMessages()
	defer func() {
		debugLogf("message_lines duration=%s messages=%d width=%d", time.Since(start), len(messages), width)
	}()
	systemEvents := m.displayOrderedSystemEvents()
	if len(messages) == 0 && len(systemEvents) == 0 {
		if len(m.rows) > 0 && m.rows[m.selected].Scope == "remote" {
			return wrapBackgroundStyledText("No messages. Remote history is in-memory for sent messages only.", wrapWidth, colors.Muted, colors.BaseBg)
		}
		return wrapBackgroundStyledText("No messages. Inbox history loads for local agents.", wrapWidth, colors.Muted, colors.BaseBg)
	}
	cacheKey := messageRenderCacheKey(m, messages, systemEvents, wrapWidth)
	if lines, ok := cachedMessageLines(cacheKey); ok {
		debugLogf("message_lines cache=hit messages=%d width=%d", len(messages), width)
		return lines
	}
	debugLogf("message_lines cache=miss messages=%d width=%d", len(messages), width)
	lines := []string{}
	for i, msg := range messages {
		if len(lines) > 0 {
			lines = append(lines, bgSpaces(wrapWidth, colors.BaseBg))
		}
		lines = append(lines, m.messageBubbleLines(msg, i, wrapWidth)...)
	}
	for _, event := range systemEvents {
		if len(lines) > 0 {
			lines = append(lines, bgSpaces(wrapWidth, colors.BaseBg))
		}
		lines = append(lines, m.systemEventLine(event, wrapWidth))
	}
	storeMessageLines(cacheKey, lines)
	return lines
}

func sentReadMarker(msg tracker.Message, bgOpt ...lipgloss.Color) string {
	if !isSentMessage(msg) {
		return ""
	}
	bg := colors.BaseBg
	if len(bgOpt) > 0 {
		bg = bgOpt[0]
	}
	if msg.Read {
		return fgOnBg(colors.ReadTick, bg).Render("✓✓")
	}
	if msg.Notified {
		return fgOnBg(colors.DeliveredTick, bg).Render("✓✓")
	}
	if msg.Delivered {
		return fgOnBg(colors.DeliveredTick, bg).Render("✓✓")
	}
	return fgOnBg(colors.SentTick, bg).Render("✓")
}

func sentReceiptLine(msg tracker.Message, bg lipgloss.Color) string {
	if !isSentMessage(msg) {
		return ""
	}
	state := "sent"
	if msg.Delivered || msg.Notified {
		state = "delivered"
	}
	if msg.Read {
		state = "read"
	}
	marker := sentReadMarker(msg, bg)
	text := state
	if ts := formatDisplayTime(msg.Timestamp); ts != "" {
		text += " · " + ts
	}
	return marker + bgSpaces(1, bg) + fgOnBg(colors.Muted, bg).Render(text)
}

func messageBodyLines(body string, wrapWidth int) []string {
	var out []string
	for _, line := range strings.Split(body, "\n") {
		out = append(out, wrapLine("    "+line, wrapWidth)...)
	}
	return out
}

func (m model) visibleBodyLines(lines []string, index int) []string {
	if m.mode != advancedView || index == m.messageSelected || index == 0 || len(lines) <= 3 {
		return lines
	}
	return append(append([]string{}, lines[:3]...), "    …")
}

func (m model) messageContentWidth() int {
	chat, _, _ := m.layoutWidths()
	return max(1, chat-6)
}

func (m model) messageChromeHeight() int {
	chat, _, _ := m.layoutWidths()
	return lineCount(titleStyle.Render(m.conversationTitle()) + "\n" + m.composerBox(max(1, chat-6)))
}

func (m model) messageVisibleLines() int {
	bottomH := lineCount(m.footer(max(1, m.width))) + lineCount(m.bottomTabBar(max(1, m.width)))
	return max(1, m.height-bottomH-m.messageChromeHeight())
}
func messagePageSize(height int) int             { return viewportPageSize(height) }
func messageBottomOffset(total, visible int) int { return viewportBottomOffset(total, visible) }
func clampMessageOffset(offset, total, visible int) int {
	return clampViewportOffset(offset, total, visible)
}

func (m model) scrollHint() string {
	lines, visible := len(m.messageLinesForWidth(m.messageContentWidth())), m.messageVisibleLines()
	if lines <= visible {
		return mutedStyle.Render("c-u/c-d scroll messages")
	}
	offset := clampMessageOffset(m.messageOffset, lines, visible)
	return mutedStyle.Render(fmt.Sprintf("messages %d-%d/%d · c-u/c-d", offset+1, min(lines, offset+visible), lines))
}
