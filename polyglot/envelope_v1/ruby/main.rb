#!/usr/bin/env ruby
# frozen_string_literal: true

# B1 fourteen-language conformance: Ruby.
#
# Invariant owned: effect_requires_persistence_class
#
# The companion to PHP's authority check. An envelope that names a persistent
# effect must also carry the recovery class that effect was classified under:
# REVERSIBLE, COMPENSATABLE, IRREVERSIBLE or UNKNOWN. The point is the ordering,
# not the label. Recovery limits have to be known *before* the effect happens;
# an effect whose class is discovered afterwards was authorised without anyone
# knowing what undoing it would cost.
#
# UNKNOWN is deliberately a permitted value. "We could not establish the
# recovery characteristics" is an honest classification and must be recordable;
# what is not permitted is the field being absent, which records nothing while
# looking like nothing is wrong.
#
# digest_verification: STDLIB. Ruby's digest library provides SHA-256.
#
# Usage: ruby main.rb <contract.json> <vector.canonical> <expected.json>

require 'digest'
require 'json'

INVARIANT = 'effect_requires_persistence_class'
ALLOWED = %w[REVERSIBLE COMPENSATABLE IRREVERSIBLE UNKNOWN].freeze

def fail_with(message, code)
  warn "Ruby: #{message}"
  exit code
end

args = ARGV
fail_with('usage: ruby main.rb <contract.json> <vector.canonical> <expected.json>', 2) if args.length != 3

contract_path, vector_path, expected_path = args

begin
  contract = JSON.parse(File.read(contract_path, encoding: 'UTF-8'))
  vector_bytes = File.binread(vector_path)
  expected = JSON.parse(File.read(expected_path, encoding: 'UTF-8'))
rescue SystemCallError => e
  fail_with("cannot read an input: #{e.message}", 3)
rescue JSON::ParserError => e
  fail_with("an input is not valid JSON: #{e.message}", 3)
end

# The contract must actually assert this invariant. A consumer checking a
# property nobody declared is testing its own opinion, not B1's contract.
unless contract.dig('invariants', INVARIANT) == true
  fail_with("contract does not assert #{INVARIANT}", 4)
end

envelope = JSON.parse(vector_bytes.force_encoding('UTF-8'))

# This consumer only means something against an effect record. Passing on a
# vector with no effect would be vacuous, and a vacuous pass looks like evidence
# without being any.
unless envelope.key?('effect_identity')
  fail_with(
    'vector carries no effect_identity, so it cannot demonstrate this invariant; ' \
    'point this consumer at the effect vector',
    5
  )
end

unless envelope.key?('persistence_class')
  fail_with(
    "effect #{envelope['effect_identity'].inspect} carries no persistence_class; " \
    'recovery limits must be classified before the effect, not discovered afterwards',
    5
  )
end

klass = envelope['persistence_class']
unless ALLOWED.include?(klass)
  fail_with(
    "persistence_class #{klass.inspect} is not one of #{ALLOWED.join(', ')}; " \
    'UNKNOWN is a permitted and honest answer, an unrecognised label is not',
    5
  )
end

actual = Digest::SHA256.hexdigest(vector_bytes)
unless actual == expected['canonical_sha256']
  fail_with(
    "digest mismatch; declared #{expected['canonical_sha256']} but the bytes hash to #{actual}",
    5
  )
end

puts "RUBY:POSTCONDITION:#{INVARIANT}"
exit 0
