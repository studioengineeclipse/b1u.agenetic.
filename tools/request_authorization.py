#!/usr/bin/env python3
"""The outstanding work, as authority envelopes rather than as a wish list.

B1's governing rule is that a persistent effect needs authorization **bound to a
specific envelope** carrying the proposed action, target, scope, expected
effect, relevant current state, current plan and causal objective. A blanket
"go ahead" is not that, and this tool exists so that asking for authorization
produces the same artifact B1 would require of anything else it does.

Applied to itself, in other words. Every item below is a real
`AuthorityEnvelope` with a real digest. Approving one means naming its digest;
approving "the blocked items" means nothing the gate could act on.

The distinction this tool is actually for
-----------------------------------------
Three of these cannot be unblocked by authorization at all, and lumping them in
with the ones that can is the failure mode. A model download is refused by the
network gateway; the Codex Security archive is not in this container; the
OmniBook is not reachable from here. **Authorization that cannot produce the
effect is not authorization, it is a wish** -- so those are reported as
`ENVIRONMENTAL` and no envelope is offered for them, because an envelope nobody
can execute is worse than no envelope: it looks like progress.

Two more are not the user's to give unless they hold the rights. Relicensing
ΩΣ13.9 and publishing anything derived from it are the rights holder's
decisions, and this tool does not assume who that is.

    python3 tools/request_authorization.py
    python3 tools/request_authorization.py --json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_authority import AuthorityEnvelope  # noqa: E402
from b1_policy import Capability, CapabilityPolicy  # noqa: E402
from b1_protocol.canonical import digest_value  # noqa: E402

# The policy these envelopes would be granted under. Written out here rather
# than assumed, because an envelope binds the policy digest and an envelope
# built against a policy nobody declared is one the gate refuses.
REQUEST_POLICY = CapabilityPolicy(
    policy_id="b1-outstanding-work",
    allow=(
        Capability("env.install", ("toolchain/**",), "REVERSIBLE",
                   reason="a toolchain in an ephemeral container is discarded with it"),
        Capability("vcs.pull_request", ("studioengineeclipse/b1u.agenetic./**",),
                   "COMPENSATABLE",
                   reason="a pull request can be closed, but reviewers have seen it"),
        Capability("repo.publish", ("**",), "IRREVERSIBLE",
                   reason="published content is indexed and cached beyond recall"),
    ),
    description="Capabilities the outstanding work would need, and nothing else.",
)


def why_environmental() -> list[dict[str, str]]:
    """Blocks no authorization can clear, with the evidence for each.

    Measured, not assumed. A network policy denial and an absent toolchain are
    facts about this container, and reporting them as "awaiting approval" would
    put the user in the position of granting something that then does not
    happen -- which teaches them their approvals do not mean anything.
    """
    missing = [c for c in ("kotlinc", "swiftc", "dotnet", "dart") if not shutil.which(c)]
    return [
        {
            "item": "download and benchmark a local model",
            "blocked_by": "ENVIRONMENTAL",
            "evidence": "the network gateway answered 403 to CONNECT for ollama.com; "
                        "measured this session",
            "note": "and the OmniBook is not reachable from here, so numbers measured in "
                    "this container would be the wrong machine's numbers",
        },
        {
            "item": "port Codex Security's four schemas and fifteen skills (ADR-0005)",
            "blocked_by": "ENVIRONMENTAL",
            "evidence": "the archive is not present in this container",
            "note": "the shapes would have to be invented, and inventing them while "
                    "citing ADR-0005 would be claiming a port that did not happen",
        },
        {
            "item": "observe the four unobserved language consumers",
            "blocked_by": "PARTLY ENVIRONMENTAL",
            "evidence": f"absent here: {', '.join(missing) or 'none'}; "
                        f"download.swift.org and the Kotlin releases host are both refused "
                        f"by the gateway, and the .NET build host is too",
            "note": "the Dart SDK archive IS reachable (HTTP 200), so one of the four is "
                    "an authorization question rather than an environmental one",
        },
    ]


def envelopes() -> list[dict[str, object]]:
    """One envelope per item authorization could actually execute."""
    policy_digest = REQUEST_POLICY.digest()

    # The current state each envelope is bound to. Real digests over real
    # facts, so that if any of this changes the authorization goes stale rather
    # than silently applying to a different situation.
    state = digest_value({
        "branch": "claude/b1-local-omega13-handoff-auv8bc",
        "polyglot_observed": 10,
        "polyglot_expected": 14,
        "publication": "BLOCKED",
        "privacy": "NO_OBJECTION",
    })
    plan = digest_value({"phase": "G1 complete", "next": "the three requests below"})

    requests = [
        {
            "authority_id": "req-install-dart",
            "proposed_action": "env.install",
            "target": "toolchain/dart",
            "scope": ("toolchain/**",),
            "expected_effect": (
                "the Dart SDK is on PATH, so polyglot participation is measured at 11 of "
                "14 rather than 10, and Dart's envelope invariant is checked rather than "
                "reported UNKNOWN"
            ),
            "causal_objective": (
                "close part of the weakest claim in the project: four of fourteen "
                "language consumers have never been executed"
            ),
            "persistence_class": "REVERSIBLE",
            "recoverability": "the container is ephemeral and is discarded with it; "
                              "B1 itself remains standard-library-only",
            "feasible": "YES -- the SDK archive answered HTTP 200 this session",
        },
        {
            "authority_id": "req-open-pull-request",
            "proposed_action": "vcs.pull_request",
            "target": "studioengineeclipse/b1u.agenetic./pull-request",
            "scope": ("studioengineeclipse/b1u.agenetic./**",),
            "expected_effect": (
                "a pull request from claude/b1-local-omega13-handoff-auv8bc exists, and "
                "whoever watches that repository is notified"
            ),
            "causal_objective": "put the work in front of reviewers",
            "persistence_class": "COMPENSATABLE",
            "recoverability": "a pull request can be closed; it cannot be unseen, and "
                              "notifications have already been delivered",
            "feasible": "YES",
        },
        {
            "authority_id": "req-publish-public-repository",
            "proposed_action": "repo.publish",
            "target": "a new public repository",
            "scope": ("**",),
            "expected_effect": (
                "the tree is public, indexed and cached by parties who will not be asked "
                "before they cache it"
            ),
            "causal_objective": "make B1 Local available",
            "persistence_class": "IRREVERSIBLE",
            "recoverability": "none. Deletion removes the repository and not the copies",
            "feasible": (
                "NOT BY THIS AUTHORIZATION ALONE -- verify_provenance.py reports "
                "PUBLICATION: BLOCKED because B1 derives design from an all-rights-reserved "
                "archive. That is the rights holder's decision, and this tool does not "
                "assume who that is"
            ),
        },
    ]

    built: list[dict[str, object]] = []
    for request in requests:
        envelope = AuthorityEnvelope(
            authority_id=str(request["authority_id"]),
            proposed_action=str(request["proposed_action"]),
            target=str(request["target"]),
            scope=tuple(request["scope"]),  # type: ignore[arg-type]
            expected_effect=str(request["expected_effect"]),
            state_digest=state,
            plan_digest=plan,
            policy_digest=policy_digest,
            causal_objective=str(request["causal_objective"]),
            persistence_class=str(request["persistence_class"]),
            granted_at_epoch=0,
        )
        built.append({
            **request,
            "scope": list(request["scope"]),  # type: ignore[arg-type]
            "digest": envelope.digest(),
            "envelope": envelope.to_canonical_dict(),
        })
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = {
        "policy_id": REQUEST_POLICY.policy_id,
        "policy_digest": REQUEST_POLICY.digest(),
        "requests": envelopes(),
        "not_an_authorization_question": why_environmental(),
        "note": (
            "Nothing here has been done. These are proposals; PLAN_READY is not "
            "EXECUTION_AUTHORIZED, and an approval has to name a digest to bind to "
            "anything."
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print("Outstanding work, as authority envelopes")
    print("=" * 74)
    print(f"policy {report['policy_id']}  {str(report['policy_digest'])[:16]}...\n")

    for request in report["requests"]:
        print(f"  {request['authority_id']}   [{request['digest'][:16]}...]")
        print(f"    action          {request['proposed_action']} on {request['target']}")
        print(f"    scope           {request['scope']}")
        print(f"    expected effect {request['expected_effect']}")
        print(f"    objective       {request['causal_objective']}")
        print(f"    recovery class  {request['persistence_class']}")
        print(f"                    {request['recoverability']}")
        print(f"    feasible        {request['feasible']}")
        print()

    print("-" * 74)
    print("Not an authorization question. No envelope is offered, because an envelope")
    print("nobody can execute looks like progress and is not:\n")
    for entry in report["not_an_authorization_question"]:
        print(f"  {entry['item']}")
        print(f"    {entry['blocked_by']}: {entry['evidence']}")
        print(f"    {entry['note']}")
        print()

    print("-" * 74)
    print(report["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
