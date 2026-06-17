package main

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

func responsivePanelPadding(width int, wide bool) int {
	if !wide {
		return 1
	}
	return 3
}

func responsivePanelPaddingForWidth(width int, wide bool) int {
	if !wide || width < 70 {
		return 1
	}
	return 3
}

func responsiveInputPadding(terminalWidth int) int {
	if terminalWidth < 70 {
		return 1
	}
	return 2
}

func contentDetailLayoutWidths(width int) (int, int) {
	right := min(42, max(28, (width*35)/100))
	if width < 100 {
		right = min(34, max(24, (width*35)/100))
	}
	return max(10, width-right), right
}

func renderInputSurface(width, terminalWidth int, contentLines []string, bg lipgloss.Color) string {
	padX := responsiveInputPadding(terminalWidth)
	inner := max(1, width-(padX*2))
	blank := bgSpaces(width, bg)
	lines := []string{blank}
	if len(contentLines) == 0 {
		contentLines = []string{""}
	}
	for _, line := range contentLines {
		lines = append(lines, bgSpaces(padX, bg)+padStyledLine(line, inner, bg)+bgSpaces(padX, bg))
	}
	lines = append(lines, blank)
	return strings.Join(lines, "\n")
}
