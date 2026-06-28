package openai_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/paulbkim-dev/clite/internal/provider"
	"github.com/paulbkim-dev/clite/internal/provider/openai"
)

func happyResponse(model string) []byte {
	resp := map[string]any{
		"id":    "chatcmpl-1",
		"model": model,
		"choices": []map[string]any{
			{
				"message": map[string]any{
					"role":    "assistant",
					"content": "hello",
					"tool_calls": []map[string]any{
						{
							"id": "tc1",
							"function": map[string]any{
								"name":      "my_tool",
								"arguments": `{"x":1}`,
							},
						},
					},
				},
				"finish_reason": "tool_calls",
			},
		},
		"usage": map[string]any{
			"prompt_tokens":     10,
			"completion_tokens": 5,
			"total_tokens":      15,
		},
	}
	b, _ := json.Marshal(resp)
	return b
}

// 1. Happy path
func TestComplete_HappyPath(t *testing.T) {
	var gotReq struct {
		Model           string             `json:"model"`
		Messages        []provider.Message `json:"messages"`
		ReasoningEffort string             `json:"reasoning_effort"`
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/chat/completions" {
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
		if auth := r.Header.Get("Authorization"); !strings.HasPrefix(auth, "Bearer ") {
			t.Errorf("missing Bearer auth, got %q", auth)
		}
		json.NewDecoder(r.Body).Decode(&gotReq)
		w.Header().Set("Content-Type", "application/json")
		w.Write(happyResponse("gpt-4o"))
	}))
	defer srv.Close()

	c := openai.New(srv.URL, "test-key", "gpt-4o", openai.WithReasoningEffort("high"))
	resp, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if gotReq.Model != "gpt-4o" {
		t.Errorf("model: got %q want %q", gotReq.Model, "gpt-4o")
	}
	if len(gotReq.Messages) != 1 {
		t.Errorf("messages: got %d want 1", len(gotReq.Messages))
	}
	if gotReq.ReasoningEffort != "high" {
		t.Errorf("reasoning_effort: got %q want %q", gotReq.ReasoningEffort, "high")
	}
	if resp.Content != "hello" {
		t.Errorf("content: got %q want %q", resp.Content, "hello")
	}
	if resp.Usage.PromptTokens != 10 || resp.Usage.TotalTokens != 15 {
		t.Errorf("usage: got %+v", resp.Usage)
	}
	if len(resp.ToolCalls) != 1 || resp.ToolCalls[0].Name != "my_tool" {
		t.Errorf("tool_calls: got %+v", resp.ToolCalls)
	}
}

// 2. HTTP error → *APIError, no response
func TestComplete_HTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"message":"server exploded","type":"server_error"}}`))
	}))
	defer srv.Close()

	c := openai.New(srv.URL, "key", "gpt-4o")
	resp, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if resp != nil {
		t.Fatalf("expected nil response, got %+v", resp)
	}
	var apiErr *openai.APIError
	if !openai.AsAPIError(err, &apiErr) {
		t.Fatalf("expected *APIError, got %T: %v", err, err)
	}
	if apiErr.StatusCode != 500 {
		t.Errorf("status: got %d want 500", apiErr.StatusCode)
	}
}

// 3. Malformed JSON → decode error, not partial Response
func TestComplete_MalformedJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{not valid json`))
	}))
	defer srv.Close()

	c := openai.New(srv.URL, "key", "gpt-4o")
	resp, err := c.Complete(context.Background(), provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if err == nil {
		t.Fatal("expected error for malformed JSON")
	}
	if resp != nil {
		t.Fatalf("expected nil response on decode error, got %+v", resp)
	}
}

// 4. Context cancellation → returns ctx error promptly
func TestComplete_ContextCancelled(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// block until client disconnects
		<-r.Context().Done()
	}))
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	c := openai.New(srv.URL, "key", "gpt-4o")
	resp, err := c.Complete(ctx, provider.Request{
		Messages: []provider.Message{{Role: provider.RoleUser, Content: "hi"}},
	})
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
	if resp != nil {
		t.Fatalf("expected nil response on ctx error, got %+v", resp)
	}
}

// 5. No-key local endpoint → no Authorization header
func TestComplete_NoKey(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" {
			t.Errorf("unexpected Authorization header: %q", r.Header.Get("Authorization"))
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(happyResponse("llama3"))
	}))
	defer srv.Close()

	c := openai.New(srv.URL, "", "llama3") // empty key
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
