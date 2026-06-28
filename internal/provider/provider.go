package provider

import "context"

type Role string

const (
	RoleSystem    Role = "system"
	RoleUser      Role = "user"
	RoleAssistant Role = "assistant"
	RoleTool      Role = "tool"
)

type Message struct {
	Role    Role   `json:"role"`
	Content string `json:"content"`
}

type Tool struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Parameters  any    `json:"parameters,omitempty"`
}

type ToolCall struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type ResponseFormat string

const (
	ResponseFormatText       ResponseFormat = ""
	ResponseFormatJSONObject ResponseFormat = "json_object"
)

type Request struct {
	Messages        []Message
	Tools           []Tool
	ResponseFormat  ResponseFormat
	ReasoningEffort string
	Temperature     *float64
	MaxTokens       int
}

type Usage struct {
	PromptTokens     int
	CompletionTokens int
	TotalTokens      int
}

type Response struct {
	Content   string
	ToolCalls []ToolCall
	Usage     Usage
	Model     string
	Raw       []byte
}

type Provider interface {
	Name() string
	Complete(ctx context.Context, req Request) (*Response, error)
}
