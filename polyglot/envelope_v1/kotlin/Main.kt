// B1 fourteen-language conformance: Kotlin.
//
// Invariant owned: epoch_is_non_negative_integer
//
// The epoch is B1's fencing token. Every stale-attempt rejection in the root
// journal is a comparison against it, so it has to be an integer that compares
// monotonically. A fractional epoch cannot be compared reliably across
// languages for the same float-rendering reason B1 bans floats outright, and a
// negative epoch has no meaning: fences start at zero and only ever move up.
//
// Kotlin owns it because Kotlin is B1's Android surface, and a mobile client
// that misreads a fence would replay a superseded attempt against live state.
//
// digest_verification: STDLIB. java.security.MessageDigest is available on the
// JVM, so this consumer also verifies the declared digest.
//
// Usage: java -jar consumer.jar <contract.json> <vector.canonical> <expected.json>

import java.io.File
import java.security.MessageDigest
import kotlin.system.exitProcess

private const val INVARIANT = "epoch_is_non_negative_integer"

private fun failWith(message: String, code: Int): Nothing {
    System.err.println("Kotlin: $message")
    exitProcess(code)
}

/**
 * Pull a top-level scalar out of canonical JSON without a parser.
 *
 * Canonical form has no insignificant whitespace, so a top-level key is always
 * followed immediately by ':' and then the value. Scanning is used rather than
 * adding a JSON dependency: this consumer must build from a single source file
 * with nothing but the standard library.
 */
private fun scalarAfterKey(text: String, key: String): String? {
    val needle = "\"$key\":"
    val at = text.indexOf(needle)
    if (at < 0) return null
    val start = at + needle.length
    var end = start
    while (end < text.length && text[end] !in ",}]") end++
    return text.substring(start, end).trim()
}

fun main(args: Array<String>) {
    if (args.size != 3) {
        failWith("usage: consumer <contract.json> <vector.canonical> <expected.json>", 2)
    }

    val contract = File(args[0]).readText(Charsets.UTF_8)
    val vectorBytes = File(args[1]).readBytes()
    val expected = File(args[2]).readText(Charsets.UTF_8)
    val vector = String(vectorBytes, Charsets.UTF_8)

    // The contract must actually assert this invariant. A consumer checking a
    // property nobody declared is testing its own opinion, not B1's contract.
    if (!contract.contains("\"$INVARIANT\": true") && !contract.contains("\"$INVARIANT\":true")) {
        failWith("contract does not assert $INVARIANT", 4)
    }

    val raw = scalarAfterKey(vector, "epoch")
        ?: failWith("vector carries no epoch; every envelope must carry a fence", 5)

    if (raw.contains('.') || raw.contains('e') || raw.contains('E')) {
        failWith(
            "epoch $raw is not an integer literal. A fractional fence cannot be compared " +
                "reliably across languages, for the same reason B1 bans floats outright.",
            5
        )
    }
    val epoch = raw.toLongOrNull()
        ?: failWith("epoch $raw is not a valid integer", 5)
    if (epoch < 0) {
        failWith("epoch $epoch is negative; fences start at zero and only ever move up", 5)
    }

    val declared = Regex("\"canonical_sha256\"\\s*:\\s*\"([0-9a-f]{64})\"")
        .find(expected)?.groupValues?.get(1)
        ?: failWith("expected.json declares no canonical_sha256", 5)

    val actual = MessageDigest.getInstance("SHA-256")
        .digest(vectorBytes)
        .joinToString("") { "%02x".format(it) }
    if (actual != declared) {
        failWith("digest mismatch; declared $declared but the bytes hash to $actual", 5)
    }

    println("KOTLIN:POSTCONDITION:$INVARIANT")
}
