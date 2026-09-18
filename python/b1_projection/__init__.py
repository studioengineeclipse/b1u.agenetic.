"""B1 Local projections: derived views, and three ways to deploy them.

A projection is disposable by construction. If it disagrees with the journal,
it loses — there is no merge, no reconciliation, and no case where the derived
view is the more current one.
"""

from .store import (
    SUFFIX,
    DriftReport,
    OutsideWorkspace,
    ProjectionError,
    ProjectionMissing,
    ProjectionMode,
    ProjectionStore,
    RebuildReport,
)
from .views import (
    VIEW_NAMES,
    Views,
    authority_view,
    build_views,
    effects_view,
    events_view,
    heads_view,
    view_digests,
    views_digest,
)

__all__ = [
    "SUFFIX",
    "VIEW_NAMES",
    "DriftReport",
    "OutsideWorkspace",
    "ProjectionError",
    "ProjectionMissing",
    "ProjectionMode",
    "ProjectionStore",
    "RebuildReport",
    "Views",
    "authority_view",
    "build_views",
    "effects_view",
    "events_view",
    "heads_view",
    "view_digests",
    "views_digest",
]
