from __future__ import annotations

from apps.notifier.events import TelegramEvent, TelegramEventType


class TelegramMessageFormatter:
    """Формирует текст Telegram-сообщения по типу события."""

    def format(self, event: TelegramEvent) -> str:
        match event.event_type:
            case TelegramEventType.AD_PAUSED_BY_BOT:
                return self._format_paused(event)
            case TelegramEventType.AD_RESUMED_BY_BOT:
                return self._format_resumed(event)
            case TelegramEventType.AD_REJECTED_OR_NOT_DELIVERING:
                return self._format_rejected(event)
            case TelegramEventType.OBSERVE_WOULD_PAUSE:
                return self._format_observe(event, "Бот рекомендовал бы остановить объявление")
            case TelegramEventType.OBSERVE_WOULD_RESUME:
                return self._format_observe(
                    event, "Бот рекомендовал бы вернуть объявление в ротацию"
                )
            case TelegramEventType.WORKER_ERROR:
                return self._format_worker_error(event)
            case TelegramEventType.SCOPE_INVALID:
                return self._format_scope_invalid(event)
            case TelegramEventType.SCAN_SOURCE_UNAVAILABLE:
                return self._format_scan_source_unavailable(event)

    def _format_header(self, title: str, event: TelegramEvent) -> str:
        payload = event.payload
        return (
            f"{title}\n\n"
            f"Хост: {payload.host}\n"
            f"Аккаунт: {payload.account_name}\n"
            f"Кампания: {payload.campaign_name}\n"
            f"Адсет: {payload.adset_name}\n"
            f"Объявление: {payload.ad_name}\n"
            f"Ad ID: {payload.fb_ad_id}"
        )

    def _format_metrics(self, event: TelegramEvent) -> str:
        metrics = event.payload.metrics
        return (
            f"Spend: {metrics.get('spend', 'n/a')}\n"
            f"Clicks: {metrics.get('clicks', 'n/a')}\n"
            f"CPC: {metrics.get('cpc', 'n/a')}\n"
            f"Leads: {metrics.get('leads', 'n/a')}\n"
            f"CPL: {metrics.get('cost_per_lead', 'n/a')}\n"
            f"Regs: {metrics.get('registrations', 'n/a')}\n"
            f"CPA Reg: {metrics.get('cost_per_registration', 'n/a')}\n"
            f"Deposits: {metrics.get('deposits', 'n/a')}"
        )

    def _format_paused(self, event: TelegramEvent) -> str:
        payload = event.payload
        return (
            f"{self._format_header('⛔ Объявление выключено ботом', event)}\n\n"
            f"Статус до: {payload.delivery_before or 'n/a'}\n"
            f"Статус после: {payload.delivery_after or 'n/a'}\n"
            f"Правило: {payload.rule_id or 'n/a'}\n"
            f"Причина: {payload.reason}\n\n"
            f"{self._format_metrics(event)}"
        )

    def _format_resumed(self, event: TelegramEvent) -> str:
        payload = event.payload
        return (
            f"{self._format_header('✅ Объявление снова включено ботом', event)}\n\n"
            f"Статус до: {payload.delivery_before or 'n/a'}\n"
            f"Статус после: {payload.delivery_after or 'n/a'}\n"
            f"Причина: {payload.reason}\n\n"
            f"{self._format_metrics(event)}"
        )

    def _format_rejected(self, event: TelegramEvent) -> str:
        payload = event.payload
        return (
            f"{self._format_header('🚫 Объявление не показывается', event)}\n\n"
            f"Причина: {payload.reason}\n\n"
            f"{self._format_metrics(event)}"
        )

    def _format_observe(self, event: TelegramEvent, title: str) -> str:
        payload = event.payload
        return (
            f"{self._format_header(title, event)}\n\n"
            f"Правило: {payload.rule_id or 'n/a'}\n"
            f"Причина: {payload.reason}\n\n"
            f"{self._format_metrics(event)}"
        )

    def _format_worker_error(self, event: TelegramEvent) -> str:
        payload = event.payload
        return f"⚠️ Ошибка фонового воркера\n\nХост: {payload.host}\nПричина: {payload.reason}"

    def _format_scope_invalid(self, event: TelegramEvent) -> str:
        payload = event.payload
        return (
            "⚠️ Скан текущего scope признан невалидным\n\n"
            f"Хост: {payload.host}\n"
            f"Причина: {payload.reason}"
        )

    def _format_scan_source_unavailable(self, event: TelegramEvent) -> str:
        payload = event.payload
        profile_id = payload.extra.get("profile_id", "unknown")
        attempts = payload.extra.get("attempts", "1")
        return (
            "⚠️ Сканирование профиля остановлено\n\n"
            f"Хост: {payload.host}\n"
            f"Профиль: {profile_id}\n"
            f"Причина: {payload.reason}\n"
            f"Попыток подряд: {attempts}"
        )
