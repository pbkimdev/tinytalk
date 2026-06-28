package tier

import (
	"context"
	"errors"
	"testing"

	"github.com/paulbkim-dev/clite/internal/contract"
)

// mockProvider is a test double for provider.Provider.
type mockProvider struct {
	result contract.Result
	err    error
}

func (m *mockProvider) Complete(_ context.Context, _ string) (contract.Result, error) {
	return m.result, m.err
}

func TestRun_ProviderSuccess(t *testing.T) {
	expected := contract.Result{
		Command:     "ls -la",
		Explanation: "list files",
		Danger:      contract.DangerSafe,
		Confidence:  0.9,
		Needs:       []string{"ls"},
	}
	tier := New(&mockProvider{result: expected})
	got, err := tier.Run(context.Background(), "list files")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Command != expected.Command {
		t.Errorf("command = %q, want %q", got.Command, expected.Command)
	}
}

func TestRun_ProviderError(t *testing.T) {
	providerErr := errors.New("all structured-output strategies failed")
	tier := New(&mockProvider{err: providerErr})
	_, err := tier.Run(context.Background(), "some prompt")
	if err == nil {
		t.Fatal("expected error from failing provider, got nil")
	}
	// Must never return a partial (zero) Result when error is present.
}

func TestRun_CacheMissCallsProvider(t *testing.T) {
	called := false
	p := &callTrackingProvider{onCall: func() { called = true }}
	tier := New(p)
	_, _ = tier.Run(context.Background(), "prompt")
	if !called {
		t.Error("expected provider to be called on cache miss")
	}
}

type callTrackingProvider struct {
	onCall func()
}

func (p *callTrackingProvider) Complete(_ context.Context, _ string) (contract.Result, error) {
	p.onCall()
	return contract.Result{
		Command:     "echo hi",
		Explanation: "greet",
		Danger:      contract.DangerSafe,
		Confidence:  1.0,
		Needs:       []string{"echo"},
	}, nil
}
