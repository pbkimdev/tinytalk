package provider

import (
	"context"

	"github.com/paulbkim-dev/clite/internal/contract"
)

// Provider is the interface all LLM backends must satisfy.
type Provider interface {
	Complete(ctx context.Context, prompt string) (contract.Result, error)
}
