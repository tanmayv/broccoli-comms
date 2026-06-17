package main

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/config"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

func TestBroccoliAgentTrackerCommandPrefersBroccoliWrapperEnv(t *testing.T) {
	config.ResetForTest()
	t.Cleanup(config.ResetForTest)
	t.Setenv("BROCCOLI_COMMS_CLI", "/nix/store/bin/broccoli-comms")
	cmd := broccoliAgentTrackerCommandContext(context.Background(), "list")
	want := []string{"/nix/store/bin/broccoli-comms", "agent-tracker", "list"}
	if !equalStrings(cmd.Args, want) {
		t.Fatalf("cmd.Args = %#v, want %#v", cmd.Args, want)
	}
}

func TestBroccoliAgentTrackerCommandIgnoresConfiguredExecutablePaths(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "broccoli-comms")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	configText := "[executables]\n" +
		"agent_tracker_ctl = \"/nix/store/example/bin/agent-tracker-ctl\"\n" +
		"agent_tracker_ctl_py = \"/nix/store/example/agent-tracker-ctl.py\"\n"
	if err := os.WriteFile(filepath.Join(configDir, "config.toml"), []byte(configText), 0o644); err != nil {
		t.Fatal(err)
	}
	config.ResetForTest()
	t.Cleanup(config.ResetForTest)
	t.Setenv("XDG_CONFIG_HOME", dir)
	t.Setenv("BROCCOLI_COMMS_CLI", "")

	cmd := broccoliAgentTrackerCommandContext(context.Background(), "list")
	want := []string{"broccoli-comms", "agent-tracker", "list"}
	if !equalStrings(cmd.Args, want) {
		t.Fatalf("cmd.Args = %#v, want %#v", cmd.Args, want)
	}
}

func TestBroccoliAgentTrackerCommandDefaultsToWrapperStyle(t *testing.T) {
	config.ResetForTest()
	t.Cleanup(config.ResetForTest)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("BROCCOLI_COMMS_CLI", "")
	cmd := broccoliAgentTrackerCommandContext(context.Background(), "list")
	want := []string{"broccoli-comms", "agent-tracker", "list"}
	if !equalStrings(cmd.Args, want) {
		t.Fatalf("cmd.Args = %#v, want %#v", cmd.Args, want)
	}
}

func TestLoadAgentsPrefersLiveTrackerList(t *testing.T) {
	local := &fakeLocal{agents: map[string]tracker.Agent{
		"live-coder": {Status: "idle", Scope: "local", TargetAddress: "live-coder"},
	}}
	rows, err := loadAgentsFromBroccoliComms(context.Background(), local)
	if err != nil {
		t.Fatalf("loadAgentsFromBroccoliComms returned error: %v", err)
	}
	if len(rows) != 1 || rows[0].Name != "live-coder" {
		t.Fatalf("rows = %#v, want only live tracker row", rows)
	}
	if !local.listOptions.IncludeRemote {
		t.Fatalf("ListWithOptions IncludeRemote = false, want true")
	}
}

func TestLoadAgentsFallbackFiltersConfiguredOfflineRows(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell script test")
	}
	dir := t.TempDir()
	cli := filepath.Join(dir, "broccoli-comms")
	script := `#!/bin/sh
cat <<'JSON'
{"agents":{"live":{"name":"live","running":true,"status":"idle","cwd":"/repo"},"configured-offline":{"name":"configured-offline","is_configured":true,"running":false,"launchable":true,"status":"stopped"},"remote-live":{"name":"remote-live","scope_kind":"remote","running":true,"target_address":"host/remote-live","hostname":"host"}}}
JSON
`
	if err := os.WriteFile(cli, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("BROCCOLI_COMMS_CLI", cli)

	rows, err := loadAgentsFromBroccoliComms(context.Background(), nil)
	if err != nil {
		t.Fatalf("loadAgentsFromBroccoliComms returned error: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("rows = %#v, want only two running rows", rows)
	}
	for _, row := range rows {
		if row.Name == "configured-offline" || boolPtrFalse(row.Running) {
			t.Fatalf("offline row was included: %#v", rows)
		}
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func boolPtrFalse(b *bool) bool {
	return b != nil && !*b
}
