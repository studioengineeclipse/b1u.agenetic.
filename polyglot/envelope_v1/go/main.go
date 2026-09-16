// B1 fourteen-language conformance: Go.
//
// Invariant owned: origin_is_one_of_u_m_p_e
//
// Origin is a closed set: U (user-literal), M (model-derived), P
// (platform-generated), E (emergent). An unrecognised origin would let a record
// escape the classification entirely, and a record whose origin is unknown
// cannot have its authority reasoned about at all. The check is deliberately
// exact-match against the four, not a length or a character-class test, because
// a lowercase "u" is a different record.
//
// digest_verification: STDLIB. crypto/sha256 provides SHA-256.
//
// Usage: main <contract.json> <vector.canonical> <expected.json>
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
)

const invariant = "origin_is_one_of_u_m_p_e"

var allowed = map[string]bool{"U": true, "M": true, "P": true, "E": true}

type contractFile struct {
	Invariants map[string]bool `json:"invariants"`
}

type envelope struct {
	Origin string `json:"origin"`
}

type expectedFile struct {
	CanonicalSHA256 string `json:"canonical_sha256"`
}

func run(args []string) error {
	if len(args) != 3 {
		return fmt.Errorf("usage: main <contract.json> <vector.canonical> <expected.json>")
	}

	contractBytes, err := os.ReadFile(args[0])
	if err != nil {
		return fmt.Errorf("cannot read contract: %w", err)
	}
	vectorBytes, err := os.ReadFile(args[1])
	if err != nil {
		return fmt.Errorf("cannot read vector: %w", err)
	}
	expectedBytes, err := os.ReadFile(args[2])
	if err != nil {
		return fmt.Errorf("cannot read expected: %w", err)
	}

	var contract contractFile
	if err := json.Unmarshal(contractBytes, &contract); err != nil {
		return fmt.Errorf("contract is not valid JSON: %w", err)
	}
	// The contract must actually assert this invariant. A consumer checking a
	// property nobody declared is testing its own opinion, not B1's contract.
	if !contract.Invariants[invariant] {
		return fmt.Errorf("contract does not assert %s", invariant)
	}

	var record envelope
	if err := json.Unmarshal(vectorBytes, &record); err != nil {
		return fmt.Errorf("vector is not valid JSON: %w", err)
	}
	if record.Origin == "" {
		return fmt.Errorf("vector carries no origin; every envelope must be classified U, M, P or E")
	}
	if !allowed[record.Origin] {
		return fmt.Errorf(
			"origin %q is not one of U, M, P, E; a record whose origin is unrecognised "+
				"has escaped the classification entirely", record.Origin)
	}

	var expected expectedFile
	if err := json.Unmarshal(expectedBytes, &expected); err != nil {
		return fmt.Errorf("expected is not valid JSON: %w", err)
	}
	sum := sha256.Sum256(vectorBytes)
	actual := hex.EncodeToString(sum[:])
	if actual != expected.CanonicalSHA256 {
		return fmt.Errorf("digest mismatch; declared %s but the bytes hash to %s",
			expected.CanonicalSHA256, actual)
	}

	fmt.Printf("GO:POSTCONDITION:%s\n", invariant)
	return nil
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "Go: %v\n", err)
		os.Exit(5)
	}
}
