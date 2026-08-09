"""Database projections and writes for the safety-first adoption bundle."""

from __future__ import annotations

import json
import uuid
from datetime import time
from decimal import Decimal
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.cleanup_worker.retention import get_default_policy
from core.ad_account_catalog import ad_account_catalog
from core.adoption.bundle import (
    AdoptionAccountV1,
    AdoptionObserverSettingsV1,
    AdoptionOfferRuleV1,
    AdoptionOfferV1,
    AdoptionOperatorDisplaySettingsV1,
    AdoptionRecipientPreferenceV1,
    AdoptionRecipientV1,
    AdoptionSectionsV1,
    AdoptionSystemSettingsV1,
)
from core.adoption.profiles import (
    AdoptionSourceProfileError,
    LegacySourceProfile,
    assert_exact_source_profile,
)
from core.meta_api.identity import require_ad_account_id
from core.models import Base
from core.models.catalog.offer import Offer
from core.models.catalog.offer_rule import OfferRule
from core.models.operator.display_preference import OperatorDisplayPreference
from core.models.settings.observer_config import ObserverConfig
from core.models.settings.system_config import SystemConfig
from core.models.telegram.notification import TelegramRecipientPreference
from core.models.telegram.recipient import TelegramRecipient
from migrations.baseline_contract import (
    BASELINE_REVISION,
    CATALOG_ARTIFACTS_SQL,
    DATABASE_EXTENSION_LAYOUT_SQL,
    PUBLIC_PARTITION_LAYOUT_SQL,
    PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL,
    assert_catalog_artifacts,
    describe_standalone_public_catalog_objects,
    validate_database_extension_layout,
    validate_public_partition_layout,
)


class AdoptionTargetPreflightError(RuntimeError):
    """The target is not the exact empty safety-first baseline."""


class AdoptionSemanticMismatchError(RuntimeError):
    """The in-transaction target projection differs from the source bundle."""


_LEGACY_OFFERS_SQL = text(
    """
    /* adoption:legacy-offers */
    SELECT code, name, vertical, pixel_id, is_active, ad_account_ids, countries
    FROM public.offers
    ORDER BY code
    """
)
_LEGACY_RULES_WITH_CURRENCY_SQL = text(
    """
    /* adoption:legacy-rules-with-currency */
    SELECT offer.code AS offer_code,
           rule.cpa_threshold,
           rule.currency,
           rule.frequency_threshold,
           rule.stop_percent_of_rule,
           rule.warning_percent_of_stop
    FROM public.offer_rules AS rule
    JOIN public.offers AS offer ON offer.id = rule.offer_id
    ORDER BY offer.code
    """
)
_LEGACY_RULES_IMPLICIT_USD_SQL = text(
    """
    /* adoption:legacy-rules-implicit-usd */
    SELECT offer.code AS offer_code,
           rule.cpa_threshold,
           rule.frequency_threshold,
           rule.stop_percent_of_rule,
           rule.warning_percent_of_stop
    FROM public.offer_rules AS rule
    JOIN public.offers AS offer ON offer.id = rule.offer_id
    ORDER BY offer.code
    """
)
_LEGACY_OBSERVER_SQL = text(
    """
    /* adoption:legacy-observer */
    SELECT interval_seconds, owner_campaign_tag, campaign_ids
    FROM public.observer_config
    ORDER BY singleton_key
    LIMIT 2
    """
)
_LEGACY_RECIPIENTS_SQL = text(
    """
    /* adoption:legacy-recipients */
    SELECT id, chat_id, telegram_user_id, username, display_name, role
    FROM public.telegram_recipients
    WHERE revoked_at IS NULL
    ORDER BY telegram_user_id
    """
)
_LEGACY_PREFERENCES_SQL = text(
    """
    /* adoption:legacy-recipient-preferences */
    SELECT recipient.telegram_user_id,
           preference.timezone,
           preference.min_severity,
           preference.quiet_hours_start,
           preference.quiet_hours_end,
           preference.digest_local_time,
           preference.categories,
           preference.is_enabled
    FROM public.telegram_recipient_preferences AS preference
    JOIN public.telegram_recipients AS recipient
      ON recipient.id = preference.recipient_id
    WHERE recipient.revoked_at IS NULL
    ORDER BY recipient.telegram_user_id
    """
)
_LEGACY_OPERATOR_DISPLAY_SETTINGS_SQL = text(
    """
    /* adoption:legacy-operator-display-settings */
    SELECT preference.timezone_name
    FROM public.operator_display_preferences AS preference
    JOIN public.telegram_recipients AS recipient
      ON recipient.id = preference.owner_recipient_id
    WHERE recipient.role = 'owner'
      AND recipient.revoked_at IS NULL
    ORDER BY recipient.telegram_user_id
    LIMIT 2
    """
)
_ALLOWLISTED_SYSTEM_SQL = text(
    """
    /* adoption:allowlisted-system-settings */
    SELECT key, value
    FROM public.system_config
    WHERE key IN ('retention_policy', 'web_app_url')
    ORDER BY key
    """
)
_TARGET_REVISION_SQL = text(
    """
    /* adoption:target-revision */
    SELECT version_num
    FROM public.alembic_version
    ORDER BY version_num
    """
)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _decimal_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _time_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value.isoformat()
    return str(value)


def _system_settings(rows: list[dict[str, Any]]) -> AdoptionSystemSettingsV1:
    settings = {str(row["key"]): _json_value(row["value"]) for row in rows}
    retention = get_default_policy()
    source_retention = settings.get("retention_policy")
    if isinstance(source_retention, dict):
        retention.update(
            {key: str(source_retention[key]) for key in retention if key in source_retention}
        )
    web_app = settings.get("web_app_url")
    web_app_url = web_app.get("url") if isinstance(web_app, dict) else None
    return AdoptionSystemSettingsV1(
        retention_policy=retention,
        web_app_url=web_app_url,
    )


class LegacyArraySourceRepository:
    """Migration-only projection for one explicitly selected ARRAY source profile."""

    def __init__(self, conn: AsyncConnection, profile: LegacySourceProfile) -> None:
        self._conn = conn
        self._profile = profile

    async def preflight(self) -> None:
        await assert_exact_source_profile(self._conn, self._profile)

    async def project(self) -> AdoptionSectionsV1:
        offer_rows = (await self._conn.execute(_LEGACY_OFFERS_SQL)).mappings().all()
        offers: list[AdoptionOfferV1] = []
        account_ids: set[str] = set()
        for row in offer_rows:
            linked_accounts = sorted(
                {require_ad_account_id(value) for value in list(row["ad_account_ids"] or [])}
            )
            account_ids.update(linked_accounts)
            offers.append(
                AdoptionOfferV1(
                    code=str(row["code"]),
                    name=str(row["name"]),
                    vertical=row["vertical"],
                    pixel_id=row["pixel_id"],
                    is_active=bool(row["is_active"]),
                    account_ids=linked_accounts,
                    countries=sorted({str(value).upper() for value in row["countries"] or []}),
                )
            )

        rules_sql = (
            _LEGACY_RULES_WITH_CURRENCY_SQL
            if self._profile.offer_rule_currency_column
            else _LEGACY_RULES_IMPLICIT_USD_SQL
        )
        rule_rows = (await self._conn.execute(rules_sql)).mappings().all()
        rules = [
            AdoptionOfferRuleV1(
                offer_code=str(row["offer_code"]),
                cpa_threshold=_decimal_or_none(row["cpa_threshold"]),
                currency=(
                    row["currency"]
                    if self._profile.offer_rule_currency_column
                    else ("USD" if row["cpa_threshold"] is not None else None)
                ),
                frequency_threshold=_decimal_or_none(row["frequency_threshold"]),
                stop_percent_of_rule=str(row["stop_percent_of_rule"]),
                warning_percent_of_stop=str(row["warning_percent_of_stop"]),
            )
            for row in rule_rows
        ]

        observer_rows = (await self._conn.execute(_LEGACY_OBSERVER_SQL)).mappings().all()
        if len(observer_rows) > 1:
            raise AdoptionSourceProfileError("legacy observer singleton data is invalid")
        observer = None
        if observer_rows:
            row = observer_rows[0]
            observer = AdoptionObserverSettingsV1(
                interval_seconds=int(row["interval_seconds"]),
                owner_campaign_tag=row["owner_campaign_tag"],
                campaign_ids=sorted(
                    {
                        require_ad_account_id(value, field_name="campaign_id")
                        for value in row["campaign_ids"] or []
                    }
                ),
            )

        recipient_rows = (await self._conn.execute(_LEGACY_RECIPIENTS_SQL)).mappings().all()
        recipients = [
            AdoptionRecipientV1(
                chat_id=int(row["chat_id"]),
                telegram_user_id=int(row["telegram_user_id"]),
                username=row["username"],
                display_name=row["display_name"],
                role=row["role"],
            )
            for row in recipient_rows
        ]

        preferences: list[AdoptionRecipientPreferenceV1] = []
        if self._profile.has_recipient_preferences:
            preference_rows = (await self._conn.execute(_LEGACY_PREFERENCES_SQL)).mappings().all()
            preferences = [
                AdoptionRecipientPreferenceV1(
                    telegram_user_id=int(row["telegram_user_id"]),
                    timezone=str(row["timezone"]),
                    min_severity=row["min_severity"],
                    quiet_hours_start=_time_or_none(row["quiet_hours_start"]),
                    quiet_hours_end=_time_or_none(row["quiet_hours_end"]),
                    digest_local_time=_time_or_none(row["digest_local_time"]),
                    categories=dict(_json_value(row["categories"]) or {}),
                    is_enabled=bool(row["is_enabled"]),
                )
                for row in preference_rows
            ]

        operator_display_settings = None
        if self._profile.has_operator_display_preferences:
            display_rows = (
                (await self._conn.execute(_LEGACY_OPERATOR_DISPLAY_SETTINGS_SQL)).mappings().all()
            )
            if len(display_rows) != 1:
                raise AdoptionSourceProfileError(
                    "legacy operator display preference must contain exactly one active owner row"
                )
            operator_display_settings = AdoptionOperatorDisplaySettingsV1(
                timezone_name=str(display_rows[0]["timezone_name"]),
            )

        system_rows = (await self._conn.execute(_ALLOWLISTED_SYSTEM_SQL)).mappings().all()
        return AdoptionSectionsV1(
            accounts=[AdoptionAccountV1(account_id=value) for value in sorted(account_ids)],
            offers=offers,
            offer_rules=rules,
            observer_settings=observer,
            operator_display_settings=operator_display_settings,
            recipients=recipients,
            recipient_preferences=preferences,
            system_settings=_system_settings(list(system_rows)),
        )


def _fresh_data_sql() -> Any:
    table_names = sorted(name for name in Base.metadata.tables if name != "system_config")
    statements = [
        f"SELECT '{name}' AS table_name WHERE EXISTS (SELECT 1 FROM public.\"{name}\" LIMIT 1)"
        for name in table_names
    ]
    return text("/* adoption:target-fresh-data */\n" + "\nUNION ALL\n".join(statements))


_TARGET_FRESH_DATA_SQL = _fresh_data_sql()


class NormalizedTargetRepository:
    """Importer and semantic projector for the exact normalized baseline."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def preflight_fresh(self) -> None:
        """Require exact baseline schema and no adopted/runtime data."""

        try:
            revisions = list((await self._conn.scalars(_TARGET_REVISION_SQL)).all())
            if revisions != [BASELINE_REVISION]:
                raise AdoptionTargetPreflightError("target baseline revision mismatch")

            assert_catalog_artifacts(
                (await self._conn.execute(text(CATALOG_ARTIFACTS_SQL))).mappings()
            )
            validate_database_extension_layout(
                (await self._conn.execute(text(DATABASE_EXTENSION_LAYOUT_SQL))).mappings(),
                baseline_installed=True,
            )
            standalone = describe_standalone_public_catalog_objects(
                (await self._conn.execute(text(PUBLIC_STANDALONE_CATALOG_OBJECTS_SQL))).mappings(),
                allow_manifested_routines=True,
            )
            if standalone:
                raise AdoptionTargetPreflightError("target contains unreviewed catalog objects")
            partition_names = validate_public_partition_layout(
                (await self._conn.execute(text(PUBLIC_PARTITION_LAYOUT_SQL))).mappings(),
                require_baseline_defaults=True,
            )

            def schema_diffs(sync_conn) -> list[Any]:
                def include_name(name, type_, _parent_names):
                    return not (type_ == "table" and name in partition_names)

                context = MigrationContext.configure(
                    sync_conn,
                    opts={
                        "include_name": include_name,
                        "compare_server_default": True,
                    },
                )
                return list(compare_metadata(context, Base.metadata))

            if await self._conn.run_sync(schema_diffs):
                raise AdoptionTargetPreflightError("target ORM schema drift detected")

            dirty_tables = list((await self._conn.scalars(_TARGET_FRESH_DATA_SQL)).all())
            if dirty_tables:
                raise AdoptionTargetPreflightError("target contains application data")
            system_rows = (
                (
                    await self._conn.execute(
                        text(
                            """
                        /* adoption:target-fresh-system-config */
                        SELECT key, value
                        FROM public.system_config
                        ORDER BY key
                        """
                        )
                    )
                )
                .mappings()
                .all()
            )
            if len(system_rows) != 1:
                raise AdoptionTargetPreflightError("target system configuration is not fresh")
            row = system_rows[0]
            if (
                row["key"] != "retention_policy"
                or _json_value(row["value"]) != get_default_policy()
            ):
                raise AdoptionTargetPreflightError("target baseline seed is not pristine")
        except AdoptionTargetPreflightError:
            raise
        except Exception as exc:
            raise AdoptionTargetPreflightError("target preflight failed") from exc

    async def import_sections(self, sections: AdoptionSectionsV1) -> None:
        """Insert only allowlisted configuration in FK-safe order."""

        if sections.system_settings is None:
            raise AdoptionTargetPreflightError("bundle system settings are required for import")
        if sections.system_settings.retention_policy is None:
            raise AdoptionTargetPreflightError("bundle retention policy is required for import")

        await ad_account_catalog.create_accounts(
            self._conn,
            (account.account_id for account in sections.accounts),
        )

        offer_ids: dict[str, uuid.UUID] = {}
        for offer in sections.offers:
            offer_id = uuid.uuid4()
            offer_ids[offer.code] = offer_id
            await self._conn.execute(
                Offer.__table__.insert().values(
                    id=offer_id,
                    code=offer.code,
                    name=offer.name,
                    vertical=offer.vertical,
                    pixel_id=offer.pixel_id,
                    is_active=offer.is_active,
                    countries=offer.countries,
                )
            )
            await ad_account_catalog.replace_offer_accounts(
                self._conn,
                offer_id=offer_id,
                account_ids=offer.account_ids,
            )

        for rule in sections.offer_rules:
            await self._conn.execute(
                OfferRule.__table__.insert().values(
                    id=uuid.uuid4(),
                    offer_id=offer_ids[rule.offer_code],
                    cpa_threshold=(
                        Decimal(rule.cpa_threshold) if rule.cpa_threshold is not None else None
                    ),
                    currency=rule.currency,
                    frequency_threshold=(
                        Decimal(rule.frequency_threshold)
                        if rule.frequency_threshold is not None
                        else None
                    ),
                    stop_percent_of_rule=Decimal(rule.stop_percent_of_rule),
                    warning_percent_of_stop=Decimal(rule.warning_percent_of_stop),
                )
            )

        if sections.observer_settings is not None:
            observer = sections.observer_settings
            await self._conn.execute(
                ObserverConfig.__table__.insert().values(
                    id=uuid.uuid4(),
                    singleton_key="default",
                    interval_seconds=observer.interval_seconds,
                    is_scanning_enabled=False,
                    owner_campaign_tag=observer.owner_campaign_tag,
                    campaign_ids=observer.campaign_ids,
                )
            )

        recipient_ids: dict[int, uuid.UUID] = {}
        for recipient in sections.recipients:
            recipient_id = uuid.uuid4()
            recipient_ids[recipient.telegram_user_id] = recipient_id
            await self._conn.execute(
                TelegramRecipient.__table__.insert().values(
                    id=recipient_id,
                    chat_id=recipient.chat_id,
                    telegram_user_id=recipient.telegram_user_id,
                    username=recipient.username,
                    display_name=recipient.display_name,
                    role=recipient.role,
                    invite_id=None,
                    revoked_at=None,
                )
            )

        if sections.operator_display_settings is not None:
            owner = next(
                recipient for recipient in sections.recipients if recipient.role == "owner"
            )
            await self._conn.execute(
                OperatorDisplayPreference.__table__.insert().values(
                    owner_recipient_id=recipient_ids[owner.telegram_user_id],
                    timezone_name=sections.operator_display_settings.timezone_name,
                )
            )

        for preference in sections.recipient_preferences:
            await self._conn.execute(
                TelegramRecipientPreference.__table__.insert().values(
                    recipient_id=recipient_ids[preference.telegram_user_id],
                    timezone=preference.timezone,
                    min_severity=preference.min_severity,
                    quiet_hours_start=(
                        time.fromisoformat(preference.quiet_hours_start)
                        if preference.quiet_hours_start is not None
                        else None
                    ),
                    quiet_hours_end=(
                        time.fromisoformat(preference.quiet_hours_end)
                        if preference.quiet_hours_end is not None
                        else None
                    ),
                    digest_local_time=(
                        time.fromisoformat(preference.digest_local_time)
                        if preference.digest_local_time is not None
                        else None
                    ),
                    categories=preference.categories,
                    is_enabled=preference.is_enabled,
                )
            )

        settings = sections.system_settings
        await self._upsert_system_config(
            "retention_policy",
            settings.retention_policy,
            "Retention policy adopted from reviewed bundle",
        )
        await self._upsert_system_config(
            "web_app_url",
            {"url": settings.web_app_url},
            "Web App URL adopted from reviewed bundle",
        )

    async def _upsert_system_config(self, key: str, value: Any, description: str) -> None:
        stmt = (
            pg_insert(SystemConfig)
            .values(
                id=uuid.uuid4(),
                key=key,
                value=value,
                description=description,
            )
            .on_conflict_do_update(
                index_elements=[SystemConfig.key],
                set_={
                    "value": value,
                    "description": description,
                    "updated_at": func.now(),
                },
            )
        )
        await self._conn.execute(stmt)

    async def project(self) -> AdoptionSectionsV1:
        account_ids = await ad_account_catalog.list_accounts(self._conn)
        offer_rows = (
            (await self._conn.execute(select(Offer.__table__).order_by(Offer.code)))
            .mappings()
            .all()
        )
        accounts_by_offer = await ad_account_catalog.list_by_offer(
            self._conn,
            offer_ids=(row["id"] for row in offer_rows),
        )
        offers = [
            AdoptionOfferV1(
                code=row["code"],
                name=row["name"],
                vertical=row["vertical"],
                pixel_id=row["pixel_id"],
                is_active=row["is_active"],
                account_ids=accounts_by_offer.get(row["id"], []),
                countries=sorted(row["countries"] or []),
            )
            for row in offer_rows
        ]

        rule_rows = (
            (
                await self._conn.execute(
                    select(
                        Offer.code.label("offer_code"),
                        OfferRule.cpa_threshold,
                        OfferRule.currency,
                        OfferRule.frequency_threshold,
                        OfferRule.stop_percent_of_rule,
                        OfferRule.warning_percent_of_stop,
                    )
                    .join(OfferRule, OfferRule.offer_id == Offer.id)
                    .order_by(Offer.code)
                )
            )
            .mappings()
            .all()
        )
        rules = [
            AdoptionOfferRuleV1(
                offer_code=row["offer_code"],
                cpa_threshold=_decimal_or_none(row["cpa_threshold"]),
                currency=row["currency"],
                frequency_threshold=_decimal_or_none(row["frequency_threshold"]),
                stop_percent_of_rule=str(row["stop_percent_of_rule"]),
                warning_percent_of_stop=str(row["warning_percent_of_stop"]),
            )
            for row in rule_rows
        ]

        observer_rows = (
            (
                await self._conn.execute(
                    select(
                        ObserverConfig.interval_seconds,
                        ObserverConfig.owner_campaign_tag,
                        ObserverConfig.campaign_ids,
                    ).order_by(ObserverConfig.singleton_key)
                )
            )
            .mappings()
            .all()
        )
        if len(observer_rows) > 1:
            raise AdoptionSemanticMismatchError("target observer singleton is not unique")
        observer = None
        if observer_rows:
            row = observer_rows[0]
            observer = AdoptionObserverSettingsV1(
                interval_seconds=row["interval_seconds"],
                owner_campaign_tag=row["owner_campaign_tag"],
                campaign_ids=sorted(row["campaign_ids"] or []),
            )

        recipient_rows = (
            (
                await self._conn.execute(
                    select(
                        TelegramRecipient.id,
                        TelegramRecipient.chat_id,
                        TelegramRecipient.telegram_user_id,
                        TelegramRecipient.username,
                        TelegramRecipient.display_name,
                        TelegramRecipient.role,
                    )
                    .where(TelegramRecipient.revoked_at.is_(None))
                    .order_by(TelegramRecipient.telegram_user_id)
                )
            )
            .mappings()
            .all()
        )
        recipients = [
            AdoptionRecipientV1(
                chat_id=row["chat_id"],
                telegram_user_id=row["telegram_user_id"],
                username=row["username"],
                display_name=row["display_name"],
                role=row["role"],
            )
            for row in recipient_rows
        ]
        preference_rows = (
            (
                await self._conn.execute(
                    select(
                        TelegramRecipient.telegram_user_id,
                        TelegramRecipientPreference.timezone,
                        TelegramRecipientPreference.min_severity,
                        TelegramRecipientPreference.quiet_hours_start,
                        TelegramRecipientPreference.quiet_hours_end,
                        TelegramRecipientPreference.digest_local_time,
                        TelegramRecipientPreference.categories,
                        TelegramRecipientPreference.is_enabled,
                    )
                    .select_from(TelegramRecipientPreference)
                    .join(
                        TelegramRecipient,
                        TelegramRecipient.id == TelegramRecipientPreference.recipient_id,
                    )
                    .where(TelegramRecipient.revoked_at.is_(None))
                    .order_by(TelegramRecipient.telegram_user_id)
                )
            )
            .mappings()
            .all()
        )
        preferences = [
            AdoptionRecipientPreferenceV1(
                telegram_user_id=row["telegram_user_id"],
                timezone=row["timezone"],
                min_severity=row["min_severity"],
                quiet_hours_start=_time_or_none(row["quiet_hours_start"]),
                quiet_hours_end=_time_or_none(row["quiet_hours_end"]),
                digest_local_time=_time_or_none(row["digest_local_time"]),
                categories=dict(row["categories"] or {}),
                is_enabled=row["is_enabled"],
            )
            for row in preference_rows
        ]
        display_rows = (
            (
                await self._conn.execute(
                    select(OperatorDisplayPreference.timezone_name)
                    .join(
                        TelegramRecipient,
                        TelegramRecipient.id == OperatorDisplayPreference.owner_recipient_id,
                    )
                    .where(
                        TelegramRecipient.role == "owner",
                        TelegramRecipient.revoked_at.is_(None),
                    )
                    .order_by(TelegramRecipient.telegram_user_id)
                )
            )
            .mappings()
            .all()
        )
        if len(display_rows) > 1:
            raise AdoptionSemanticMismatchError("target operator display preference is not unique")
        operator_display_settings = (
            AdoptionOperatorDisplaySettingsV1(timezone_name=display_rows[0]["timezone_name"])
            if display_rows
            else None
        )
        system_rows = (await self._conn.execute(_ALLOWLISTED_SYSTEM_SQL)).mappings().all()
        return AdoptionSectionsV1(
            accounts=[AdoptionAccountV1(account_id=value) for value in account_ids],
            offers=offers,
            offer_rules=rules,
            observer_settings=observer,
            operator_display_settings=operator_display_settings,
            recipients=recipients,
            recipient_preferences=preferences,
            system_settings=_system_settings(list(system_rows)),
        )


__all__ = [
    "AdoptionSemanticMismatchError",
    "AdoptionTargetPreflightError",
    "LegacyArraySourceRepository",
    "NormalizedTargetRepository",
]
