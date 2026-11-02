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


async def _close_old(app: "App", tg_id: int, provider: str, keep_same: tuple | None) -> PaymentRow | None:
    lifetime = CRYPTO_LIFETIME if provider == "cryptopay" else STARS_LIFETIME
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
