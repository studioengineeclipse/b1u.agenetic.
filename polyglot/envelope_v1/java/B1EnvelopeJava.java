// B1 fourteen-language conformance: Java.
//
// Invariant owned: top_level_keys_are_ascending
//
// Key order is what makes two independent serializers agree on bytes. Python's
// sort_keys orders by Unicode code point; Rust's BTreeMap<String,_> orders by
// UTF-8 byte order. Those agree because UTF-8 is constructed so that byte-wise
// ordering equals code-point ordering, but "agree" is a claim until something
// checks it. Java checks the top-level ordering; Dart checks it over the
// non-ASCII vector where the two orderings could most plausibly diverge.
//
// digest_verification: STDLIB. java.security.MessageDigest provides SHA-256, so
// this consumer also verifies that the vector's bytes hash to the declared
// digest.
//
// Usage: B1EnvelopeJava <contract.json> <vector.canonical> <expected.json>

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

public final class B1EnvelopeJava {

    private static final String INVARIANT = "top_level_keys_are_ascending";

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            System.err.println("usage: B1EnvelopeJava <contract.json> <vector.canonical> <expected.json>");
            System.exit(2);
        }

        String contract = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
        byte[] vectorBytes = Files.readAllBytes(Path.of(args[1]));
        String vector = new String(vectorBytes, StandardCharsets.UTF_8);
        String expected = Files.readString(Path.of(args[2]), StandardCharsets.UTF_8);

        // The contract must actually assert this invariant. A consumer checking
        // a property nobody declared is testing its own opinion.
        if (!contract.contains("\"" + INVARIANT + "\": true")
                && !contract.contains("\"" + INVARIANT + "\":true")) {
            System.err.println("Java: contract does not assert " + INVARIANT);
            System.exit(4);
        }

        List<String> keys = topLevelKeys(vector);
        if (keys.isEmpty()) {
            System.err.println("Java: found no top-level keys; the vector is not an object");
            System.exit(5);
        }
        for (int i = 1; i < keys.size(); i++) {
            byte[] previous = keys.get(i - 1).getBytes(StandardCharsets.UTF_8);
            byte[] current = keys.get(i).getBytes(StandardCharsets.UTF_8);
            if (compareUnsigned(previous, current) >= 0) {
                System.err.println("Java: top-level keys are not ascending: "
                        + keys.get(i - 1) + " then " + keys.get(i));
                System.exit(5);
            }
        }

        // STDLIB digest verification.
        String declared = extractString(expected, "canonical_sha256");
        if (declared == null) {
            System.err.println("Java: expected.json declares no canonical_sha256");
            System.exit(5);
        }
        MessageDigest sha256 = MessageDigest.getInstance("SHA-256");
        StringBuilder hex = new StringBuilder();
        for (byte b : sha256.digest(vectorBytes)) {
            hex.append(String.format("%02x", b));
        }
        if (!hex.toString().equals(declared)) {
            System.err.println("Java: digest mismatch; declared " + declared
                    + " but the bytes hash to " + hex);
            System.exit(5);
        }

        System.out.println("Java:POSTCONDITION:" + INVARIANT);
    }

    /** Collect keys at nesting depth 1, walking bytes rather than parsing. */
    private static List<String> topLevelKeys(String vector) {
        List<String> keys = new ArrayList<>();
        int depth = 0;
        boolean inString = false, escaped = false, expectingKey = false;
        StringBuilder current = new StringBuilder();

        for (int i = 0; i < vector.length(); i++) {
            char c = vector.charAt(i);
            if (inString) {
                if (escaped) {
                    escaped = false;
                    if (expectingKey) current.append(c);
                } else if (c == '\\') {
                    escaped = true;
                    if (expectingKey) current.append(c);
                } else if (c == '"') {
                    inString = false;
                    if (expectingKey) {
                        // Only a string immediately followed by ':' is a key.
                        int j = i + 1;
                        if (j < vector.length() && vector.charAt(j) == ':') {
                            keys.add(current.toString());
                        }
                        current.setLength(0);
                        expectingKey = false;
                    }
                } else if (expectingKey) {
                    current.append(c);
                }
                continue;
            }
            switch (c) {
                case '"' -> {
                    inString = true;
                    expectingKey = depth == 1;
                    current.setLength(0);
                }
                case '{', '[' -> depth++;
                case '}', ']' -> depth--;
                default -> { }
            }
        }
        return keys;
    }

    private static int compareUnsigned(byte[] a, byte[] b) {
        int limit = Math.min(a.length, b.length);
        for (int i = 0; i < limit; i++) {
            int diff = (a[i] & 0xff) - (b[i] & 0xff);
            if (diff != 0) return diff;
        }
        return a.length - b.length;
    }

    private static String extractString(String json, String key) {
        String needle = "\"" + key + "\"";
        int at = json.indexOf(needle);
        if (at < 0) return null;
        int colon = json.indexOf(':', at + needle.length());
        if (colon < 0) return null;
        int open = json.indexOf('"', colon);
        if (open < 0) return null;
        int close = json.indexOf('"', open + 1);
        if (close < 0) return null;
        return json.substring(open + 1, close);
    }
}
