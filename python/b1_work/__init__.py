"""B1 Local work runner: one task, from proposal to verified effect.

This package joins the layers below it into a path a task travels:

    propose -> tournament -> adjudicate -> authority -> permit
            -> execute -> read back -> consume -> journal

Everything else in B1 decides, records or refuses. `b1_work.effects` is the
only code that touches the world, which is why it is small.
"""

from .effects import (
    Attempt,
    EffectError,
    EffectExecutor,
    FileWriteEffect,
    OutsideScope,
)
from .runner import (
    AuthorityDecision,
    WorkOutcome,
    WorkRunner,
    always_refuse,
    workspace_runner,
)

__all__ = [
    "Attempt",
    "EffectError",
    "EffectExecutor",
    "FileWriteEffect",
    "OutsideScope",
    "AuthorityDecision",
    "WorkOutcome",
    "WorkRunner",
    "always_refuse",
    "workspace_runner",
]
