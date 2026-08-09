from __future__ import annotations

from typing import Any

import pytest

from core.adoption.profiles import (
    LEGACY_0036_PROFILE,
    LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE,
    LEGACY_BASELINE_PREFERENCES_PROFILE,
    AdoptionSourceProfileError,
    assert_exact_source_profile,
    get_source_profile,
)


class _ScalarResult:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def all(self) -> list[str]:
        return self._values


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_MappingResult":
        return self

    def __iter__(self):
        return iter(self._rows)


class _ProfileConnection:
    def __init__(
        self,
        *,
        profile_name: str,
        revision: str | None = None,
        omit_column: tuple[str, str] | None = None,
        override_type: tuple[tuple[str, str], tuple[str, str]] | None = None,
        add_preference_table: bool | None = None,
        add_display_preference_table: bool | None = None,
        normalized: bool = False,
    ) -> None:
        profile = get_source_profile(profile_name)
        self.revision = revision or profile.revision
        self.normalized = normalized
        table_columns = dict(profile.table_columns)
        if add_preference_table is True and "telegram_recipient_preferences" not in table_columns:
            preferences_profile = get_source_profile(LEGACY_BASELINE_PREFERENCES_PROFILE)
            table_columns["telegram_recipient_preferences"] = preferences_profile.table_columns[
                "telegram_recipient_preferences"
            ]
        elif add_preference_table is False:
            table_columns.pop("telegram_recipient_preferences", None)
        if (
            add_display_preference_table is True
            and "operator_display_preferences" not in table_columns
        ):
            display_profile = get_source_profile(LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE)
            table_columns["operator_display_preferences"] = display_profile.table_columns[
                "operator_display_preferences"
            ]
        elif add_display_preference_table is False:
            table_columns.pop("operator_display_preferences", None)

        self.rows: list[dict[str, Any]] = []
        for table_name, columns in table_columns.items():
            for column_name in sorted(columns):
                if omit_column == (table_name, column_name):
                    continue
                data_type, udt_name = profile.exported_column_types.get(
                    (table_name, column_name),
                    ("text", "text"),
                )
                if override_type is not None and override_type[0] == (
                    table_name,
                    column_name,
                ):
                    data_type, udt_name = override_type[1]
                self.rows.append(
                    {
                        "table_name": table_name,
                        "column_name": column_name,
                        "data_type": data_type,
                        "udt_name": udt_name,
                        "relation_kind": "r",
                    }
                )

    async def scalars(self, _statement) -> _ScalarResult:
        return _ScalarResult([self.revision])

    async def execute(self, _statement, _params) -> _MappingResult:
        return _MappingResult(self.rows)

    async def scalar(self, _statement) -> bool:
        return self.normalized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile_name",
    [
        LEGACY_0036_PROFILE,
        LEGACY_BASELINE_PREFERENCES_PROFILE,
        LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE,
    ],
)
async def test_explicit_source_profiles_require_exact_schema(profile_name: str) -> None:
    profile = get_source_profile(profile_name)

    await assert_exact_source_profile(
        _ProfileConnection(profile_name=profile_name),  # type: ignore[arg-type]
        profile,
    )


@pytest.mark.asyncio
async def test_source_profile_rejects_revision_or_column_drift() -> None:
    profile = get_source_profile(LEGACY_0036_PROFILE)

    with pytest.raises(AdoptionSourceProfileError, match="revision"):
        await assert_exact_source_profile(
            _ProfileConnection(  # type: ignore[arg-type]
                profile_name=LEGACY_0036_PROFILE,
                revision="unexpected_revision",
            ),
            profile,
        )
    with pytest.raises(AdoptionSourceProfileError, match="types"):
        await assert_exact_source_profile(
            _ProfileConnection(  # type: ignore[arg-type]
                profile_name=LEGACY_0036_PROFILE,
                override_type=(("offers", "ad_account_ids"), ("ARRAY", "_text")),
            ),
            profile,
        )
    with pytest.raises(AdoptionSourceProfileError, match="columns"):
        await assert_exact_source_profile(
            _ProfileConnection(  # type: ignore[arg-type]
                profile_name=LEGACY_0036_PROFILE,
                omit_column=("offers", "name"),
            ),
            profile,
        )


@pytest.mark.asyncio
async def test_no_preferences_profile_rejects_preference_table_instead_of_falling_back() -> None:
    profile = get_source_profile(LEGACY_0036_PROFILE)

    with pytest.raises(AdoptionSourceProfileError, match="preference"):
        await assert_exact_source_profile(
            _ProfileConnection(  # type: ignore[arg-type]
                profile_name=LEGACY_0036_PROFILE,
                add_preference_table=True,
            ),
            profile,
        )


@pytest.mark.asyncio
async def test_source_profile_never_auto_detects_operator_display_preferences() -> None:
    without_display = get_source_profile(LEGACY_BASELINE_PREFERENCES_PROFILE)
    with_display = get_source_profile(LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE)

    with pytest.raises(AdoptionSourceProfileError, match="operator display preference"):
        await assert_exact_source_profile(
            _ProfileConnection(  # type: ignore[arg-type]
                profile_name=LEGACY_BASELINE_PREFERENCES_PROFILE,
                add_display_preference_table=True,
            ),
            without_display,
        )
    with pytest.raises(AdoptionSourceProfileError, match="operator display preference"):
        await assert_exact_source_profile(
            _ProfileConnection(  # type: ignore[arg-type]
                profile_name=LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE,
                add_display_preference_table=False,
            ),
            with_display,
        )


@pytest.mark.asyncio
async def test_legacy_export_profile_rejects_normalized_catalog() -> None:
    profile = get_source_profile(LEGACY_0036_PROFILE)

    with pytest.raises(AdoptionSourceProfileError, match="normalized"):
        await assert_exact_source_profile(
            _ProfileConnection(  # type: ignore[arg-type]
                profile_name=LEGACY_0036_PROFILE,
                normalized=True,
            ),
            profile,
        )


def test_unknown_source_profile_is_rejected() -> None:
    with pytest.raises(AdoptionSourceProfileError, match="unknown explicit"):
        get_source_profile("auto-detect")
