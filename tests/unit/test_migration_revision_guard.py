from __future__ import annotations

from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from migrations.baseline_contract import BASELINE_REVISION
from migrations.revision_guard import (
    LinearRevisionChain,
    RevisionContractError,
    load_linear_revision_chain,
    validate_database_revisions,
)


def _revision(directory: Path, revision: str, down_revision: str | tuple[str, ...] | None) -> None:
    versions = directory / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / f"{revision}.py").write_text(
        "\n".join(
            (
                f"revision = {revision!r}",
                f"down_revision = {down_revision!r}",
                "branch_labels = None",
                "depends_on = None",
                "def upgrade(): pass",
                "def downgrade(): pass",
                "",
            )
        ),
        encoding="utf-8",
    )


def _script(directory: Path) -> ScriptDirectory:
    return ScriptDirectory(str(directory))


def test_linear_0001_to_test_0002_is_a_supported_forward_chain(tmp_path: Path) -> None:
    _revision(tmp_path, BASELINE_REVISION, None)
    _revision(tmp_path, "test_0002", BASELINE_REVISION)

    chain = load_linear_revision_chain(_script(tmp_path))

    assert chain.revisions == (BASELINE_REVISION, "test_0002")
    assert validate_database_revisions(chain, []) is None
    assert validate_database_revisions(chain, [BASELINE_REVISION]) == BASELINE_REVISION
    assert validate_database_revisions(chain, ["test_0002"]) == "test_0002"


def test_multiple_code_heads_are_rejected(tmp_path: Path) -> None:
    _revision(tmp_path, BASELINE_REVISION, None)
    _revision(tmp_path, "test_0002_a", BASELINE_REVISION)
    _revision(tmp_path, "test_0002_b", BASELINE_REVISION)

    with pytest.raises(RevisionContractError, match="exactly one head"):
        load_linear_revision_chain(_script(tmp_path))


def test_merge_revision_is_rejected_even_with_one_head(tmp_path: Path) -> None:
    _revision(tmp_path, BASELINE_REVISION, None)
    _revision(tmp_path, "test_0002_a", BASELINE_REVISION)
    _revision(tmp_path, "test_0002_b", BASELINE_REVISION)
    _revision(tmp_path, "test_0003_merge", ("test_0002_a", "test_0002_b"))

    with pytest.raises(RevisionContractError, match="merges multiple parents"):
        load_linear_revision_chain(_script(tmp_path))


@pytest.mark.parametrize(
    "database_revisions",
    [
        ["unknown_revision"],
        [BASELINE_REVISION, "test_0002"],
    ],
)
def test_unknown_or_multiple_database_revisions_are_rejected(
    database_revisions: list[str],
) -> None:
    chain = LinearRevisionChain((BASELINE_REVISION, "test_0002"))

    with pytest.raises(RevisionContractError):
        validate_database_revisions(chain, database_revisions)
