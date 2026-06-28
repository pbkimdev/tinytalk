package contract

// Danger classifies the risk level of a generated command.
type Danger string

const (
	DangerSafe        Danger = "safe"
	DangerCaution     Danger = "caution"
	DangerDestructive Danger = "destructive"
)

// Result is the structured output contract all providers must produce.
type Result struct {
	Command      string   `json:"command"`
	Explanation  string   `json:"explanation"`
	Danger       Danger   `json:"danger"`
	Confidence   float64  `json:"confidence"`
	Needs        []string `json:"needs"`
	Alternatives []string `json:"alternatives,omitempty"`
}
