// B1 fourteen-language conformance: Swift.
//
// Invariant owned: payload_digest_is_lowercase_hex64
//
// Hex case is part of the bytes. An upper-case digest is a different string,
// therefore a different record, therefore a different chained digest. B1 emits
// lowercase everywhere and this consumer holds the committed vector to it,
// because case is exactly the kind of difference a human reviewer reads straight
// past.
//
// The check is exact: 64 characters, each 0-9 or a-f. Not "looks hex-ish", not
// a case-insensitive match. A permissive check here would let the one thing
// this invariant exists to catch go through.
//
// Swift owns it because Swift is B1's Apple surface, where digests cross a
// platform boundary and get re-encoded on the way.
//
// digest_verification: NOT_IN_STDLIB. CryptoKit is Apple-only and Foundation
// carries no SHA-256 on Linux, so a portable single-file consumer cannot verify
// the declared digest without a dependency. B1 does not carry a hand-written
// SHA-256 it cannot execute everywhere; the gap is declared rather than faked.
//
// Usage: main <contract.json> <vector.canonical>

import Foundation

let invariant = "payload_digest_is_lowercase_hex64"

func failWith(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write("Swift: \(message)\n".data(using: .utf8)!)
    exit(code)
}

let args = Array(CommandLine.arguments.dropFirst())
guard args.count == 2 else {
    failWith("usage: main <contract.json> <vector.canonical>", 2)
}

guard let contract = try? String(contentsOfFile: args[0], encoding: .utf8) else {
    failWith("cannot read contract", 3)
}
guard let vectorData = FileManager.default.contents(atPath: args[1]),
      let vector = String(data: vectorData, encoding: .utf8) else {
    failWith("cannot read vector", 3)
}

// The contract must actually assert this invariant. A consumer checking a
// property nobody declared is testing its own opinion, not B1's contract.
if !contract.contains("\"\(invariant)\": true") && !contract.contains("\"\(invariant)\":true") {
    failWith("contract does not assert \(invariant)", 4)
}

// Pull the top-level payload_digest without a JSON parser. Canonical form has
// no insignificant whitespace, so the key is followed immediately by ':' and
// then the quoted value.
let needle = "\"payload_digest\":\""
guard let start = vector.range(of: needle) else {
    failWith("vector carries no payload_digest", 5)
}
guard let end = vector.range(of: "\"", range: start.upperBound..<vector.endIndex) else {
    failWith("payload_digest value is not terminated", 5)
}
let digest = String(vector[start.upperBound..<end.lowerBound])

if digest.count != 64 {
    failWith("payload_digest is \(digest.count) characters, expected exactly 64", 5)
}
let allowed = Set("0123456789abcdef")
for (offset, character) in digest.enumerated() where !allowed.contains(character) {
    if character.isHexDigit {
        failWith(
            "payload_digest contains the upper-case hex character '\(character)' at " +
            "offset \(offset). Case is part of the bytes: an upper-case digest is a " +
            "different string and so a different record.",
            5
        )
    }
    failWith("payload_digest contains the non-hex character '\(character)' at offset \(offset)", 5)
}

print("SWIFT:POSTCONDITION:\(invariant)")
