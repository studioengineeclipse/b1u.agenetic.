"""The resource ledger: what B1 believes will fit, and what it refuses to try.

The OmniBook has 16 GB of soldered LPDDR5x shared with an Adreno X1-45 iGPU.
The handoff's rule follows from that and is worth restating exactly:

    Do not load one model process per logical agent.

A logical agent is a role in a tournament. A resident model is a multi-gigabyte
allocation. Conflating them is how a design with nine specialists tries to hold
nine models in memory and the machine starts swapping.

What this ledger is, and is not
-------------------------------
It is an **accounting** of declared costs against a declared budget. It refuses
a load it predicts will not fit, so hot-loading is bounded rather than hopeful.

It is **not a measurement**. Every figure it reasons about is a *declared
estimate* from a model profile, not an observed footprint. The handoff is blunt
about this and so is the code: model file size is not total RAM requirement, and
until something runs on the actual machine, every number here is a
`WORKING_ASSUMPTION`. `Reservation.basis` carries that grade so a caller cannot
read a prediction as a measurement.

The thing this makes possible
-----------------------------
    small router resident
      + hot-load specialist A -> evidence -> unload
      + hot-load specialist B -> evidence -> unload
      + verifier / synthesiser

which is the handoff's §10 pattern, and it only works if something is counting.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "LedgerError",
    "WouldNotFit",
    "ModelProfile",
    "Reservation",
    "ResourceLedger",
    "MIB",
    "GIB",
]

MIB = 1024 * 1024
GIB = 1024 * MIB


class LedgerError(Exception):
    """The ledger refused an operation."""


class WouldNotFit(LedgerError):
    """The predicted footprint exceeds the remaining budget.

    Raised *before* anything is loaded. The alternative — discovering it
    afterwards — on a machine with soldered memory and no swap headroom means
    the OS chooses what dies, and it will not choose well.
    """


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """A model's declared costs. Every field is an estimate until benchmarked.

    ``weights_bytes`` is the quantised file size, which is the *floor* on
    residency, never the total. ``runtime_overhead_bytes`` covers the
    allocator, graph and scratch buffers the runtime needs on top, and
    ``kv_bytes_per_token`` is what each token of context costs.
    """

    model_id: str
    provider_id: str
    weights_bytes: int
    kv_bytes_per_token: int
    runtime_overhead_bytes: int
    # 8K-16K unless real benchmarks justify more, per the handoff. Context is
    # the cost people forget: it is paid per resident model, per token.
    default_context_tokens: int = 8192
    roles: tuple[str, ...] = ()
    notes: str = ""

    def footprint_bytes(self, context_tokens: int | None = None) -> int:
        """Predicted resident bytes at a given context length."""
        tokens = self.default_context_tokens if context_tokens is None else context_tokens
        if tokens < 0:
            raise LedgerError("context_tokens must be non-negative")
        return (
            self.weights_bytes
            + self.runtime_overhead_bytes
            + self.kv_bytes_per_token * tokens
        )


@dataclass(frozen=True, slots=True)
class Reservation:
    """A held claim on the budget."""

    model_id: str
    bytes_reserved: int
    context_tokens: int
    # Always "WORKING_ASSUMPTION" until a real measurement replaces the
    # profile's estimates. Carried on the reservation rather than documented
    # elsewhere, so a caller reading this object cannot miss it.
    basis: str = "WORKING_ASSUMPTION"


@dataclass
class ResourceLedger:
    """Accounting for model residency against a fixed budget.

    ``total_bytes`` is the machine's RAM. ``reserved_bytes`` is what B1 holds
    back for everything that is not a model: the OS, the desktop UI, the
    journal and gate, the Python and Rust peers. Models compete for what is
    left, and that remainder is a lot smaller than the headline number — which
    is exactly the arithmetic a 16 GB machine punishes you for skipping.
    """

    total_bytes: int
    reserved_bytes: int
    _held: dict[str, Reservation] = field(default_factory=dict)

    @classmethod
    def for_omnibook(cls, *, reserved_gib: float = 6.0) -> "ResourceLedger":
        """A ledger shaped for the target machine.

        16 GB total, with a default 6 GiB held back. That reserve is a
        deliberate choice rather than a measurement: Windows 11 ARM64 plus a
        Tauri UI plus two runtime peers plus the iGPU's share is not a small
        number, and reserving too little produces exactly the swap-thrash this
        class exists to prevent. Revise it with measurements from the actual
        machine, not with optimism.
        """
        return cls(total_bytes=16 * GIB, reserved_bytes=int(reserved_gib * GIB))

    @property
    def budget_bytes(self) -> int:
        """What models may collectively occupy."""
        return max(0, self.total_bytes - self.reserved_bytes)

    @property
    def held_bytes(self) -> int:
        return sum(r.bytes_reserved for r in self._held.values())

    @property
    def available_bytes(self) -> int:
        return self.budget_bytes - self.held_bytes

    def resident(self) -> tuple[str, ...]:
        return tuple(sorted(self._held))

    def would_fit(self, profile: ModelProfile, context_tokens: int | None = None) -> bool:
        return profile.footprint_bytes(context_tokens) <= self.available_bytes

    def reserve(
        self, profile: ModelProfile, context_tokens: int | None = None
    ) -> Reservation:
        """Hold budget for a model, or refuse and hold nothing.

        Refusing is the point. A ledger that reported a shortfall but reserved
        anyway would be a log, not a budget.
        """
        if profile.model_id in self._held:
            raise LedgerError(
                f"{profile.model_id!r} is already resident; releasing and re-reserving "
                f"is how a caller says it changed the context length"
            )
        tokens = (
            profile.default_context_tokens if context_tokens is None else context_tokens
        )
        needed = profile.footprint_bytes(tokens)
        if needed > self.available_bytes:
            raise WouldNotFit(
                f"{profile.model_id!r} needs {needed / GIB:.2f} GiB at {tokens} tokens "
                f"of context, but only {self.available_bytes / GIB:.2f} GiB of the "
                f"{self.budget_bytes / GIB:.2f} GiB model budget is free "
                f"(resident: {', '.join(self.resident()) or 'nothing'}). "
                f"Unload a specialist, shorten the context, or choose a smaller model."
            )
        reservation = Reservation(
            model_id=profile.model_id, bytes_reserved=needed, context_tokens=tokens
        )
        self._held[profile.model_id] = reservation
        return reservation

    def release(self, model_id: str) -> int:
        """Give budget back. Returns the bytes released."""
        reservation = self._held.pop(model_id, None)
        if reservation is None:
            raise LedgerError(f"{model_id!r} is not resident, so there is nothing to release")
        return reservation.bytes_reserved

    def release_all(self) -> int:
        total = self.held_bytes
        self._held.clear()
        return total

    def make_room_for(
        self, profile: ModelProfile, *, keep: tuple[str, ...] = (), context_tokens: int | None = None
    ) -> tuple[str, ...]:
        """Evict resident models until ``profile`` fits. Returns what was evicted.

        ``keep`` protects models that must stay — the router, typically, which
        is small and resident precisely so routing never pays a load.

        Evicts largest-first: fewest evictions to free a given amount, so the
        fewest specialists pay a reload. Raises without evicting anything if
        even an empty budget could not hold the model, because partially
        tearing down residency to then fail is the worst of both.
        """
        needed = profile.footprint_bytes(context_tokens)
        if needed > self.budget_bytes:
            raise WouldNotFit(
                f"{profile.model_id!r} needs {needed / GIB:.2f} GiB, which exceeds the "
                f"entire {self.budget_bytes / GIB:.2f} GiB model budget. No eviction can "
                f"help. This model does not belong on this machine at this context length."
            )

        evictable = sorted(
            (r for name, r in self._held.items() if name not in keep),
            key=lambda r: r.bytes_reserved,
            reverse=True,
        )
        evicted: list[str] = []
        for reservation in evictable:
            if needed <= self.available_bytes:
                break
            self.release(reservation.model_id)
            evicted.append(reservation.model_id)

        if needed > self.available_bytes:
            protected = ", ".join(keep) or "nothing"
            raise WouldNotFit(
                f"{profile.model_id!r} needs {needed / GIB:.2f} GiB; after evicting "
                f"{len(evicted)} model(s) only {self.available_bytes / GIB:.2f} GiB is free "
                f"and {protected} is protected from eviction"
            )
        return tuple(evicted)

    def report(self) -> dict[str, object]:
        """A snapshot, with its epistemic grade attached.

        The grade is not decoration. Everything here is arithmetic over
        declared estimates, and a reader who mistakes it for a measurement of
        the running machine will trust a number nobody has observed.
        """
        return {
            "basis": "WORKING_ASSUMPTION",
            "basis_note": (
                "Arithmetic over declared model profiles, not observed RSS. "
                "Replace the profiles with measurements from the target machine "
                "before treating any of this as VERIFIED."
            ),
            "total_gib": round(self.total_bytes / GIB, 2),
            "reserved_for_non_models_gib": round(self.reserved_bytes / GIB, 2),
            "model_budget_gib": round(self.budget_bytes / GIB, 2),
            "held_gib": round(self.held_bytes / GIB, 2),
            "available_gib": round(self.available_bytes / GIB, 2),
            "resident": list(self.resident()),
        }
