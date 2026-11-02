"""Фоновые задачи: старт-проверки, автоповтор выдачи, напоминания, суточное обновление каталога."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from . import texts as T
from .config import DAYS_PER_MONTH, DEVICES, MONTHS
from .context import App
from .partner.client import PartnerError
from .utils import fmt_date, parse_iso, utcnow

log = logging.getLogger("jobs")


async def startup_checks(app: App) -> list[str]:
    """Возвращает предупреждения; они же уходят админам."""
    warnings: list[str] = []
    try:
        me = await app.partner.me()
        app.partner.set_bases(me.get("api_base_urls", []))
        if me.get("mode") != "live":
            warnings.append("Ключ Partner API тестовый (mode=test): клиенты уходят в песочницу.")
        await app.plans.refresh()
        if app.plans.trial_project:
            warnings.append("Проект на пробных условиях: продаётся только «Тест интеграции» (до 14 суток, 3 клиента). "
                            "Для боевого режима пополните Кошелёк от 1 000 ₽.")
    except PartnerError as e:
        warnings.append(f"Partner API недоступен при старте: {e.code or e.status}")
    warnings += await price_check(app)
    unfilled = app.settings.unfilled_links()
    if unfilled:
        warnings.append("Не заполнены ссылки в .env: " + ", ".join(unfilled))
    if not app.settings.partner_webhook_secret:
        warnings.append("PARTNER_WEBHOOK_SECRET пуст: вебхуки Partner API будут отклоняться (python -m app.scripts.set_webhook).")
    if not app.cryptopay.enabled:
        warnings.append("CRYPTOPAY_TOKEN не задан: оплата USDT недоступна.")
    for w in warnings:
        log.warning(w)
    return warnings


async def price_check(app: App) -> list[str]:
    """Рублёвая цена не должна быть ниже себестоимости из /quotes."""
    if not app.plans.grid:
        return []
    out: list[str] = []
    try:
        for m in MONTHS:
            for d in DEVICES:
                q = await app.partner.quote(app.plans.plan_for(d)["id"], m * DAYS_PER_MONTH)
                cost = int(q["amount_minor"])
                price = int(app.settings.price("RUB", m, d)) * 100
                if price < cost:
                    out.append(f"Цена {m} мес × {d} устр. ({price / 100:.0f} ₽) ниже себестоимости {cost / 100:.2f} ₽.")
    except PartnerError as e:
        log.warning("проверка цен не выполнена: %s", e)
    return out


async def retry_loop(app: App, interval: int = 60) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await app.orders.retry_due()
        except Exception:  # noqa: BLE001
            log.exception("retry_due")


async def reminders_once(app: App) -> int:
    now = utcnow()
    lo, hi = app.settings.reminder_hours_utc
    if not lo <= now.hour <= hi:
        return 0
    ts = int(now.timestamp())
    params = {"status": "active", "expires_after": ts, "limit": 200,
              "expires_before": ts + app.settings.reminder_days * 86400}
    sent, cursor = 0, ""
    while True:
        data = await app.partner.users(**params, **({"cursor": cursor} if cursor else {}))
        for u in data.get("users", []):
            ext = u.get("external_id", "")
            if not ext.startswith("tg-") or not u.get("expires_at"):
                continue
            tg_id = int(ext[3:])
            if not await app.db.has_paid(tg_id):  # триал живёт 72 часа – напоминать нечего
                continue
            if await app.db.remember_reminder(tg_id, u["expires_at"]):
                await app.notifier.reminder(tg_id, fmt_date(parse_iso(u["expires_at"])))
                sent += 1
        cursor = data.get("next_cursor") or ""
        if not cursor:
            return sent


async def reminders_loop(app: App, interval: int = 3600) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await reminders_once(app)
        except Exception:  # noqa: BLE001
            log.exception("reminders")
        await asyncio.sleep(interval)


async def daily_loop(app: App) -> None:
    """Раз в сутки: каталог ступеней, запасные адреса API, проверка цен."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            me = await app.partner.me()
            app.partner.set_bases(me.get("api_base_urls", []))
            await app.plans.refresh()
            warnings = await price_check(app)
            if warnings:
                await app.notifier.admins("⚠️ " + "\n".join(warnings))
        except Exception:  # noqa: BLE001
            log.exception("daily")
