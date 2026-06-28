package tier

import (
	"context"
	"fmt"

	"github.com/paulbkim-dev/clite/internal/contract"
	"github.com/paulbkim-dev/clite/internal/provider"
)

// Tier orchestrates the T0/T1/T2 execution ladder.
type Tier struct {
	provider provider.Provider
}

// New constructs a Tier with the given provider.
func New(p provider.Provider) *Tier {
	return &Tier{provider: p}
}

// Run executes the tiered pipeline for a prompt and returns a Result.
//
// T0: exact-match cache (stub — always misses in v1).
// T1: grounded-lite call to the provider.
// T2: on-demand help fetch + re-ask (hook left for grounding issue).
func (t *Tier) Run(ctx context.Context, prompt string) (contract.Result, error) {
	// T0: cache lookup (stub; grounding issue will implement the real cache).
	if hit, ok := t.cacheGet(prompt); ok {
		return hit, nil
	}

	// T1: single grounded-lite call.
	result, err := t.provider.Complete(ctx, prompt)
	if err != nil {
		return contract.Result{}, fmt.Errorf("T1 provider call failed: %w", err)
	}

	// T2 hook: if the provider signals low confidence or unknown tools,
	// fetch --help/man for those tools and retry (deferred to grounding issue).

	return result, nil
}

// cacheGet is a stub that always returns a cache miss.
// The real implementation (T0 exact cache) is deferred to the caching issue.
func (t *Tier) cacheGet(_ string) (contract.Result, bool) {
	return contract.Result{}, false
}
