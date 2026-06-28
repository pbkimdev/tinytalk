package openai

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/paulbkim-dev/clite/internal/contract"
)

// ErrAllStrategiesFailed is returned when every degradation level fails to
// produce a valid Result. Never call code should surface this to the user.
var ErrAllStrategiesFailed = errors.New("all structured-output strategies failed")

// completeWithDegradation attempts three strategies in order:
//  1. Tool-calling (native structured output, most reliable)
//  2. JSON mode (response_format: json_object)
//  3. Fenced-block extraction + strict unmarshal (universal fallback)
//
// The capabilities map controls which strategies are attempted; fenced-block
// is always attempted last regardless.
func completeWithDegradation(ctx context.Context, c *Client, prompt string, caps map[string]bool) (contract.Result, error) {
	msgs := []chatMessage{
		{Role: "system", Content: systemPrompt},
		{Role: "user", Content: prompt},
	}

	// Strategy 1: tool-calling
	if caps["tool_calling"] {
		result, err := tryToolCalling(ctx, c, msgs)
		if err == nil {
			return result, nil
		}
	}

	// Strategy 2: JSON mode
	if caps["json_mode"] {
		result, err := tryJSONMode(ctx, c, msgs)
		if err == nil {
			return result, nil
		}
	}

	// Strategy 3: fenced-block extraction (always available)
	result, err := tryFencedBlock(ctx, c, msgs)
	if err == nil {
		return result, nil
	}

	return contract.Result{}, ErrAllStrategiesFailed
}

func tryToolCalling(ctx context.Context, c *Client, msgs []chatMessage) (contract.Result, error) {
	req := chatRequest{
		Model:       c.backend.Model,
		Messages:    msgs,
		Temperature: 0,
		Tools: []tool{{
			Type: "function",
			Function: toolFunction{
				Name:        "emit_command",
				Description: "Emit the structured shell command result",
				Parameters:  resultSchema,
			},
		}},
		ToolChoice: "required",
	}

	resp, err := c.post(ctx, req)
	if err != nil {
		return contract.Result{}, fmt.Errorf("tool-calling request: %w", err)
	}

	msg := resp.Choices[0].Message
	if len(msg.ToolCalls) == 0 {
		return contract.Result{}, fmt.Errorf("no tool calls in response")
	}

	return parseResult([]byte(msg.ToolCalls[0].Function.Arguments))
}

func tryJSONMode(ctx context.Context, c *Client, msgs []chatMessage) (contract.Result, error) {
	req := chatRequest{
		Model:          c.backend.Model,
		Messages:       msgs,
		Temperature:    0,
		ResponseFormat: &respFormat{Type: "json_object"},
	}

	resp, err := c.post(ctx, req)
	if err != nil {
		return contract.Result{}, fmt.Errorf("json-mode request: %w", err)
	}

	return parseResult([]byte(resp.Choices[0].Message.Content))
}

var fencedBlockRe = regexp.MustCompile("(?s)```(?:json)?\\s*([\\s\\S]*?)```")

func tryFencedBlock(ctx context.Context, c *Client, msgs []chatMessage) (contract.Result, error) {
	req := chatRequest{
		Model:       c.backend.Model,
		Messages:    msgs,
		Temperature: 0,
	}

	resp, err := c.post(ctx, req)
	if err != nil {
		return contract.Result{}, fmt.Errorf("fenced-block request: %w", err)
	}

	content := resp.Choices[0].Message.Content

	// Try the full content as JSON first.
	if r, err := parseResult([]byte(strings.TrimSpace(content))); err == nil {
		return r, nil
	}

	// Extract from first fenced block.
	m := fencedBlockRe.FindStringSubmatch(content)
	if m == nil {
		return contract.Result{}, fmt.Errorf("no JSON or fenced block found in response")
	}

	return parseResult([]byte(strings.TrimSpace(m[1])))
}

// parseResult strictly unmarshals raw JSON into a Result and validates required fields.
// It returns an error rather than a zero Result on any failure.
func parseResult(data []byte) (contract.Result, error) {
	var r contract.Result
	if err := json.Unmarshal(data, &r); err != nil {
		return contract.Result{}, fmt.Errorf("unmarshal result: %w", err)
	}
	if r.Command == "" {
		return contract.Result{}, fmt.Errorf("result missing required field: command")
	}
	if r.Explanation == "" {
		return contract.Result{}, fmt.Errorf("result missing required field: explanation")
	}
	switch r.Danger {
	case contract.DangerSafe, contract.DangerCaution, contract.DangerDestructive:
	default:
		return contract.Result{}, fmt.Errorf("result has invalid danger value: %q", r.Danger)
	}
	if r.Confidence < 0 || r.Confidence > 1 {
		return contract.Result{}, fmt.Errorf("result confidence %v out of range [0,1]", r.Confidence)
	}
	return r, nil
}

const systemPrompt = `You are a shell command generator. Given a natural-language request, emit exactly one shell command in the structured format below.

Respond ONLY with valid JSON matching this schema (no prose, no markdown):
{
  "command": "<the shell command>",
  "explanation": "<one-line description of what it does>",
  "danger": "safe" | "caution" | "destructive",
  "confidence": <0.0–1.0>,
  "needs": ["<binary1>", ...],
  "alternatives": []
}

danger levels:
- safe: read-only commands (ls, du, cat, grep, find without -delete)
- caution: mutates state (mv, cp overwrite, install, chmod)
- destructive: rm -rf, dd, mkfs, truncate, sudo writes, force push`
