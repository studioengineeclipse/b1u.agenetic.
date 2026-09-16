// B1 fourteen-language conformance: C++.
//
// Invariant owned: canonical_ends_with_single_newline
//
// The trailing newline is part of B1's canonical form, inherited from
// OmegaSigma13.9. There must be exactly one, it must be the last byte, and
// there must be no other raw newline anywhere outside a string literal. A
// serializer that emits two, or none, or CRLF, produces different bytes for the
// same logical record, and every digest downstream moves with it.
//
// digest_verification: NOT_IN_STDLIB. C++ has no standard SHA-256.
//
// Usage: main <contract.json> <vector.canonical>

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace {

constexpr const char* kInvariant = "canonical_ends_with_single_newline";

std::string read_file(const std::string& path, bool& ok) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) { ok = false; return {}; }
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    ok = true;
    return buffer.str();
}

// The contract must actually assert this invariant. A consumer checking a
// property nobody declared is testing its own opinion, not B1's contract.
bool contract_declares(const std::string& contract) {
    const std::string quoted = std::string("\"") + kInvariant + "\"";
    return contract.find(quoted + ": true") != std::string::npos ||
           contract.find(quoted + ":true") != std::string::npos;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: " << argv[0] << " <contract.json> <vector.canonical>\n";
        return 2;
    }

    bool ok = false;
    const std::string contract = read_file(argv[1], ok);
    if (!ok) { std::cerr << "C++: cannot read contract\n"; return 3; }
    const std::string vector = read_file(argv[2], ok);
    if (!ok) { std::cerr << "C++: cannot read vector\n"; return 3; }

    if (!contract_declares(contract)) {
        std::cerr << "C++: contract does not assert " << kInvariant << "\n";
        return 4;
    }

    if (vector.empty()) {
        std::cerr << "C++: vector is empty; canonical bytes always end with a newline\n";
        return 5;
    }
    if (vector.back() != '\n') {
        std::cerr << "C++: canonical bytes do not end with a newline\n";
        return 5;
    }
    if (vector.size() >= 2 && vector[vector.size() - 2] == '\n') {
        std::cerr << "C++: canonical bytes end with more than one newline\n";
        return 5;
    }
    if (vector.size() >= 2 && vector[vector.size() - 2] == '\r') {
        std::cerr << "C++: canonical bytes end with CRLF; the terminator is a bare newline\n";
        return 5;
    }

    // Any newline before the last byte would have to be inside a string
    // literal, where it would appear escaped as \n rather than raw. A raw one
    // means the writer pretty-printed.
    const auto stray = vector.find('\n');
    if (stray != vector.size() - 1) {
        std::cerr << "C++: raw newline at offset " << stray
                  << " before the terminator; canonical bytes are not pretty-printed\n";
        return 5;
    }

    std::cout << "C++:POSTCONDITION:" << kInvariant << "\n";
    return 0;
}
