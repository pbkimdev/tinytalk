package openai

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

// wire types for /chat/completions

type wireMessage struct {
	Role      string          `json:"role"`
	Content   string          `json:"content,omitempty"`
	ToolCalls []wireToolCall  `json:"tool_calls,omitempty"`
}

type wireToolCall struct {
	ID       string           `json:"id"`
	Type     string           `json:"type,omitempty"`
	Function wireFunctionCall `json:"function"`
}

type wireFunctionCall struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type wireTool struct {
	Type     string       `json:"type"`
	Function wireFunction `json:"function"`
}

type wireFunction struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Parameters  any    `json:"parameters,omitempty"`
}

type wireRequest struct {
	Model           string        `json:"model"`
	Messages        []wireMessage `json:"messages"`
	Tools           []wireTool    `json:"tools,omitempty"`
	ResponseFormat  *wireRespFmt  `json:"response_format,omitempty"`
	ReasoningEffort string        `json:"reasoning_effort,omitempty"`
	Temperature     *float64      `json:"temperature,omitempty"`
	MaxTokens       int           `json:"max_tokens,omitempty"`
}

type wireRespFmt struct {
	Type string `json:"type"`
}

type wireResponse struct {
	Model   string       `json:"model"`
	Choices []wireChoice `json:"choices"`
	Usage   wireUsage    `json:"usage"`
}

type wireChoice struct {
	Message      wireMessage `json:"message"`
	FinishReason string      `json:"finish_reason"`
}

type wireUsage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

type wireError struct {
	Error struct {
		Message string `json:"message"`
		Type    string `json:"type"`
	} `json:"error"`
}

// APIError is returned for non-2xx HTTP responses.
type APIError struct {
	StatusCode int
	Message    string
	ErrorType  string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("openai: HTTP %d: %s", e.StatusCode, e.Message)
}

// AsAPIError unwraps err into target if it is (or wraps) an *APIError.
func AsAPIError(err error, target **APIError) bool {
	return errors.As(err, target)
}

// Client implements provider.Provider against any OpenAI-compatible endpoint.
type Client struct {
	httpClient      *http.Client
	baseURL         string
	apiKey          string
	model           string
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

// New creates an OpenAI-compatible client.
func New(baseURL, apiKey, model string, opts ...Option) *Client {
	c := &Client{
		httpClient: http.DefaultClient,
		baseURL:    baseURL,
		apiKey:     apiKey,
		model:      model,
		name:       "openai",
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

func (c *Client) Name() string { return c.name }

func (c *Client) Complete(ctx context.Context, req provider.Request) (*provider.Response, error) {
	body := wireRequest{
		Model:       c.model,
		Messages:    toWireMessages(req.Messages),
		Temperature: req.Temperature,
	}
	if req.MaxTokens > 0 {
		body.MaxTokens = req.MaxTokens
	}
	if req.ReasoningEffort != "" {
		body.ReasoningEffort = req.ReasoningEffort
	} else if c.reasoningEffort != "" {
		body.ReasoningEffort = c.reasoningEffort
	}
	if len(req.Tools) > 0 {
		body.Tools = toWireTools(req.Tools)
	}
	if req.ResponseFormat == provider.ResponseFormatJSONObject {
		body.ResponseFormat = &wireRespFmt{Type: "json_object"}
	}

	b, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("openai: marshal request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/chat/completions", bytes.NewReader(b))
	if err != nil {
		return nil, fmt.Errorf("openai: build request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+c.apiKey)
	}

	httpResp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("openai: do request: %w", err)
	}
	defer httpResp.Body.Close()

	raw, err := io.ReadAll(httpResp.Body)
	if err != nil {
		return nil, fmt.Errorf("openai: read body: %w", err)
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
		return nil, fmt.Errorf("openai: decode response: %w", err)
	}
	if len(wr.Choices) == 0 {
		return nil, fmt.Errorf("openai: empty choices in response")
	}

	msg := wr.Choices[0].Message
	resp := &provider.Response{
		Content: msg.Content,
		Model:   wr.Model,
		Raw:     raw,
		Usage: provider.Usage{
			PromptTokens:     wr.Usage.PromptTokens,
			CompletionTokens: wr.Usage.CompletionTokens,
			TotalTokens:      wr.Usage.TotalTokens,
		},
	}
	for _, tc := range msg.ToolCalls {
		resp.ToolCalls = append(resp.ToolCalls, provider.ToolCall{
			ID:        tc.ID,
			Name:      tc.Function.Name,
			Arguments: tc.Function.Arguments,
		})
	}
	return resp, nil
}

func toWireMessages(msgs []provider.Message) []wireMessage {
	out := make([]wireMessage, len(msgs))
	for i, m := range msgs {
		out[i] = wireMessage{Role: string(m.Role), Content: m.Content}
	}
	return out
}

func toWireTools(tools []provider.Tool) []wireTool {
	out := make([]wireTool, len(tools))
	for i, t := range tools {
		out[i] = wireTool{
			Type: "function",
			Function: wireFunction{
				Name:        t.Name,
				Description: t.Description,
				Parameters:  t.Parameters,
			},
		}
	}
	return out
}

// compile-time assertion
var _ provider.Provider = (*Client)(nil)
