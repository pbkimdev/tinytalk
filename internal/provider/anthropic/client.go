// Package anthropic implements provider.Provider against Anthropic's native
// Messages API (POST /v1/messages). It mirrors the shape and ergonomics of the
// OpenAI client in the sibling package.
package anthropic

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"

	"github.com/paulbkim-dev/clite/internal/provider"
)

// defaultVersion pins the anthropic-version header. Bump deliberately; keep it
// a named constant so the API contract is explicit and greppable.
const defaultVersion = "2023-06-01"

// defaultMaxTokens is sent when the request leaves MaxTokens unset — the
// Messages API requires max_tokens, so there is no "omit" option.
const defaultMaxTokens = 4096

// wire types for /messages

type wireMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type wireTool struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	InputSchema any    `json:"input_schema,omitempty"`
}

type wireThinking struct {
	Type         string `json:"type"`
	BudgetTokens int    `json:"budget_tokens"`
}

type wireRequest struct {
	Model       string        `json:"model"`
	MaxTokens   int           `json:"max_tokens"`
	Messages    []wireMessage `json:"messages"`
	System      string        `json:"system,omitempty"`
	Tools       []wireTool    `json:"tools,omitempty"`
	Temperature *float64      `json:"temperature,omitempty"`
	Thinking    *wireThinking `json:"thinking,omitempty"`
}

type wireBlock struct {
	Type  string          `json:"type"`
	Text  string          `json:"text"`
	ID    string          `json:"id"`
	Name  string          `json:"name"`
	Input json.RawMessage `json:"input"`
}

type wireUsage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

type wireResponse struct {
	Model   string      `json:"model"`
	Content []wireBlock `json:"content"`
	Usage   wireUsage   `json:"usage"`
}

type wireError struct {
	Error struct {
		Type    string `json:"type"`
		Message string `json:"message"`
	} `json:"error"`
}

// APIError is returned for non-2xx HTTP responses.
type APIError struct {
	StatusCode int
	Message    string
	ErrorType  string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("anthropic: HTTP %d: %s", e.StatusCode, e.Message)
}

// AsAPIError unwraps err into target if it is (or wraps) an *APIError.
func AsAPIError(err error, target **APIError) bool {
	return errors.As(err, target)
}

// Client implements provider.Provider against the Anthropic Messages API.
type Client struct {
	httpClient      *http.Client
	baseURL         string
	apiKey          string
	model           string
	version         string
	reasoningEffort string
	name            string
}

type Option func(*Client)

func WithHTTPClient(hc *http.Client) Option {
	return func(c *Client) { c.httpClient = hc }
}

func WithReasoningEffort(effort string) Option {
	return func(c *Client) { c.reasoningEffort = effort }
}

func WithName(name string) Option {
	return func(c *Client) { c.name = name }
}

// WithVersion overrides the pinned anthropic-version header.
func WithVersion(version string) Option {
	return func(c *Client) { c.version = version }
}

// New creates an Anthropic Messages client.
func New(baseURL, apiKey, model string, opts ...Option) *Client {
	c := &Client{
		httpClient: http.DefaultClient,
		baseURL:    baseURL,
		apiKey:     apiKey,
		model:      model,
		version:    defaultVersion,
		name:       "anthropic",
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

func (c *Client) Name() string { return c.name }

func (c *Client) Complete(ctx context.Context, req provider.Request) (*provider.Response, error) {
	system, msgs := extractSystem(req.Messages)

	maxTokens := req.MaxTokens
	if maxTokens <= 0 {
		maxTokens = defaultMaxTokens
	}

	body := wireRequest{
		Model:     c.model,
		MaxTokens: maxTokens,
		Messages:  msgs,
		System:    system,
	}
	if len(req.Tools) > 0 {
		body.Tools = toWireTools(req.Tools)
	}

	effort := req.ReasoningEffort
	if effort == "" {
		effort = c.reasoningEffort
	}
	if effort != "" {
		budget := thinkingBudget(effort)
		body.Thinking = &wireThinking{Type: "enabled", BudgetTokens: budget}
		// max_tokens must exceed the thinking budget; reserve the output
		// budget on top of it. Temperature is unsupported with thinking, so
		// it is deliberately left unset.
		body.MaxTokens = maxTokens + budget
	} else {
		body.Temperature = req.Temperature
	}
	// ResponseFormatJSONObject is a documented no-op here (structured-output
	// degradation lives in #11).

	b, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("anthropic: marshal request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/messages", bytes.NewReader(b))
	if err != nil {
		return nil, fmt.Errorf("anthropic: build request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("anthropic-version", c.version)
	if c.apiKey != "" {
		httpReq.Header.Set("x-api-key", c.apiKey)
	}

	httpResp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("anthropic: do request: %w", err)
	}
	defer httpResp.Body.Close()

	raw, err := io.ReadAll(httpResp.Body)
	if err != nil {
		return nil, fmt.Errorf("anthropic: read body: %w", err)
	}

	if httpResp.StatusCode < 200 || httpResp.StatusCode >= 300 {
		var we wireError
		json.Unmarshal(raw, &we) // best-effort; ignore error
		return nil, &APIError{
			StatusCode: httpResp.StatusCode,
			Message:    we.Error.Message,
			ErrorType:  we.Error.Type,
		}
	}

	var wr wireResponse
	if err := json.Unmarshal(raw, &wr); err != nil {
		return nil, fmt.Errorf("anthropic: decode response: %w", err)
	}

	resp := &provider.Response{
		Model: wr.Model,
		Raw:   raw,
		Usage: provider.Usage{
			PromptTokens:     wr.Usage.InputTokens,
			CompletionTokens: wr.Usage.OutputTokens,
			TotalTokens:      wr.Usage.InputTokens + wr.Usage.OutputTokens,
		},
	}
	for _, blk := range wr.Content {
		switch blk.Type {
		case "text":
			resp.Content += blk.Text
		case "tool_use":
			resp.ToolCalls = append(resp.ToolCalls, provider.ToolCall{
				ID:        blk.ID,
				Name:      blk.Name,
				Arguments: string(blk.Input),
			})
		}
		// thinking and other block types are skipped.
	}
	return resp, nil
}

// extractSystem hoists RoleSystem messages into the top-level system string
// (joined) and returns the remaining messages as wire messages.
func extractSystem(msgs []provider.Message) (string, []wireMessage) {
	var system string
	out := make([]wireMessage, 0, len(msgs))
	for _, m := range msgs {
		if m.Role == provider.RoleSystem {
			if system != "" {
				system += "\n\n"
			}
			system += m.Content
			continue
		}
		out = append(out, wireMessage{Role: string(m.Role), Content: m.Content})
	}
	return system, out
}

func toWireTools(tools []provider.Tool) []wireTool {
	out := make([]wireTool, len(tools))
	for i, t := range tools {
		out[i] = wireTool{
			Name:        t.Name,
			Description: t.Description,
			InputSchema: t.Parameters,
		}
	}
	return out
}

// thinkingBudget maps a reasoning-effort label to an extended-thinking token
// budget. The Messages API requires a minimum of 1024.
func thinkingBudget(effort string) int {
	switch effort {
	case "low":
		return 1024
	case "high":
		return 8192
	default: // "medium" and any other non-empty value
		return 4096
	}
}

// compile-time assertion
var _ provider.Provider = (*Client)(nil)
