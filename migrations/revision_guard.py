"""Pure revision-graph guards shared by Alembic and the locked migrator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from alembic.config import Config
from alembic.script import ScriptDirectory

from migrations.baseline_contract import BASELINE_REVISION


class RevisionContractError(RuntimeError):
    """The code revision graph or database revision cannot be migrated safely."""


@dataclass(frozen=True)
class LinearRevisionChain:
    """One immutable base followed by a single forward-only revision path."""

    revisions: tuple[str, ...]

    @property
    def base(self) -> str:
        return self.revisions[0]

    @property
    def head(self) -> str:
        return self.revisions[-1]

    def contains(self, revision: str) -> bool:
        return revision in self.revisions


def load_project_revision_chain() -> LinearRevisionChain:
    """Load the checked-in migration directory independently of the CWD."""

    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    return load_linear_revision_chain(ScriptDirectory.from_config(config))


def load_linear_revision_chain(script: ScriptDirectory) -> LinearRevisionChain:
    """Reject branches, merges, dependencies and disconnected revisions."""

    heads = tuple(script.get_heads())
    bases = tuple(script.get_bases())
    if len(heads) != 1:
        raise RevisionContractError(
            f"migration graph must have exactly one head; found {list(heads)!r}"
        )
    if bases != (BASELINE_REVISION,):
        raise RevisionContractError(
            f"migration graph must retain the immutable safety-first base; found {list(bases)!r}"
        )

    try:
        all_revisions = {revision.revision: revision for revision in script.walk_revisions()}
    except Exception as exc:
        raise RevisionContractError("migration graph cannot be resolved") from exc
    if len(all_revisions) == 0:
        raise RevisionContractError("migration graph is empty")

    descending: list[str] = []
    seen: set[str] = set()
    current = all_revisions.get(heads[0])
    while current is not None:
        if current.revision in seen:
            raise RevisionContractError("migration graph contains a revision cycle")
        seen.add(current.revision)
        descending.append(current.revision)

        dependencies = current.dependencies
        if dependencies not in (None, (), []):
            raise RevisionContractError(
                f"migration revision {current.revision!r} uses a dependency edge"
            )
        down_revision = current.down_revision
        if down_revision is None:
            break
        if not isinstance(down_revision, str):
            raise RevisionContractError(
                f"migration revision {current.revision!r} merges multiple parents"
            )
        current = all_revisions.get(down_revision)
        if current is None:
            raise RevisionContractError(
                f"migration revision references missing parent {down_revision!r}"
            )

    if seen != set(all_revisions):
        disconnected = sorted(set(all_revisions) - seen)
        raise RevisionContractError(
            f"migration graph is not one linear chain; disconnected revisions={disconnected!r}"
        )
    revisions = tuple(reversed(descending))
    if revisions[0] != BASELINE_REVISION:
        raise RevisionContractError(
            f"migration chain starts at {revisions[0]!r}, expected {BASELINE_REVISION!r}"
        )
    return LinearRevisionChain(revisions=revisions)


def validate_database_revisions(
    chain: LinearRevisionChain,
    revisions: Iterable[str],
) -> str | None:
    """Return the current ancestor, or ``None`` for an unversioned base target."""

    current_revisions = tuple(str(revision) for revision in revisions)
    if len(current_revisions) > 1:
        raise RevisionContractError(
            f"database has multiple Alembic revisions; found {list(current_revisions)!r}"
        )
    if not current_revisions:
        return None
    current = current_revisions[0]
    if not chain.contains(current):
        raise RevisionContractError(
            f"database revision {current!r} is unknown or not an ancestor of {chain.head!r}"
        )
    return current


__all__ = [
    "LinearRevisionChain",
    "RevisionContractError",
    "load_linear_revision_chain",
    "load_project_revision_chain",
    "validate_database_revisions",
]
