/*
 * B1 fourteen-language conformance: C.
 *
 * Invariant owned: canonical_has_no_insignificant_whitespace
 *
 * A single stray space between tokens changes the canonical bytes, therefore
 * the digest, therefore history. C checks this by scanning bytes directly,
 * which is the right tool for the job and needs no JSON parser: the question is
 * about bytes, not about structure.
 *
 * digest_verification: NOT_IN_STDLIB. C has no standard SHA-256, and B1 does
 * not carry a hand-written one it cannot execute everywhere.
 *
 * Usage: main <contract.json> <vector.canonical>
 * Exits 0 and prints C:POSTCONDITION:<invariant> when the invariant holds.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define INVARIANT "canonical_has_no_insignificant_whitespace"

static char *read_file(const char *path, long *length) {
    FILE *handle = fopen(path, "rb");
    if (!handle) return NULL;
    if (fseek(handle, 0, SEEK_END) != 0) { fclose(handle); return NULL; }
    long size = ftell(handle);
    if (size < 0) { fclose(handle); return NULL; }
    rewind(handle);
    char *buffer = malloc((size_t)size + 1);
    if (!buffer) { fclose(handle); return NULL; }
    if (fread(buffer, 1, (size_t)size, handle) != (size_t)size) {
        free(buffer); fclose(handle); return NULL;
    }
    buffer[size] = '\0';
    fclose(handle);
    *length = size;
    return buffer;
}

/* The contract must actually assert this invariant. A consumer that checks a
 * property nobody declared is testing its own opinion, not B1's contract. */
static int contract_declares(const char *contract) {
    char needle[256];
    snprintf(needle, sizeof needle, "\"%s\": true", INVARIANT);
    if (strstr(contract, needle)) return 1;
    snprintf(needle, sizeof needle, "\"%s\":true", INVARIANT);
    return strstr(contract, needle) != NULL;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <contract.json> <vector.canonical>\n", argv[0]);
        return 2;
    }

    long contract_len = 0, vector_len = 0;
    char *contract = read_file(argv[1], &contract_len);
    if (!contract) { fprintf(stderr, "C: cannot read contract\n"); return 3; }
    char *vector = read_file(argv[2], &vector_len);
    if (!vector) { free(contract); fprintf(stderr, "C: cannot read vector\n"); return 3; }

    if (!contract_declares(contract)) {
        fprintf(stderr, "C: contract does not assert %s\n", INVARIANT);
        free(contract); free(vector);
        return 4;
    }

    /* Walk the bytes tracking whether we are inside a JSON string. Whitespace
     * inside a string is content and must survive; whitespace outside one is
     * insignificant and must not exist in canonical form. The trailing newline
     * is the single exception, and is owned by the C++ consumer. */
    int in_string = 0, escaped = 0, failed = 0;
    for (long i = 0; i < vector_len; i++) {
        char c = vector[i];
        if (in_string) {
            if (escaped) { escaped = 0; }
            else if (c == '\\') { escaped = 1; }
            else if (c == '"') { in_string = 0; }
            continue;
        }
        if (c == '"') { in_string = 1; continue; }
        if (c == '\n' && i == vector_len - 1) continue;  /* the canonical terminator */
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
            fprintf(stderr,
                    "C: insignificant whitespace (0x%02x) at offset %ld; "
                    "canonical bytes carry none outside string literals\n",
                    (unsigned char)c, i);
            failed = 1;
            break;
        }
    }
    if (!failed && in_string) {
        fprintf(stderr, "C: vector ends inside an unterminated string literal\n");
        failed = 1;
    }

    free(contract);
    free(vector);
    if (failed) return 5;

    printf("C:POSTCONDITION:%s\n", INVARIANT);
    return 0;
}
