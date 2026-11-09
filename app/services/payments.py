"""Создание счетов: не больше одного открытого счёта на пользователя и способ оплаты."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from ..db import PaymentRow
from ..utils import utcnow

if TYPE_CHECKING:
    from ..context import App

STARS_LIFETIME = timedelta(hours=24)
CRYPTO_LIFETIME = timedelta(minutes=60)
PLATEGA_LIFETIME = timedelta(minutes=15)


async def _close_old(app: "App", tg_id: int, provider: str, keep_same: tuple | None) -> PaymentRow | None:
    lifetime = {"cryptopay": CRYPTO_LIFETIME, "platega": PLATEGA_LIFETIME}.get(provider, STARS_LIFETIME)
    reuse = None
    for p in await app.db.open_payments(tg_id, provider):
        fresh = utcnow() - p.created_at < lifetime
        if keep_same and fresh and reuse is None and (p.kind, p.months, p.devices, p.amount) == keep_same:
            reuse = p
            continue
        await app.db.transition(p.id, ("created",), "expired")
        if provider == "cryptopay" and p.provider_ref:
            await app.cryptopay.delete_invoice(p.provider_ref)
    return reuse


async def stars_payment(app: "App", tg_id: int, kind: str, months: int, devices: int, amount: Decimal) -> PaymentRow:
    reuse = await _close_old(app, tg_id, "stars", (kind, months, devices, amount))
    return reuse or await app.orders.new_payment(tg_id=tg_id, provider="stars", kind=kind, months=months,
                                                 devices=devices, amount=amount, currency="STARS")


async def crypto_payment(app: "App", tg_id: int, kind: str, months: int, devices: int, amount: Decimal,
                         description: str) -> PaymentRow:
    reuse = await _close_old(app, tg_id, "cryptopay", (kind, months, devices, amount))
    if reuse:
        return reuse
    # Сначала счёт у провайдера, затем запись; payload счёта = id нашего платежа.
    import uuid
    from ..db import PaymentRow as Row
    pid = str(uuid.uuid4())
    invoice_id, url = await app.cryptopay.create_invoice(amount, description, pid)
    return await app.db.add_payment(Row(
        id=pid, tg_id=tg_id, provider="cryptopay", provider_ref=invoice_id, kind=kind, months=months,
        devices=devices, amount=amount, currency="USDT", status="created", invoice_url=url))


async def platega_payment(app: "App", tg_id: int, kind: str, months: int, devices: int, amount: Decimal,
                          description: str) -> PaymentRow:
    reuse = await _close_old(app, tg_id, "platega", (kind, months, devices, amount))
    if reuse:
        return reuse
    import uuid
    from ..db import PaymentRow as Row
    me = await app.bot.me()
    username = ""
    try:  # Platega просит username покупателя; если Telegram его не отдал – передаём пустым
        username = (await app.bot.get_chat(tg_id)).username or ""
    except Exception:  # noqa: BLE001
        pass
    metadata = {"tg_bot_id": str(me.id), "tg_bot_username": f"@{me.username}", "tg_id": str(tg_id),
                "tg_username": f"@{username}" if username else ""}
    pid = str(uuid.uuid4())
    ref, url = await app.platega.create_invoice(amount, description, pid, metadata, f"https://t.me/{me.username}")
    return await app.db.add_payment(Row(
        id=pid, tg_id=tg_id, provider="platega", provider_ref=ref, kind=kind, months=months,
        devices=devices, amount=amount, currency="RUB", status="created", invoice_url=url))


async def check_platega(app: "App", pay: PaymentRow) -> str:
    """Сверяет платёж с Platega по API. Возвращает paid | expired | pending.
    Выдача – только при CONFIRMED и совпавшей сумме; повторный вызов безопасен."""
    from .. import texts as T
    st = await app.platega.status(pay.provider_ref or "")
    status = st["status"]
    if status == "CONFIRMED":
        # Комиссию Platega может добавить сверху: больше цены – нормально, меньше – недоплата
        if (st["amount"] is not None and st["amount"] < pay.amount) or (st["currency"] not in (None, "RUB")):
            if await app.db.remember_event(f"platega-mismatch:{pay.provider_ref}", "platega.mismatch", ""):
                await app.notifier.admins(T.A_PLATEGA_MISMATCH.format(
                    tg_id=pay.tg_id, ref=pay.provider_ref, expected=pay.amount, got=st["amount"], cur=st["currency"]))
            return "pending"
        await app.orders.process_paid(pay.id)
        return "paid"
    if status in ("CANCELED", "CHARGEBACKED"):
        await app.db.transition(pay.id, ("created",), "expired")
        return "expired"
    return "pending"
