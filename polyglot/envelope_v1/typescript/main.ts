// B1 fourteen-language conformance: TypeScript.
//
// Invariant owned: schema_version_is_exact
//
// A reader that accepts an unknown schema_version is processing a record whose
// meaning it has not established. B1's envelope readers refuse rather than
// guess, and this consumer checks the committed vector carries the exact
// version those readers are written against. Exact equality, not a prefix or a
// range: "b1-event-envelope-10" starts with "b1-event-envelope-1".
//
// TypeScript owns it because TypeScript is B1's contract language for the
// desktop surface, where an envelope arrives from a backend that may be a
// different build than the UI.
//
// digest_verification: STDLIB. The Node runtime provides node:crypto.
//
// The ambient declarations below are deliberate: this consumer must compile
// with a bare `tsc` and no @types/node, because the harness requires each
// language to build from a single source file with nothing installed beyond its
// own toolchain.
//
// Usage: node main.js <contract.json> <vector.canonical> <expected.json>

// `console` is deliberately not declared here: tsc's default lib already
// supplies it, and redeclaring it is an error rather than a convenience.
declare function require(name: string): any;
declare const process: { argv: string[]; exit(code: number): never };

const { createHash } = require("node:crypto");
const { readFileSync } = require("node:fs");

const INVARIANT = "schema_version_is_exact";
const EXPECTED_SCHEMA = "b1-event-envelope-1";

interface Contract {
  invariants?: Record<string, boolean>;
}

interface Expected {
  canonical_sha256?: string;
}

function main(argv: string[]): number {
  if (argv.length !== 3) {
    console.error("usage: node main.js <contract.json> <vector.canonical> <expected.json>");
    return 2;
  }
  const contractPath = argv[0];
  const vectorPath = argv[1];
  const expectedPath = argv[2];

  const contract: Contract = JSON.parse(readFileSync(contractPath, "utf8"));
  const vectorBytes = readFileSync(vectorPath);
  const expected: Expected = JSON.parse(readFileSync(expectedPath, "utf8"));

  // The contract must actually assert this invariant. A consumer checking a
  // property nobody declared is testing its own opinion, not B1's contract.
  if (contract.invariants?.[INVARIANT] !== true) {
    console.error(`TypeScript: contract does not assert ${INVARIANT}`);
    return 4;
  }

  const envelope: Record<string, unknown> = JSON.parse(vectorBytes.toString("utf8"));
  const version = envelope["schema_version"];

  if (typeof version !== "string") {
    console.error("TypeScript: schema_version is absent or not a string");
    return 5;
  }
  if (version !== EXPECTED_SCHEMA) {
    console.error(
      `TypeScript: schema_version is ${JSON.stringify(version)}, expected ` +
        `${JSON.stringify(EXPECTED_SCHEMA)}. A reader that accepts an unknown version is ` +
        `processing a record whose meaning it has not established.`,
    );
    return 5;
  }

  const actual = createHash("sha256").update(vectorBytes).digest("hex");
  if (actual !== expected.canonical_sha256) {
    console.error(
      `TypeScript: digest mismatch; declared ${expected.canonical_sha256} ` +
        `but the bytes hash to ${actual}`,
    );
    return 5;
  }

  console.log(`TYPESCRIPT:POSTCONDITION:${INVARIANT}`);
  return 0;
}

process.exit(main(process.argv.slice(2)));
