package openai

import (
	"testing"

	"github.com/paulbkim-dev/clite/internal/contract"
)

func TestParseResult_ValidJSON(t *testing.T) {
	raw := []byte(`{
		"command": "ls -la",
		"explanation": "List files in long format",
		"danger": "safe",
		"confidence": 0.95,
		"needs": ["ls"]
	}`)
	r, err := parseResult(raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if r.Command != "ls -la" {
		t.Errorf("command = %q, want %q", r.Command, "ls -la")
	}
	if r.Danger != contract.DangerSafe {
		t.Errorf("danger = %q, want %q", r.Danger, contract.DangerSafe)
	}
}

func TestParseResult_MalformedJSON(t *testing.T) {
	cases := []struct {
		name string
		raw  []byte
	}{
		{"not json", []byte("this is not json")},
		{"empty object", []byte("{}")},
		{"missing command", []byte(`{"explanation":"x","danger":"safe","confidence":0.5,"needs":[]}`)},
		{"missing explanation", []byte(`{"command":"ls","danger":"safe","confidence":0.5,"needs":[]}`)},
		{"bad danger", []byte(`{"command":"ls","explanation":"x","danger":"unknown","confidence":0.5,"needs":[]}`)},
		{"confidence out of range", []byte(`{"command":"ls","explanation":"x","danger":"safe","confidence":1.5,"needs":[]}`)},
		{"negative confidence", []byte(`{"command":"ls","explanation":"x","danger":"safe","confidence":-0.1,"needs":[]}`)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := parseResult(tc.raw)
			if err == nil {
				t.Error("expected error, got nil")
			}
			// Ensure the zero value is not silently passed through.
		})
	}
}

func TestParseResult_FencedBlockExtraction(t *testing.T) {
	// Simulate a model that wraps JSON in a fenced block.
	content := "Here is the command:\n```json\n{\"command\":\"df -h\",\"explanation\":\"disk usage\",\"danger\":\"safe\",\"confidence\":0.9,\"needs\":[\"df\"]}\n```"
	m := fencedBlockRe.FindStringSubmatch(content)
	if m == nil {
		t.Fatal("fenced block regex did not match")
	}
	r, err := parseResult([]byte(m[1]))
	if err != nil {
		t.Fatalf("parseResult from fenced block: %v", err)
	}
	if r.Command != "df -h" {
		t.Errorf("command = %q, want %q", r.Command, "df -h")
	}
}

func TestParseResult_NoFencedBlockReturnsError(t *testing.T) {
	// Plain prose with no JSON and no fenced block should fail fenced-block extraction.
	content := "I cannot help with that."
	m := fencedBlockRe.FindStringSubmatch(content)
	if m != nil {
		t.Fatal("expected no match, got one")
	}
}
