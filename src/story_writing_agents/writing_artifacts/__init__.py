"""Stable value contracts for canonical writing artifacts.

The package facade is deliberately side-effect free.  Filesystem-backed
repository behavior is imported from ``writing_artifacts.repository`` by the
runtime that owns it rather than re-exported here.
"""

from .catalog import (
    CORE_ARTIFACT_PATHS,
    ArtifactCatalog,
    ArtifactCatalogEntry,
    ArtifactKind,
    canonical_artifact_kind,
    manuscript_artifact_path,
)
from .references import (
    MAX_ARTIFACT_PATH_BYTES,
    MAX_REFERENCE_COUNT,
    MAX_REFERENCE_TOTAL_BYTES,
    MAX_REFERENCE_UTF8_BYTES,
    MAX_VIEWER_UTF8_BYTES,
    ArtifactLineRange,
    ArtifactReference,
)

__all__ = (
    "CORE_ARTIFACT_PATHS",
    "MAX_ARTIFACT_PATH_BYTES",
    "MAX_REFERENCE_COUNT",
    "MAX_REFERENCE_TOTAL_BYTES",
    "MAX_REFERENCE_UTF8_BYTES",
    "MAX_VIEWER_UTF8_BYTES",
    "ArtifactCatalog",
    "ArtifactCatalogEntry",
    "ArtifactKind",
    "ArtifactLineRange",
    "ArtifactReference",
    "canonical_artifact_kind",
    "manuscript_artifact_path",
)
