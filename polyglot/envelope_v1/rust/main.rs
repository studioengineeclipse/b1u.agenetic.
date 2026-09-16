// B1 fourteen-language conformance: Rust.
//
// Invariant owned: non_ascii_is_unescaped
//
// B1's canonical form emits non-ASCII raw, as UTF-8, never as \uXXXX. This is
// inherited from OmegaSigma13.9 (ensure_ascii=False) and it is not cosmetic: a
// serializer that escapes produces entirely different bytes for the same
// logical value, so every digest downstream moves. Many JSON writers escape by
// default, which is exactly why this needs checking rather than assuming.
//
// Rust owns it because Rust is one of B1's two resident state peers, and this
// consumer is deliberately independent of crates/b1-protocol: it is compiled
// standalone with rustc and shares no code with the implementation it is
// checking. A consumer that imported the thing under test would only prove that
// code equals itself.
//
// digest_verification: NOT_IN_STDLIB. A standalone rustc build has no SHA-256,
// and B1 does not carry a hand-written one it cannot execute everywhere.
//
// Usage: main <contract.json> <vector.canonical>

use std::env;
use std::fs;
use std::process::ExitCode;

const INVARIANT: &str = "non_ascii_is_unescaped";

fn fail(message: &str, code: u8) -> ExitCode {
    eprintln!("Rust: {message}");
    ExitCode::from(code)
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.len() != 2 {
        return fail("usage: main <contract.json> <vector.canonical>", 2);
    }

    let contract = match fs::read_to_string(&args[0]) {
        Ok(text) => text,
        Err(err) => return fail(&format!("cannot read contract: {err}"), 3),
    };
    let vector_bytes = match fs::read(&args[1]) {
        Ok(bytes) => bytes,
        Err(err) => return fail(&format!("cannot read vector: {err}"), 3),
    };

    // The contract must actually assert this invariant. A consumer checking a
    // property nobody declared is testing its own opinion, not B1's contract.
    let quoted = format!("\"{INVARIANT}\"");
    if !contract.contains(&format!("{quoted}: true")) && !contract.contains(&format!("{quoted}:true"))
    {
        return fail(&format!("contract does not assert {INVARIANT}"), 4);
    }

    // The bytes must be valid UTF-8 in the first place. Invalid UTF-8 would
    // mean the writer produced something no other peer could decode.
    let text = match std::str::from_utf8(&vector_bytes) {
        Ok(text) => text,
        Err(err) => return fail(&format!("vector is not valid UTF-8: {err}"), 5),
    };

    // There must be at least one non-ASCII character, or this vector proves
    // nothing. Checking that an ASCII-only file contains no escapes is vacuous,
    // and a vacuous pass is worse than a failure because it looks like evidence.
    let non_ascii = text.chars().filter(|c| !c.is_ascii()).count();
    if non_ascii == 0 {
        return fail(
            "vector contains no non-ASCII characters, so it cannot demonstrate this \
             invariant; point this consumer at the unicode-keys vector",
            5,
        );
    }

    // Now the invariant itself: no \u escape may appear. Scan for a backslash
    // that is not itself escaped and is followed by 'u'.
    let bytes = text.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'\\' {
            if index + 1 < bytes.len() && bytes[index + 1] == b'u' {
                return fail(
                    &format!(
                        "found a \\u escape at offset {index}; canonical bytes emit non-ASCII \
                         raw, and an escaping serializer produces different bytes for the \
                         same logical value"
                    ),
                    5,
                );
            }
            index += 2; // skip the escaped character, so \\\\u is not a false positive
            continue;
        }
        index += 1;
    }

    println!("RUST:POSTCONDITION:{INVARIANT}");
    ExitCode::SUCCESS
}
