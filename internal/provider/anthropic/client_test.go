package anthropic_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/paulbkim-dev/clite/internal/provider"
	"github.com/paulbkim-dev/clite/internal/provider/anthropic"
)

// wireReq is the subset of the Messages request body the tests assert on.
type wireReq struct {
	Model     string            `json:"model"`
	MaxTokens int               `json:"max_tokens"`
	System    string            `json:"system"`
	Messages  []json.RawMessage `json:"messages"`
	Tools     []struct {
		Name        string `json:"name"`
		Description string `json:"description"`
		InputSchema any    `json:"input_schema"`
	} `json:"tools"`
	Temperature *float64 `json:"temperature"`
	Thinking    *struct {
		Type         string `json:"type"`
		BudgetTokens int    `json:"budget_tokens"`
	} `json:"thinking"`
}

func happyResponse(model string) []byte {
	resp := map[string]any{
		"id":    "msg_1",
		"model": model,
		"role":  "assistant",
		"type":  "message",
		"content": []map[string]any{
			{"type": "text", "text": "hello"},
			{
				"type":  "tool_use",
				"id":    "tu1",
				"name":  "my_tool",
				"input": map[string]any{"x": 1},
			},
		},
		"usage": map[string]any{
			"input_tokens":  10,
			"output_tokens": 5,
		},
	}
	b, _ := json.Marshal(resp)
	return b
}

// 1. Happy path: headers, POST /messages, model, max_tokens, content flattening.
func TestComplete_HappyPath(t *testing.T) {
	var got wireReq
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/messages" {
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
		if k := r.Header.Get("x-api-key"); k != "test-key" {
			t.Errorf("x-api-key: got %q want test-key", k)
		}
		if v := r.Header.Get("anthropic-version"); v != "2023-06-01" {
			t.Errorf("anthropic-version: got %q want 2023-06-01", v)
		}
		json.NewDecoder(r.Body).Decode(&got)
		w.Header().Set("Content-Type", "application/json")
		w.Write(happyResponse("claude-x"))
	}))
	defer srv.Close()

	c := anthropic.New(srv.URL, "test-key", "claude-x")
	resp, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Model != "claude-x" {
		t.Errorf("model: got %q want claude-x", got.Model)
	}
	if got.MaxTokens <= 0 {
		t.Errorf("max_tokens: got %d want > 0", got.MaxTokens)
	}
	if len(got.Messages) != 1 {
		t.Errorf("messages: got %d want 1", len(got.Messages))
	}
	if resp.Content != "hello" {
		t.Errorf("content: got %q want hello", resp.Content)
	}
	if len(resp.ToolCalls) != 1 || resp.ToolCalls[0].Name != "my_tool" {
		t.Fatalf("tool_calls: got %+v", resp.ToolCalls)
	}
	if resp.ToolCalls[0].ID != "tu1" {
		t.Errorf("tool id: got %q want tu1", resp.ToolCalls[0].ID)
	}
	var args map[string]any
	if err := json.Unmarshal([]byte(resp.ToolCalls[0].Arguments), &args); err != nil {
		t.Fatalf("arguments not JSON: %q (%v)", resp.ToolCalls[0].Arguments, err)
	}
	if args["x"] != float64(1) {
		t.Errorf("arguments: got %v", args)
	}
	if resp.Usage.PromptTokens != 10 || resp.Usage.CompletionTokens != 5 || resp.Usage.TotalTokens != 15 {
		t.Errorf("usage: got %+v want {10 5 15}", resp.Usage)
	}
}

// 2. System message is hoisted to top-level `system`, absent from messages.
func TestComplete_SystemExtraction(t *testing.T) {
	var got wireReq
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&got)
		w.Write(happyResponse("claude-x"))
	}))
	defer srv.Close()

	c := anthropic.New(srv.URL, "k", "claude-x")
	_, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{
			{Role: provider.RoleSystem, Content: "be brief"},
			{Role: provider.RoleUser, Content: "hi"},
		},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.System != "be brief" {
		t.Errorf("system: got %q want %q", got.System, "be brief")
	}
	if len(got.Messages) != 1 {
		t.Errorf("messages: got %d want 1 (system hoisted out)", len(got.Messages))
	}
}

// 3. Tools serialize under input_schema.
func TestComplete_ToolInputSchema(t *testing.T) {
	var got wireReq
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&got)
		w.Write(happyResponse("claude-x"))
	}))
	defer srv.Close()

	c := anthropic.New(srv.URL, "k", "claude-x")
	_, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
		Tools: []provider.Tool{{
			Name:        "lookup",
			Description: "look it up",
			Parameters:  map[string]any{"type": "object"},
		}},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(got.Tools) != 1 {
		t.Fatalf("tools: got %d want 1", len(got.Tools))
	}
	if got.Tools[0].Name != "lookup" {
		t.Errorf("tool name: got %q", got.Tools[0].Name)
	}
	if got.Tools[0].InputSchema == nil {
		t.Errorf("input_schema missing")
	}
}

// 4. ReasoningEffort → thinking block; temperature omitted; max_tokens > budget;
// thinking response blocks skipped.
func TestComplete_ExtendedThinking(t *testing.T) {
	var got wireReq
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&got)
		resp := map[string]any{
			"model": "claude-x",
			"content": []map[string]any{
				{"type": "thinking", "thinking": "hmm", "signature": "sig"},
				{"type": "text", "text": "answer"},
			},
			"usage": map[string]any{"input_tokens": 1, "output_tokens": 2},
		}
		b, _ := json.Marshal(resp)
		w.Write(b)
	}))
	defer srv.Close()

	temp := 0.7
	c := anthropic.New(srv.URL, "k", "claude-x")
	resp, err := c.Complete(context.Background(), provider.Request{
		Messages:        []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
		ReasoningEffort: "high",
		Temperature:     &temp,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Thinking == nil {
		t.Fatal("thinking block missing from request")
	}
	if got.Thinking.Type != "enabled" || got.Thinking.BudgetTokens <= 0 {
		t.Errorf("thinking: got %+v", got.Thinking)
	}
	if got.MaxTokens <= got.Thinking.BudgetTokens {
		t.Errorf("max_tokens (%d) must exceed budget (%d)", got.MaxTokens, got.Thinking.BudgetTokens)
	}
	if got.Temperature != nil {
		t.Errorf("temperature must be omitted when thinking enabled, got %v", *got.Temperature)
	}
	if resp.Content != "answer" {
		t.Errorf("content: got %q want answer (thinking skipped)", resp.Content)
	}
}

// 5. Non-2xx → *APIError, nil response.
func TestComplete_HTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		w.Write([]byte(`{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}`))
	}))
	defer srv.Close()

	c := anthropic.New(srv.URL, "k", "claude-x")
	resp, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if resp != nil {
		t.Fatalf("expected nil response, got %+v", resp)
	}
	var apiErr *anthropic.APIError
	if !anthropic.AsAPIError(err, &apiErr) {
		t.Fatalf("expected *APIError, got %T: %v", err, err)
	}
	if apiErr.StatusCode != 429 {
		t.Errorf("status: got %d want 429", apiErr.StatusCode)
	}
	if apiErr.Message != "slow down" {
		t.Errorf("message: got %q", apiErr.Message)
	}
}

// 6. Malformed JSON → error, nil response.
func TestComplete_MalformedJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{not valid`))
	}))
	defer srv.Close()

	c := anthropic.New(srv.URL, "k", "claude-x")
	resp, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if err == nil {
		t.Fatal("expected error for malformed JSON")
	}
	if resp != nil {
		t.Fatalf("expected nil response, got %+v", resp)
	}
}

// 7. Cancelled context → error, nil response.
func TestComplete_ContextCancelled(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	c := anthropic.New(srv.URL, "k", "claude-x")
	resp, err := c.Complete(ctx, provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
	if resp != nil {
		t.Fatalf("expected nil response, got %+v", resp)
	}
}

// 8. max_tokens default applied when unset.
func TestComplete_MaxTokensDefault(t *testing.T) {
	var got wireReq
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&got)
		w.Write(happyResponse("claude-x"))
	}))
	defer srv.Close()

	c := anthropic.New(srv.URL, "k", "claude-x")
	_, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.MaxTokens <= 0 {
		t.Errorf("max_tokens default not applied: got %d", got.MaxTokens)
	}
}

// 9. No key → no x-api-key header.
func TestComplete_NoKey(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if k := r.Header.Get("x-api-key"); k != "" {
			t.Errorf("unexpected x-api-key: %q", k)
		}
		w.Write(happyResponse("claude-x"))
	}))
	defer srv.Close()

	c := anthropic.New(srv.URL, "", "claude-x")
	resp, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.Content != "hello" {
		t.Errorf("content: got %q want hello", resp.Content)
	}
}

// Pinned version is overridable.
func TestWithVersion(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if v := r.Header.Get("anthropic-version"); v != "2099-01-01" {
			t.Errorf("anthropic-version: got %q want 2099-01-01", v)
		}
		w.Write(happyResponse("claude-x"))
	}))
	defer srv.Close()

	c := anthropic.New(srv.URL, "k", "claude-x", anthropic.WithVersion("2099-01-01"))
	if _, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	}); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
