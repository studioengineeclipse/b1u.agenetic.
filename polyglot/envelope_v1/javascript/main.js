#!/usr/bin/env node
// B1 fourteen-language conformance: JavaScript.
//
// Invariant owned: canonical_contains_no_null_literal
//
// After canonicalisation, an absent key and an explicit null are
// indistinguishable in several of these fourteen languages. Permitting null
// would therefore give one logical envelope two canonical forms, and two forms
// means two digests. B1 omits the key instead, and this consumer checks that no
// bare null survives into the committed bytes.
//
// JavaScript owns it because JS is the language most likely to reintroduce one:
// JSON.stringify turns undefined into null inside arrays, silently.
//
// digest_verification: STDLIB. node:crypto provides SHA-256.
//
// Usage: node main.js <contract.json> <vector.canonical> <expected.json>

"use strict";

const fs = require("node:fs");
const crypto = require("node:crypto");

const INVARIANT = "canonical_contains_no_null_literal";

/** Return the text with every string literal removed. */
function outsideStrings(text) {
  let out = "";
  let inString = false;
  let escaped = false;
  for (const char of text) {
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') {
      inString = true;
      continue;
    }
    out += char;
  }
  return out;
}

function main(argv) {
  if (argv.length !== 3) {
    console.error("usage: node main.js <contract.json> <vector.canonical> <expected.json>");
    return 2;
  }
  const [contractPath, vectorPath, expectedPath] = argv;

  const contract = JSON.parse(fs.readFileSync(contractPath, "utf8"));
  const vectorBytes = fs.readFileSync(vectorPath);
  const expected = JSON.parse(fs.readFileSync(expectedPath, "utf8"));

  // The contract must actually assert this invariant.
  if (contract.invariants?.[INVARIANT] !== true) {
    console.error(`JavaScript: contract does not assert ${INVARIANT}`);
    return 4;
  }

  const structure = outsideStrings(vectorBytes.toString("utf8"));
  const at = structure.indexOf("null");
  if (at !== -1) {
    console.error(
      "JavaScript: found a bare null literal outside a string. An absent key and an " +
        "explicit null are indistinguishable after canonicalisation in several target " +
        "languages, so B1 omits the key instead.",
    );
    return 5;
  }

  const actual = crypto.createHash("sha256").update(vectorBytes).digest("hex");
  if (actual !== expected.canonical_sha256) {
    console.error(
      `JavaScript: digest mismatch; declared ${expected.canonical_sha256} ` +
        `but the bytes hash to ${actual}`,
    );
    return 5;
  }

  console.log(`JavaScript:POSTCONDITION:${INVARIANT}`);
  return 0;
}

process.exit(main(process.argv.slice(2)));
