#!/usr/bin/env php
<?php
// B1 fourteen-language conformance: PHP.
//
// Invariant owned: effect_requires_authority_digest
//
// This is the load-bearing one for the authority model. An envelope that names
// a persistent effect must also name the authority envelope that permitted it.
// An effect record with no bound authority is unauthorised by construction,
// whatever the surrounding prose claims, and no amount of downstream checking
// can recover an authorisation that was never recorded.
//
// The check runs against the effect vector, and it also confirms the digest is
// well formed: an authority reference that is not a real 64-character hex
// digest cannot be resolved to an envelope, which makes it decoration.
//
// digest_verification: STDLIB. hash('sha256', ...) is built in.
//
// Usage: php main.php <contract.json> <vector.canonical> <expected.json>

declare(strict_types=1);

const INVARIANT = 'effect_requires_authority_digest';

function fail(string $message, int $code): never {
    fwrite(STDERR, "PHP: {$message}\n");
    exit($code);
}

$args = array_slice($argv, 1);
if (count($args) !== 3) {
    fail('usage: php main.php <contract.json> <vector.canonical> <expected.json>', 2);
}
[$contractPath, $vectorPath, $expectedPath] = $args;

$contractRaw = @file_get_contents($contractPath);
if ($contractRaw === false) { fail('cannot read contract', 3); }
$vectorBytes = @file_get_contents($vectorPath);
if ($vectorBytes === false) { fail('cannot read vector', 3); }
$expectedRaw = @file_get_contents($expectedPath);
if ($expectedRaw === false) { fail('cannot read expected', 3); }

$contract = json_decode($contractRaw, true);
$envelope = json_decode($vectorBytes, true);
$expected = json_decode($expectedRaw, true);

if (!is_array($contract) || !is_array($envelope) || !is_array($expected)) {
    fail('one of the inputs is not valid JSON', 3);
}

// The contract must actually assert this invariant. A consumer checking a
// property nobody declared is testing its own opinion, not B1's contract.
if (($contract['invariants'][INVARIANT] ?? null) !== true) {
    fail('contract does not assert ' . INVARIANT, 4);
}

// This consumer only means something against an effect record. Passing on a
// vector with no effect would be vacuous, and a vacuous pass looks like
// evidence without being any.
if (!array_key_exists('effect_identity', $envelope)) {
    fail(
        'vector carries no effect_identity, so it cannot demonstrate this invariant; '
        . 'point this consumer at the effect vector',
        5
    );
}

if (!array_key_exists('authority_envelope_digest', $envelope)) {
    fail(
        'effect ' . json_encode($envelope['effect_identity']) . ' names no '
        . 'authority_envelope_digest; an effect with no bound authority is '
        . 'unauthorised by construction',
        5
    );
}

$digest = $envelope['authority_envelope_digest'];
if (!is_string($digest) || preg_match('/^[0-9a-f]{64}$/', $digest) !== 1) {
    fail(
        'authority_envelope_digest is not 64 lowercase hex characters, so it cannot be '
        . 'resolved to an authority envelope; an unresolvable reference is decoration',
        5
    );
}

$actual = hash('sha256', $vectorBytes);
if ($actual !== ($expected['canonical_sha256'] ?? null)) {
    fail(
        'digest mismatch; declared ' . ($expected['canonical_sha256'] ?? 'nothing')
        . ' but the bytes hash to ' . $actual,
        5
    );
}

echo 'PHP:POSTCONDITION:' . INVARIANT . "\n";
exit(0);
