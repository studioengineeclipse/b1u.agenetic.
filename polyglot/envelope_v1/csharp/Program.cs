// B1 fourteen-language conformance: C#.
//
// Invariant owned: epistemic_status_is_one_of_four
//
// VERIFIED, WORKING_ASSUMPTION, UNKNOWN and IN_DOUBT are the only states B1
// recognises, and the set is closed on purpose. Every one of the three weaker
// states is a way of saying "this is not established", and a record carrying
// some fifth label would be a claim with no grade attached, which is precisely
// how a provider's success return quietly becomes a fact.
//
// The check is exact-match against the four. A case-insensitive or prefix match
// would let "verified" through, and a lowercase claim is not a different
// spelling of a graded one.
//
// C# owns it because C# is B1's Windows and .NET surface, which is the target
// platform where the desktop UI renders these grades to a person.
//
// digest_verification: STDLIB. System.Security.Cryptography provides SHA-256.
//
// Usage: dotnet run -- <contract.json> <vector.canonical> <expected.json>

using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

internal static class Program
{
    private const string Invariant = "epistemic_status_is_one_of_four";

    private static readonly string[] Allowed =
    {
        "VERIFIED", "WORKING_ASSUMPTION", "UNKNOWN", "IN_DOUBT"
    };

    private static int Fail(string message, int code)
    {
        Console.Error.WriteLine($"C#: {message}");
        return code;
    }

    private static int Main(string[] args)
    {
        if (args.Length != 3)
        {
            return Fail("usage: consumer <contract.json> <vector.canonical> <expected.json>", 2);
        }

        string contractText;
        byte[] vectorBytes;
        string expectedText;
        try
        {
            contractText = File.ReadAllText(args[0], Encoding.UTF8);
            vectorBytes = File.ReadAllBytes(args[1]);
            expectedText = File.ReadAllText(args[2], Encoding.UTF8);
        }
        catch (IOException error)
        {
            return Fail($"cannot read an input: {error.Message}", 3);
        }

        using var contract = JsonDocument.Parse(contractText);
        using var envelope = JsonDocument.Parse(vectorBytes);
        using var expected = JsonDocument.Parse(expectedText);

        // The contract must actually assert this invariant. A consumer checking
        // a property nobody declared is testing its own opinion, not B1's.
        if (!contract.RootElement.TryGetProperty("invariants", out var invariants)
            || !invariants.TryGetProperty(Invariant, out var declared)
            || declared.ValueKind != JsonValueKind.True)
        {
            return Fail($"contract does not assert {Invariant}", 4);
        }

        if (!envelope.RootElement.TryGetProperty("epistemic_status", out var statusElement)
            || statusElement.ValueKind != JsonValueKind.String)
        {
            return Fail(
                "vector carries no epistemic_status; every consequential record must be graded",
                5);
        }

        var status = statusElement.GetString();
        if (!Allowed.Contains(status, StringComparer.Ordinal))
        {
            return Fail(
                $"epistemic_status \"{status}\" is not one of {string.Join(", ", Allowed)}. " +
                "A record carrying an unrecognised grade is a claim with no grade at all.",
                5);
        }

        if (!expected.RootElement.TryGetProperty("canonical_sha256", out var digestElement)
            || digestElement.ValueKind != JsonValueKind.String)
        {
            return Fail("expected.json declares no canonical_sha256", 5);
        }

        var actual = Convert.ToHexString(SHA256.HashData(vectorBytes)).ToLowerInvariant();
        if (!string.Equals(actual, digestElement.GetString(), StringComparison.Ordinal))
        {
            return Fail(
                $"digest mismatch; declared {digestElement.GetString()} " +
                $"but the bytes hash to {actual}",
                5);
        }

        Console.WriteLine($"CSHARP:POSTCONDITION:{Invariant}");
        return 0;
    }
}
