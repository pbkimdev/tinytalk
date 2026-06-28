package config

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/BurntSushi/toml"
)

// Backend represents a single LLM backend entry from config.
type Backend struct {
	Name        string `toml:"name"`
	Endpoint    string `toml:"endpoint"`
	APIKeyEnv   string `toml:"api_key_env"`
	Model       string `toml:"model"`
	Posture     string `toml:"posture"`
	// Capabilities controls which structured-output modes this backend supports.
	// Values: "tool_calling", "json_mode", "fenced_block" (always supported as fallback).
	Capabilities []string `toml:"capabilities"`
}

// Config is the root configuration structure.
type Config struct {
	Backends []Backend `toml:"backend"`
}

// Load reads the config file from the default location (~/.config/clite/config.toml).
// Returns a descriptive error if the file is missing or malformed.
func Load() (*Config, error) {
	path, err := defaultPath()
	if err != nil {
		return nil, err
	}
	return LoadFrom(path)
}

// LoadFrom reads config from an explicit path.
func LoadFrom(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("config file not found: %s (create it with at least one [[backend]] entry)", path)
		}
		return nil, fmt.Errorf("reading config %s: %w", path, err)
	}

	var cfg Config
	if _, err := toml.Decode(string(data), &cfg); err != nil {
		return nil, fmt.Errorf("parsing config %s: %w", path, err)
	}
	if len(cfg.Backends) == 0 {
		return nil, fmt.Errorf("config %s: no [[backend]] entries found", path)
	}
	return &cfg, nil
}

func defaultPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("resolving home directory: %w", err)
	}
	return filepath.Join(home, ".config", "clite", "config.toml"), nil
}
