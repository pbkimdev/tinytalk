package openai

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/paulbkim-dev/clite/internal/config"
	"github.com/paulbkim-dev/clite/internal/contract"
)

// Client is an OpenAI-compatible /chat/completions client.
// It covers local endpoints (Ollama, llama.cpp) and cloud endpoints
// (Anthropic's OpenAI-compat layer) via the same HTTP path.
type Client struct {
	backend    config.Backend
	httpClient *http.Client
}

// New constructs a Client from a backend config entry.
func New(b config.Backend) *Client {
	return &Client{
		backend: b,
		httpClient: &http.Client{
			Timeout: 120 * time.Second,
		},
	}
}

// chatMessage is a single message in the /chat/completions request.
type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// chatRequest is the body sent to /chat/completions.
type chatRequest struct {
	Model          string        `json:"model"`
	Messages       []chatMessage `json:"messages"`
	Temperature    float64       `json:"temperature"`
	ResponseFormat *respFormat   `json:"response_format,omitempty"`
	Tools          []tool        `json:"tools,omitempty"`
	ToolChoice     string        `json:"tool_choice,omitempty"`
}

type respFormat struct {
	Type string `json:"type"`
}

type tool struct {
	Type     string       `json:"type"`
	Function toolFunction `json:"function"`
}

type toolFunction struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}

// chatResponse mirrors the /chat/completions response envelope.
type chatResponse struct {
	Choices []struct {
		Message struct {
			Content   string     `json:"content"`
			ToolCalls []toolCall `json:"tool_calls"`
		} `json:"message"`
		FinishReason string `json:"finish_reason"`
	} `json:"choices"`
}

type toolCall struct {
	Function struct {
		Name      string `json:"name"`
		Arguments string `json:"arguments"`
	} `json:"function"`
}

// Complete sends the prompt to the backend using the degradation chain and
// returns a validated Result or an error if all strategies fail.
func (c *Client) Complete(ctx context.Context, prompt string) (contract.Result, error) {
	caps := capSet(c.backend.Capabilities)
	return completeWithDegradation(ctx, c, prompt, caps)
}

// post sends a chat completion request and returns the raw response.
func (c *Client) post(ctx context.Context, req chatRequest) (*chatResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshalling request: %w", err)
	}

	endpoint := c.backend.Endpoint + "/chat/completions"
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("creating HTTP request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if keyEnv := c.backend.APIKeyEnv; keyEnv != "" {
		if key := os.Getenv(keyEnv); key != "" {
			httpReq.Header.Set("Authorization", "Bearer "+key)
		}
	}

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("HTTP request to %s: %w", endpoint, err)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading response body: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("backend returned %d: %s", resp.StatusCode, truncate(string(data), 200))
	}

	var out chatResponse
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, fmt.Errorf("parsing response: %w", err)
	}
	if len(out.Choices) == 0 {
		return nil, fmt.Errorf("backend returned no choices")
	}
	return &out, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// capSet converts a slice of capability strings to a set for O(1) lookup.
func capSet(caps []string) map[string]bool {
	m := make(map[string]bool, len(caps))
	for _, c := range caps {
		m[c] = true
	}
	return m
}

// resultSchema is the JSON Schema for the structured-output tool parameter.
var resultSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"command":      map[string]any{"type": "string"},
		"explanation":  map[string]any{"type": "string"},
		"danger":       map[string]any{"type": "string", "enum": []string{"safe", "caution", "destructive"}},
		"confidence":   map[string]any{"type": "number"},
		"needs":        map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
		"alternatives": map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
	},
	"required": []string{"command", "explanation", "danger", "confidence", "needs"},
}
