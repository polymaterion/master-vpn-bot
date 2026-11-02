"""Сообщения клиентам и админам. Заблокировавшие бота пользователи пропускаются молча."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import texts as T
from .config import Settings
from .db import Database, PaymentRow
from .partner.client import PartnerError
from .utils import esc, fmt_date, fmt_money, parse_iso

log = logging.getLogger("notify")

PROVIDER_TITLES = {"stars": "Stars", "cryptopay": "USDT (CryptoPay)", "platega": "Рубли (СБП)"}


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _gr(text: str, screen: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"gr|{screen}")


class BotNotifier:
    def __init__(self, bot: Bot, settings: Settings, db: Database):
        self.bot, self.settings, self.db = bot, settings, db

    async def send(self, chat_id: int, text: str, kb: InlineKeyboardMarkup | None = None) -> bool:
        try:
            await self.bot.send_message(chat_id, text, reply_markup=kb)
            return True
        except TelegramForbiddenError:
            return False
        except TelegramAPIError as e:
            log.warning("не отправлено %s: %s", chat_id, e)
            return False

    async def admins(self, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
        for admin_id in self.settings.admin_ids:
            await self.send(admin_id, text, kb)

    @staticmethod
    def _amount(p: PaymentRow) -> str:
        return fmt_money(p.currency, p.amount)

    # --- результат оплаты ------------------------------------------------------
    async def paid_ok(self, payment: PaymentRow, user: dict) -> None:
        text = T.N_PAID_OK.format(until=fmt_date(parse_iso(user.get("expires_at"))), devices=user.get("device_limit"))
        await self.send(payment.tg_id, text, _kb([_gr("Показать ссылку", "sub_link"), _gr("QR-код", "sub_qr")]))
        await self.admins(T.A_PAID_OK.format(tg=payment.tg_id, provider=PROVIDER_TITLES.get(payment.provider, payment.provider),
                                             amount=self._amount(payment)))

    async def paid_pending(self, payment: PaymentRow, error: PartnerError) -> None:
        await self.send(payment.tg_id, T.N_PAID_PENDING)
        amount = self._amount(payment)
        if error.code == "insufficient_balance":
            await self.admins(T.A_WALLET_EMPTY.format(tg=payment.tg_id, amount=amount))
        else:
            await self.admins(T.A_PENDING.format(code=error.code or "network", tg=payment.tg_id,
                                                 provider=PROVIDER_TITLES.get(payment.provider, payment.provider), amount=amount))

    async def paid_failed(self, payment: PaymentRow, error: PartnerError) -> None:
        support = self.settings.support_url
        kb = _kb([InlineKeyboardButton(text=T.BTN_SUPPORT, url=support)]) if support else None
        await self.send(payment.tg_id, T.N_PAID_FAILED, kb)
        rows = [[InlineKeyboardButton(text="🔁 Повторить", callback_data=f"adm|retry|{payment.id}")]]
        if payment.provider == "stars":
            rows.append([InlineKeyboardButton(text="↩️ Возврат Stars", callback_data=f"adm|refund|{payment.id}")])
        await self.admins(T.A_FAILED.format(
            tg=payment.tg_id, provider=PROVIDER_TITLES.get(payment.provider, payment.provider),
            amount=self._amount(payment), code=esc(error.code or error.status), request_id=esc(error.request_id or "–"),
            detail=esc(error.detail[:300])), _kb(*rows))

    # --- события Partner API -----------------------------------------------------
    async def _renew_or_buy_kb(self, tg_id: int) -> tuple[bool, InlineKeyboardMarkup]:
        paid = await self.db.has_paid(tg_id)
        return paid, _kb([_gr(T.BTN_RENEW, "renew_term")] if paid else [_gr(T.BTN_BUY, "buy_term")])

    async def expired(self, tg_id: int) -> None:
        paid, kb = await self._renew_or_buy_kb(tg_id)
        await self.send(tg_id, T.N_EXPIRED if paid else T.N_EXPIRED_TRIAL, kb)

    async def traffic(self, tg_id: int, *, white: bool) -> None:
        _, kb = await self._renew_or_buy_kb(tg_id)
        await self.send(tg_id, T.N_TRAFFIC.format(what="белого трафика" if white else "трафика"), kb)

    async def reminder(self, tg_id: int, until: str) -> None:
        _, kb = await self._renew_or_buy_kb(tg_id)
        await self.send(tg_id, T.N_REMIND.format(until=until), kb)
