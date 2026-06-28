package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadFrom_MissingFile(t *testing.T) {
	_, err := LoadFrom("/nonexistent/path/config.toml")
	if err == nil {
		t.Fatal("expected error for missing file, got nil")
	}
	if !strings.Contains(err.Error(), "/nonexistent/path/config.toml") {
		t.Errorf("error should mention path, got: %v", err)
	}
}

func TestLoadFrom_ValidTOML(t *testing.T) {
	content := `
[[backend]]
name = "local"
endpoint = "http://localhost:11434/v1"
api_key_env = ""
model = "qwen2.5:7b"
posture = "local"
capabilities = ["tool_calling", "json_mode"]

[[backend]]
name = "anthropic"
endpoint = "https://api.anthropic.com/v1"
api_key_env = "ANTHROPIC_API_KEY"
model = "claude-sonnet-4-6"
posture = "cloud"
capabilities = []
`
	path := filepath.Join(t.TempDir(), "config.toml")
	if err := os.WriteFile(path, []byte(content), 0600); err != nil {
		t.Fatal(err)
	}

	cfg, err := LoadFrom(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(cfg.Backends) != 2 {
		t.Fatalf("expected 2 backends, got %d", len(cfg.Backends))
	}

	local := cfg.Backends[0]
	if local.Name != "local" {
		t.Errorf("backend[0].name = %q, want %q", local.Name, "local")
	}
	if local.Endpoint != "http://localhost:11434/v1" {
		t.Errorf("backend[0].endpoint = %q", local.Endpoint)
	}
	if len(local.Capabilities) != 2 {
		t.Errorf("backend[0].capabilities = %v, want 2 entries", local.Capabilities)
	}

	anth := cfg.Backends[1]
	if anth.APIKeyEnv != "ANTHROPIC_API_KEY" {
		t.Errorf("backend[1].api_key_env = %q", anth.APIKeyEnv)
	}
}

func TestLoadFrom_EmptyBackends(t *testing.T) {
	content := `# no backends`
	path := filepath.Join(t.TempDir(), "config.toml")
	if err := os.WriteFile(path, []byte(content), 0600); err != nil {
		t.Fatal(err)
	}

	_, err := LoadFrom(path)
	if err == nil {
		t.Fatal("expected error for empty backends, got nil")
	}
	if !strings.Contains(err.Error(), "no [[backend]]") {
		t.Errorf("error should mention missing backends, got: %v", err)
	}
}

func TestLoadFrom_MalformedTOML(t *testing.T) {
	content := `this is not valid = toml = at all`
	path := filepath.Join(t.TempDir(), "config.toml")
	if err := os.WriteFile(path, []byte(content), 0600); err != nil {
		t.Fatal(err)
	}

	_, err := LoadFrom(path)
	if err == nil {
		t.Fatal("expected error for malformed TOML, got nil")
	}
}
