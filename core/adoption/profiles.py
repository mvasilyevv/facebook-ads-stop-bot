"""Explicit migration-only source schema profiles for adoption export."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

LEGACY_0036_PROFILE = "legacy-array-0036-no-preferences"
LEGACY_BASELINE_PREFERENCES_PROFILE = "legacy-array-baseline-with-preferences"
LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE = "legacy-array-baseline-with-display-preferences"


class AdoptionSourceProfileError(RuntimeError):
    """The selected legacy source profile does not exactly match the database."""


_OFFER_COLUMNS = frozenset(
    {
        "id",
        "code",
        "name",
        "vertical",
        "pixel_id",
        "is_active",
        "ad_account_ids",
        "countries",
        "created_at",
        "updated_at",
    }
)
_RECIPIENT_COLUMNS = frozenset(
    {
        "id",
        "chat_id",
        "telegram_user_id",
        "username",
        "display_name",
        "role",
        "invite_id",
        "revoked_at",
        "created_at",
    }
)
_SYSTEM_COLUMNS = frozenset({"id", "key", "value", "description", "created_at", "updated_at"})
_LEGACY_RULE_COLUMNS = frozenset(
    {
        "id",
        "offer_id",
        "spend_no_event_threshold",
        "cpa_threshold",
        "cpm_threshold",
        "ctr_threshold",
        "frequency_threshold",
        "funnel_ratio_threshold",
        "stop_percent_of_rule",
        "warning_percent_of_stop",
        "created_at",
        "updated_at",
    }
)
_BASELINE_RULE_COLUMNS = frozenset(
    {
        "id",
        "offer_id",
        "cpa_threshold",
        "currency",
        "frequency_threshold",
        "stop_percent_of_rule",
        "warning_percent_of_stop",
        "created_at",
        "updated_at",
    }
)
_LEGACY_OBSERVER_COLUMNS = frozenset(
    {
        "id",
        "singleton_key",
        "interval_seconds",
        "jitter_seconds",
        "stale_data_threshold_seconds",
        "install_cost_usd",
        "agent_commission_percent",
        "is_scanning_enabled",
        "auto_enable_recommendations",
        "owner_campaign_tag",
        "campaign_ids",
        "created_at",
        "updated_at",
    }
)
_BASELINE_OBSERVER_COLUMNS = frozenset(
    {
        "id",
        "singleton_key",
        "interval_seconds",
        "is_scanning_enabled",
        "auto_enable_recommendations",
        "owner_campaign_tag",
        "campaign_ids",
        "created_at",
        "updated_at",
    }
)
_PREFERENCE_COLUMNS = frozenset(
    {
        "recipient_id",
        "timezone",
        "min_severity",
        "quiet_hours_start",
        "quiet_hours_end",
        "digest_local_time",
        "categories",
        "is_enabled",
        "created_at",
        "updated_at",
    }
)
_OPERATOR_DISPLAY_PREFERENCE_COLUMNS = frozenset(
    {
        "owner_recipient_id",
        "timezone_name",
        "created_at",
        "updated_at",
    }
)


@dataclass(frozen=True)
class LegacySourceProfile:
    name: str
    revision: str
    table_columns: dict[str, frozenset[str]]
    exported_column_types: dict[tuple[str, str], tuple[str, str]]
    has_recipient_preferences: bool
    has_operator_display_preferences: bool
    offer_rule_currency_column: bool


_COMMON_EXPORTED_TYPES = {
    ("offers", "code"): ("character varying", "varchar"),
    ("offers", "name"): ("character varying", "varchar"),
    ("offers", "vertical"): ("character varying", "varchar"),
    ("offers", "pixel_id"): ("character varying", "varchar"),
    ("offers", "is_active"): ("boolean", "bool"),
    ("offers", "ad_account_ids"): ("ARRAY", "_varchar"),
    ("offers", "countries"): ("ARRAY", "_varchar"),
    ("offer_rules", "offer_id"): ("uuid", "uuid"),
    ("offer_rules", "cpa_threshold"): ("numeric", "numeric"),
    ("offer_rules", "frequency_threshold"): ("numeric", "numeric"),
    ("offer_rules", "stop_percent_of_rule"): ("numeric", "numeric"),
    ("offer_rules", "warning_percent_of_stop"): ("numeric", "numeric"),
    ("observer_config", "interval_seconds"): ("integer", "int4"),
    ("observer_config", "owner_campaign_tag"): ("character varying", "varchar"),
    ("observer_config", "campaign_ids"): ("ARRAY", "_text"),
    ("telegram_recipients", "id"): ("uuid", "uuid"),
    ("telegram_recipients", "chat_id"): ("bigint", "int8"),
    ("telegram_recipients", "telegram_user_id"): ("bigint", "int8"),
    ("telegram_recipients", "username"): ("character varying", "varchar"),
    ("telegram_recipients", "display_name"): ("character varying", "varchar"),
    ("telegram_recipients", "role"): ("character varying", "varchar"),
    ("telegram_recipients", "revoked_at"): ("timestamp with time zone", "timestamptz"),
    ("system_config", "key"): ("character varying", "varchar"),
    ("system_config", "value"): ("jsonb", "jsonb"),
}
_PREFERENCE_EXPORTED_TYPES = {
    ("telegram_recipient_preferences", "recipient_id"): ("uuid", "uuid"),
    ("telegram_recipient_preferences", "timezone"): ("character varying", "varchar"),
    ("telegram_recipient_preferences", "min_severity"): (
        "character varying",
        "varchar",
    ),
    ("telegram_recipient_preferences", "quiet_hours_start"): (
        "time without time zone",
        "time",
    ),
    ("telegram_recipient_preferences", "quiet_hours_end"): (
        "time without time zone",
        "time",
    ),
    ("telegram_recipient_preferences", "digest_local_time"): (
        "time without time zone",
        "time",
    ),
    ("telegram_recipient_preferences", "categories"): ("jsonb", "jsonb"),
    ("telegram_recipient_preferences", "is_enabled"): ("boolean", "bool"),
}
_OPERATOR_DISPLAY_PREFERENCE_EXPORTED_TYPES = {
    ("operator_display_preferences", "owner_recipient_id"): ("uuid", "uuid"),
    ("operator_display_preferences", "timezone_name"): (
        "character varying",
        "varchar",
    ),
}


SOURCE_PROFILES: dict[str, LegacySourceProfile] = {
    LEGACY_0036_PROFILE: LegacySourceProfile(
        name=LEGACY_0036_PROFILE,
        revision="0036_observer_30s_default",
        table_columns={
            "offers": _OFFER_COLUMNS,
            "offer_rules": _LEGACY_RULE_COLUMNS,
            "observer_config": _LEGACY_OBSERVER_COLUMNS,
            "telegram_recipients": _RECIPIENT_COLUMNS,
            "system_config": _SYSTEM_COLUMNS,
        },
        exported_column_types=dict(_COMMON_EXPORTED_TYPES),
        has_recipient_preferences=False,
        has_operator_display_preferences=False,
        offer_rule_currency_column=False,
    ),
    LEGACY_BASELINE_PREFERENCES_PROFILE: LegacySourceProfile(
        name=LEGACY_BASELINE_PREFERENCES_PROFILE,
        revision="0001_safety_first_baseline",
        table_columns={
            "offers": _OFFER_COLUMNS,
            "offer_rules": _BASELINE_RULE_COLUMNS,
            "observer_config": _BASELINE_OBSERVER_COLUMNS,
            "telegram_recipients": _RECIPIENT_COLUMNS,
            "telegram_recipient_preferences": _PREFERENCE_COLUMNS,
            "system_config": _SYSTEM_COLUMNS,
        },
        exported_column_types={
            **_COMMON_EXPORTED_TYPES,
            **_PREFERENCE_EXPORTED_TYPES,
            ("offer_rules", "currency"): ("character varying", "varchar"),
        },
        has_recipient_preferences=True,
        has_operator_display_preferences=False,
        offer_rule_currency_column=True,
    ),
    LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE: LegacySourceProfile(
        name=LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE,
        revision="0001_safety_first_baseline",
        table_columns={
            "offers": _OFFER_COLUMNS,
            "offer_rules": _BASELINE_RULE_COLUMNS,
            "observer_config": _BASELINE_OBSERVER_COLUMNS,
            "telegram_recipients": _RECIPIENT_COLUMNS,
            "telegram_recipient_preferences": _PREFERENCE_COLUMNS,
            "operator_display_preferences": _OPERATOR_DISPLAY_PREFERENCE_COLUMNS,
            "system_config": _SYSTEM_COLUMNS,
        },
        exported_column_types={
            **_COMMON_EXPORTED_TYPES,
            **_PREFERENCE_EXPORTED_TYPES,
            **_OPERATOR_DISPLAY_PREFERENCE_EXPORTED_TYPES,
            ("offer_rules", "currency"): ("character varying", "varchar"),
        },
        has_recipient_preferences=True,
        has_operator_display_preferences=True,
        offer_rule_currency_column=True,
    ),
}

SOURCE_REVISION_SQL = text(
    """
    /* adoption:source-revision */
    SELECT version_num
    FROM public.alembic_version
    ORDER BY version_num
    """
)
SOURCE_PROFILE_COLUMNS_SQL = text(
    """
    /* adoption:source-profile-columns */
    SELECT columns.table_name,
           columns.column_name,
           columns.data_type,
           columns.udt_name,
           relation.relkind::text AS relation_kind
    FROM information_schema.columns AS columns
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.nspname = columns.table_schema
    JOIN pg_catalog.pg_class AS relation
      ON relation.relnamespace = namespace.oid
     AND relation.relname = columns.table_name
    WHERE columns.table_schema = 'public'
      AND columns.table_name IN :table_names
    ORDER BY columns.table_name, columns.ordinal_position
    """
).bindparams(bindparam("table_names", expanding=True))
SOURCE_FORBIDDEN_NORMALIZED_SQL = text(
    """
    /* adoption:source-forbidden-normalized */
    SELECT pg_catalog.to_regclass('public.ad_accounts') IS NOT NULL
        OR pg_catalog.to_regclass('public.offer_ad_accounts') IS NOT NULL
    """
)


def get_source_profile(name: str) -> LegacySourceProfile:
    try:
        return SOURCE_PROFILES[name]
    except KeyError as exc:
        raise AdoptionSourceProfileError("unknown explicit adoption source profile") from exc


async def assert_exact_source_profile(
    conn: AsyncConnection,
    profile: LegacySourceProfile,
) -> None:
    """Fail closed unless the selected legacy schema profile matches exactly."""

    try:
        revisions = list((await conn.scalars(SOURCE_REVISION_SQL)).all())
        if revisions != [profile.revision]:
            raise AdoptionSourceProfileError("source revision does not match selected profile")

        inspected_names = tuple(
            sorted(
                set(profile.table_columns)
                | {
                    "operator_display_preferences",
                    "telegram_recipient_preferences",
                }
            )
        )
        rows = (
            await conn.execute(
                SOURCE_PROFILE_COLUMNS_SQL,
                {"table_names": inspected_names},
            )
        ).mappings()
        actual_columns: dict[str, set[str]] = {}
        type_by_column: dict[tuple[str, str], tuple[str, str]] = {}
        relation_kinds: dict[str, set[str]] = {}
        for row in rows:
            table_name = str(row["table_name"])
            column_name = str(row["column_name"])
            actual_columns.setdefault(table_name, set()).add(column_name)
            type_by_column[(table_name, column_name)] = (
                str(row["data_type"]),
                str(row["udt_name"]),
            )
            relation_kinds.setdefault(table_name, set()).add(str(row["relation_kind"]))

        preference_present = "telegram_recipient_preferences" in actual_columns
        if preference_present is not profile.has_recipient_preferences:
            raise AdoptionSourceProfileError(
                "source preference table does not match selected profile"
            )
        operator_display_present = "operator_display_preferences" in actual_columns
        if operator_display_present is not profile.has_operator_display_preferences:
            raise AdoptionSourceProfileError(
                "source operator display preference table does not match selected profile"
            )
        if {
            table_name: frozenset(columns)
            for table_name, columns in actual_columns.items()
            if table_name in profile.table_columns
        } != profile.table_columns:
            raise AdoptionSourceProfileError("source columns do not match selected profile")
        if any(kinds - {"r", "p"} for kinds in relation_kinds.values()):
            raise AdoptionSourceProfileError("source profile relations must be tables")
        if any(
            type_by_column.get(column) != expected
            for column, expected in profile.exported_column_types.items()
        ):
            raise AdoptionSourceProfileError("source exported column types do not match profile")
        if await conn.scalar(SOURCE_FORBIDDEN_NORMALIZED_SQL):
            raise AdoptionSourceProfileError("normalized source is forbidden for legacy export")
    except AdoptionSourceProfileError:
        raise
    except Exception as exc:
        raise AdoptionSourceProfileError("source schema preflight failed") from exc


__all__ = [
    "AdoptionSourceProfileError",
    "LEGACY_0036_PROFILE",
    "LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE",
    "LEGACY_BASELINE_PREFERENCES_PROFILE",
    "LegacySourceProfile",
    "SOURCE_PROFILES",
    "assert_exact_source_profile",
    "get_source_profile",
]
