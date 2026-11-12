"""Входящие вебхуки: Partner API и CryptoPay. Отвечаем сразу, работу делаем в фоне."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import pathlib
import time

from aiohttp import web

from .context import App
from .utils import fmt_date, parse_iso

log = logging.getLogger("webhooks")
_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if not t.cancelled() and t.exception():
            log.error("фоновая задача упала", exc_info=t.exception())

    task.add_done_callback(_done)


def verify_signature(secret: str, raw: bytes, timestamp: str, signature: str, *,
                     tolerance: int = 300, now: float | None = None) -> bool:
    """X-Webhook-Signature = hex(HMAC-SHA256(secret, timestamp + "." + сырое тело))."""
    try:
        sent = int(timestamp)
    except (TypeError, ValueError):
        return False
    if not secret or abs((now if now is not None else time.time()) - sent) > tolerance:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


async def handle_partner_event(app: App, event: dict) -> None:
    kind, data = event.get("type", ""), event.get("data", {})
    ext = data.get("external_id", "")
    if kind == "user.expired" and ext.startswith("tg-"):
        await app.notifier.expired(int(ext[3:]))
    elif kind == "user.traffic_exhausted" and ext.startswith("tg-"):
        await app.notifier.traffic(int(ext[3:]), white=bool(data.get("white_exhausted")) and not data.get("total_exhausted"))
    elif kind == "balance.low":
        from . import texts as T
        await app.notifier.admins(T.A_BALANCE_LOW.format(balance=f"{int(data.get('balance_minor', 0)) / 100:.2f}"))
    # user.enabled / user.disabled / webhook.test и незнакомые типы: просто подтверждаем


async def handle_platega_callback(app: App, data: dict) -> None:
    """Статус из тела не принимаем на веру: CONFIRMED/CANCELED перепроверяются запросом к Platega."""
    from . import texts as T
    from .providers.platega import PlategaError
    from .services.payments import check_platega

    ref, status = str(data["id"]), str(data["status"])
    pay = await app.db.payment_by_ref("platega", ref)
    if pay is None:
        log.warning("Platega: колбэк по неизвестному платежу %s (%s)", ref, status)
        return
    if status in ("CONFIRMED", "CANCELED"):
        try:
            await check_platega(app, pay)
        except PlategaError as e:
            log.warning("Platega: проверка %s не удалась, доберёт фоновая проверка: %s", ref, e)
    elif status in ("CHARGEBACKED", "PARTIALLY_REFUNDED", "DISPUTED"):
        if await app.db.remember_event(f"platega:{ref}:{status}", f"platega.{status}", ""):
            await app.notifier.admins(T.A_PLATEGA_ALERT.format(
                status=status, tg_id=pay.tg_id, amount=pay.amount, ref=ref, order=pay.status))


def build_web_app(app: App) -> web.Application:
    async def partner_webhook(request: web.Request) -> web.Response:
        raw = await request.read()  # подпись считается по сырым байтам тела
        if not verify_signature(app.settings.partner_webhook_secret, raw, request.headers.get("X-Webhook-Timestamp", ""),
                                request.headers.get("X-Webhook-Signature", "")):
            return web.Response(status=401)
        try:
            event = json.loads(raw)
            event_id = str(event["id"])
        except (ValueError, KeyError, TypeError):
            return web.Response(status=400)
        if not await app.db.remember_event(event_id, str(event.get("type", "")), str(event.get("seq", ""))):
            return web.Response(status=200)  # повтор доставки
        spawn(handle_partner_event(app, event))
        return web.Response(status=204)  # незнакомые типы тоже подтверждаем

    async def cryptopay_webhook(request: web.Request) -> web.Response:
        raw = await request.read()
        if not app.cryptopay.verify_webhook(raw, request.headers.get("crypto-pay-api-signature", "")):
            return web.Response(status=401)
        try:
            update = json.loads(raw)
        except ValueError:
            return web.Response(status=400)
        if update.get("update_type") == "invoice_paid":
            invoice = update.get("payload", {})
            pay = await app.db.payment_by_ref("cryptopay", str(invoice.get("invoice_id")))
            if pay is not None:
                spawn(app.orders.process_paid(pay.id))
        return web.Response(status=200)

    async def platega_callback(request: web.Request) -> web.Response:
        if not app.platega.verify_callback(request.headers):
            return web.Response(status=401)
        try:
            data = json.loads(await request.read())
            str(data["id"]), str(data["status"])
        except (ValueError, KeyError, TypeError):
            return web.Response(status=400)
        spawn(handle_platega_callback(app, data))
        return web.Response(status=200)  # работу делаем в фоне; потерянное доберёт проверка раз в минуту

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    web_app = web.Application()
    web_app.router.add_post("/partner-webhook", partner_webhook)
    web_app.router.add_post("/cryptopay-webhook", cryptopay_webhook)
    web_app.router.add_post("/platega-callback", platega_callback)
    web_app.router.add_get("/health", health)
    web_app.router.add_static("/static", pathlib.Path(__file__).parent / "static")  # картинки для inline-режима
    return web_app
