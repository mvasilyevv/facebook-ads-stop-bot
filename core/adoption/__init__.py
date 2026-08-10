"""Safety-first configuration adoption bundle."""

from core.adoption.bundle import (
    AdoptionBundleV1,
    AdoptionSectionsV1,
    AdoptionValidationError,
    build_adoption_bundle,
    canonical_bundle_json,
    parse_adoption_bundle_json,
)
from core.adoption.profiles import SOURCE_PROFILES, get_source_profile
from core.adoption.service import apply_adoption_bundle, export_legacy_bundle

__all__ = [
    "AdoptionBundleV1",
    "AdoptionSectionsV1",
    "AdoptionValidationError",
    "SOURCE_PROFILES",
    "apply_adoption_bundle",
    "build_adoption_bundle",
    "canonical_bundle_json",
    "export_legacy_bundle",
    "get_source_profile",
    "parse_adoption_bundle_json",
]
