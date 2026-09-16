// B1 fourteen-language conformance: Dart.
//
// Invariant owned: unicode_keys_are_ascending_by_code_point
//
// This is the invariant that proves the thing every other digest claim leans
// on. Python's sort_keys orders object keys by Unicode code point; Rust's
// BTreeMap<String,_> orders them by UTF-8 byte order. B1 asserts these agree,
// because UTF-8 is constructed so that byte-wise ordering equals code-point
// ordering. Asserting it is cheap. Checking it on real non-ASCII keys spanning
// Latin-1, Greek, Han and a character outside the Basic Multilingual Plane is
// what turns the assertion into evidence.
//
// Dart owns it because Dart is B1's cross-platform client surface, which is the
// most likely place for a locale-aware or collation-aware sort to be
// substituted for a code-point one without anybody noticing.
//
// digest_verification: NOT_IN_STDLIB. Dart's SHA-256 lives in package:crypto,
// not the standard library, and a portable single-file consumer cannot depend
// on it. B1 does not carry a hand-written SHA-256 it cannot execute everywhere;
// the gap is declared rather than faked.
//
// Usage: dart run main.dart <contract.json> <vector.canonical>

import 'dart:convert';
import 'dart:io';

const invariant = 'unicode_keys_are_ascending_by_code_point';

Never failWith(String message, int code) {
  stderr.writeln('Dart: $message');
  exit(code);
}

void main(List<String> args) {
  if (args.length != 2) {
    failWith('usage: dart run main.dart <contract.json> <vector.canonical>', 2);
  }

  final Map<String, dynamic> contract;
  final Map<String, dynamic> envelope;
  try {
    contract = jsonDecode(File(args[0]).readAsStringSync()) as Map<String, dynamic>;
    envelope = jsonDecode(utf8.decode(File(args[1]).readAsBytesSync()))
        as Map<String, dynamic>;
  } on FileSystemException catch (error) {
    failWith('cannot read an input: ${error.message}', 3);
  } on FormatException catch (error) {
    failWith('an input is not valid JSON: ${error.message}', 3);
  }

  // The contract must actually assert this invariant. A consumer checking a
  // property nobody declared is testing its own opinion, not B1's contract.
  final invariants = contract['invariants'];
  if (invariants is! Map || invariants[invariant] != true) {
    failWith('contract does not assert $invariant', 4);
  }

  final payload = envelope['payload'];
  if (payload is! Map<String, dynamic>) {
    failWith('vector payload is not an object', 5);
  }

  final keys = payload.keys.toList();
  // Dart's jsonDecode preserves insertion order, which for canonical bytes is
  // the order they were written in. That is exactly what needs checking.
  final nonAscii = keys.where((k) => k.runes.any((r) => r > 0x7f)).length;
  if (nonAscii == 0) {
    failWith(
      'payload has no non-ASCII keys, so it cannot demonstrate this invariant; '
      'point this consumer at the unicode-keys vector',
      5,
    );
  }

  for (var i = 1; i < keys.length; i++) {
    if (compareByCodePoint(keys[i - 1], keys[i]) >= 0) {
      failWith(
        'payload keys are not ascending by code point: "${keys[i - 1]}" then "${keys[i]}". '
        'If this fails, a collation-aware sort has been substituted for a code-point one, '
        'and every cross-language digest in B1 stops agreeing.',
        5,
      );
    }
  }

  print('DART:POSTCONDITION:$invariant');
}

/// Compare by Unicode code point, not by UTF-16 code unit.
///
/// Dart strings are UTF-16, so the default `String.compareTo` orders by code
/// unit. For characters outside the Basic Multilingual Plane that differs from
/// code-point order, which is the one case this invariant most needs to catch.
int compareByCodePoint(String a, String b) {
  final left = a.runes.toList();
  final right = b.runes.toList();
  final limit = left.length < right.length ? left.length : right.length;
  for (var i = 0; i < limit; i++) {
    if (left[i] != right[i]) return left[i] - right[i];
  }
  return left.length - right.length;
}
