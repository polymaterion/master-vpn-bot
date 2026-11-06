"""Выдача оплаченного: атомарные переходы статусов, автоповтор, один заказ на один платёж."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from ..config import DAYS_PER_MONTH
from ..db import Database, PaymentRow
from ..partner.client import PartnerClient, PartnerError
from .plans import PlanCatalog

log = logging.getLogger("orders")


class Notifier(Protocol):
    async def paid_ok(self, payment: PaymentRow, user: dict) -> None: ...
    async def paid_pending(self, payment: PaymentRow, error: PartnerError) -> None: ...
    async def paid_failed(self, payment: PaymentRow, error: PartnerError) -> None: ...


class OrderService:
    def __init__(self, db: Database, partner: PartnerClient, plans: PlanCatalog, notifier: Notifier):
        self.db, self.partner, self.plans, self.notifier = db, partner, plans, notifier

    async def new_payment(self, *, tg_id: int, provider: str, kind: str, months: int, devices: int,
                          amount: Decimal, currency: str, provider_ref: str | None = None,
                          invoice_url: str | None = None) -> PaymentRow:
        return await self.db.add_payment(PaymentRow(
            id=str(uuid.uuid4()), tg_id=tg_id, provider=provider, provider_ref=provider_ref, kind=kind,
            months=months, devices=devices, amount=amount, currency=currency, status="created",
            invoice_url=invoice_url))

    async def process_paid(self, payment_id: str, provider_ref: str | None = None) -> str:
        """Деньги получены. Повторное уведомление об оплате ничего не делает."""
        p = await self.db.get_payment(payment_id)
        if p is None:
            return "unknown"
        ref = provider_ref or p.provider_ref
        if not ref:
            return "unknown"
        row = await self.db.transition(payment_id, ("created", "expired"), "paid",
                                       provider_ref=ref, idem_key=f"{p.provider}-{ref}")
        if row is None:
            return "duplicate"
        return await self.fulfil(row.id)

    async def fulfil(self, payment_id: str) -> str:
        p = await self.db.transition(payment_id, ("paid",), "fulfilling")
        if p is None:
            return "skipped"
        try:
            user = await self._deliver(p)
        except PartnerError as e:
            return await self._on_error(p, e)
        await self.db.transition(p.id, ("fulfilling",), "fulfilled", error_code=None)
        await self.db.drop_pending(p.id)
        await self.notifier.paid_ok(p, user)
        return "fulfilled"

    async def _deliver(self, p: PaymentRow) -> dict:
        try:
            return await self._place_order(p)
        except PartnerError as e:
            if e.code != "excess_devices":
                raise
        # Устройств больше, чем в новом тарифе: покупателя не задерживаем – освобождаем слоты
        # и выпускаем новую ссылку, затем повторяем заказ с тем же ключом.
        await self._reset_devices(f"tg-{p.tg_id}")
        return await self._place_order(p)

    async def _reset_devices(self, ext: str) -> None:
        log.info("%s: лишние устройства, удаляем все и перевыпускаем ссылку", ext)
        for d in (await self.partner.devices(ext)).get("devices", []):
            try:
                await self.partner.delete_device(ext, d["device_id"])
            except PartnerError as e:
                if e.status != 404:
                    raise
        await self.partner.rotate(ext)

    async def _place_order(self, p: PaymentRow) -> dict:
        ext, days, key = f"tg-{p.tg_id}", p.months * DAYS_PER_MONTH, p.idem_key or ""
        await self.plans.ensure()
        if p.kind == "renew":
            current = await self.partner.user(ext)
            if current is None:
                raise PartnerError(404, {"code": "not_found", "error": "client not found for renew"})
            plan = self.plans.plan_for(int(current["device_limit"]))
            days = self.plans.clamp_days(plan, days)
            quote = await self.partner.quote(plan["id"], days)
            return await self.partner.extend(ext, days, key, max_charge_minor=quote["amount_minor"])
        plan = self.plans.plan_for(p.devices)
        days = self.plans.clamp_days(plan, days)
        quote = await self.partner.quote(plan["id"], days)
        return await self.partner.put_user(
            ext, plan["id"], days, key, max_charge_minor=quote["amount_minor"],
            metadata={"tg_id": p.tg_id, "source": "bot", "payment": p.id})

    async def _on_error(self, p: PaymentRow, e: PartnerError) -> str:
        if e.retryable:
            attempts = await self.db.bump_pending(p.id, e.code or "network")
            await self.db.transition(p.id, ("fulfilling",), "paid", error_code=e.code, request_id=e.request_id)
            log.warning("выдача %s отложена (%s), попытка %s", p.id, e.code, attempts)
            if attempts == 1:
                await self.notifier.paid_pending(p, e)
            return "pending"
        await self.db.transition(p.id, ("fulfilling",), "failed", error_code=e.code or str(e.status),
                                 request_id=e.request_id)
        await self.db.drop_pending(p.id)
        log.error("выдача %s не удалась: %s", p.id, e)
        await self.notifier.paid_failed(p, e)
        return "failed"

    async def retry_due(self) -> int:
        await self.db.release_stuck(timedelta(minutes=5))
        n = 0
        for pid in await self.db.due_payments():
            await self.fulfil(pid)
            n += 1
        return n

    async def admin_retry(self, payment_id: str) -> str:
        row = await self.db.transition(payment_id, ("failed",), "paid", error_code=None)
        if row is None:
            return "skipped"
        return await self.fulfil(payment_id)
