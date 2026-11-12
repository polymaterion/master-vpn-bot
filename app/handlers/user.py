from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message,
                           PreCheckoutQuery)

from .. import texts as T
from ..config import DAYS_PER_MONTH
from ..context import App
from ..partner.client import PartnerError
from ..providers.cryptopay import CryptoPayError
from ..providers.platega import PlategaError
from ..services.payments import check_platega, stars_payment
from ..ui.nav import HOME, decode
from ..ui.screens import MONTH_NAMES
from ..utils import fmt_money, parse_source

log = logging.getLogger("user")
router = Router()


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, app: App) -> None:
    u = message.from_user
    # запись пользователя и источника (?start=<метка>); источник фиксируется только при первом заходе
    await app.db.register_user(u.id, u.username, u.first_name, u.last_name, parse_source(command.args))
    await app.nav.start(u.id)


@router.callback_query(F.data == "b")
async def back(cb: CallbackQuery, app: App) -> None:
    await app.nav.back(cb.from_user.id, cb.message)
    await cb.answer()


@router.callback_query(F.data.startswith("g|"))
async def go(cb: CallbackQuery, app: App) -> None:
    name, params = decode(cb.data)
    await app.nav.push(cb.from_user.id, cb.message, name, params)
    await cb.answer()


@router.callback_query(F.data.startswith("gr|"))
async def go_chain(cb: CallbackQuery, app: App) -> None:
    await app.nav.to_chain(cb.from_user.id, cb.message, cb.data.split("|", 1)[1])
    await cb.answer()


# --- действия -------------------------------------------------------------------
@router.callback_query(F.data == "a|trial")
async def trial(cb: CallbackQuery, app: App) -> None:
    uid = cb.from_user.id
    try:
        await app.partner.trial(f"tg-{uid}", f"trial-{uid}", {"tg_id": uid, "source": "bot"})
    except PartnerError as e:
        if e.code == "trial_already_used":
            await cb.answer(T.ALERT_TRIAL_USED, show_alert=True)
        elif e.code in ("trial_budget_exhausted", "trial_budget_unavailable", "conflict", "client_limit_reached",
                        "terms_expired", "terms_disabled", "plan_exceeds_terms"):
            await cb.answer(T.ALERT_TRIAL_UNAVAILABLE, show_alert=True)
        else:
            log.warning("триал tg-%s: %s", uid, e)
            await cb.answer(T.ALERT_FAILED, show_alert=True)
        return
    await app.nav.reset(uid, cb.message, [HOME, "sub"], notice=T.TRIAL_GIVEN)
    await cb.answer()


@router.callback_query(F.data == "a|stars")
async def stars_invoice(cb: CallbackQuery, app: App) -> None:
    """Экран способа оплаты заменяется счётом Stars: счёт – отдельное сообщение, править в него нельзя."""
    uid = cb.from_user.id
    p = await app.nav.top_params(uid)
    kind, months, devices = p["kind"], int(p["months"]), int(p["devices"])
    amount = app.settings.price("STARS", months, devices)
    pay = await stars_payment(app, uid, kind, months, devices, amount)
    months_text = MONTH_NAMES[months]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=T.BTN_PAY_STARS.format(price=fmt_money("STARS", amount)), pay=True)],  # первой – обязательно «Оплатить»
        [InlineKeyboardButton(text=T.BACK, callback_data=f"a|cancel|{pay.id}")],
    ])
    await app.bot.send_invoice(
        chat_id=uid, title=T.STARS_INVOICE_TITLE.format(months_text=months_text),
        description=T.STARS_INVOICE_DESC.format(months_text=months_text, devices=devices),
        payload=pay.id, currency="XTR", prices=[LabeledPrice(label="Master VPN", amount=int(amount))],
        reply_markup=kb)
    with contextlib.suppress(Exception):  # старый экран убираем только после того, как счёт отправлен
        await cb.message.delete()
    await cb.answer()


@router.callback_query(F.data.startswith("a|cancel|"))
async def cancel_invoice(cb: CallbackQuery, app: App) -> None:
    """«Назад» на счёте: счёт гасится и удаляется, возвращается экран способа оплаты."""
    uid = cb.from_user.id
    pay = await app.db.get_payment(cb.data.split("|", 2)[2])
    if pay is not None and pay.tg_id == uid:
        if pay.status in ("paid", "fulfilling", "fulfilled"):
            await cb.answer(T.ALERT_ALREADY_PAID, show_alert=True)
            return
        await app.db.transition(pay.id, ("created",), "expired")  # оплатить погашенный счёт уже нельзя
    with contextlib.suppress(Exception):
        await cb.message.delete()
    await app.nav.refresh(uid)
    await cb.answer()


@router.callback_query(F.data == "a|check")
async def check_usdt(cb: CallbackQuery, app: App) -> None:
    uid = cb.from_user.id
    opens = await app.db.open_payments(uid, "cryptopay")
    if not opens:
        await cb.answer(T.CHECK_NOT_FOUND, show_alert=True)
        return
    pay = opens[0]
    try:
        status = await app.cryptopay.status(pay.provider_ref or "")
    except CryptoPayError:
        await cb.answer(T.ALERT_FAILED, show_alert=True)
        return
    if status == "paid":
        await cb.answer(T.CHECK_PAID)
        await app.orders.process_paid(pay.id)
        await app.nav.reset(uid, cb.message, [HOME, "sub"])
    elif status == "expired":
        await app.db.transition(pay.id, ("created",), "expired")
        await cb.answer(T.CHECK_EXPIRED, show_alert=True)
        await app.nav.back(uid, cb.message)
    else:
        await cb.answer(T.CHECK_WAIT, show_alert=True)


@router.callback_query(F.data == "a|checkr")
async def check_rub(cb: CallbackQuery, app: App) -> None:
    uid = cb.from_user.id
    opens = await app.db.open_payments(uid, "platega")
    if not opens:
        await cb.answer(T.CHECK_NOT_FOUND, show_alert=True)
        return
    try:
        result = await check_platega(app, opens[0])
    except PlategaError:
        await cb.answer(T.ALERT_FAILED, show_alert=True)
        return
    if result == "paid":
        await cb.answer(T.CHECK_PAID)
        await app.nav.reset(uid, cb.message, [HOME, "sub"])
    elif result == "expired":
        await cb.answer(T.CHECK_EXPIRED, show_alert=True)
        await app.nav.back(uid, cb.message)
    else:
        await cb.answer(T.CHECK_WAIT, show_alert=True)


# --- Stars: проверка перед списанием и успешная оплата -----------------------------
@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, app: App) -> None:
    pay = await app.db.get_payment(query.invoice_payload)
    if (pay is None or pay.status != "created" or pay.tg_id != query.from_user.id
            or int(pay.amount) != query.total_amount):
        await query.answer(ok=False, error_message="Счёт устарел. Создайте новый в боте.")
        return
    try:
        async def check() -> bool:
            await app.plans.ensure()
            plan = app.plans.plan_for(pay.devices)
            days = app.plans.clamp_days(plan, pay.months * DAYS_PER_MONTH)
            quote = await app.partner.quote(plan["id"], days, attempts=1)
            balance = await app.partner.balance(attempts=1)
            return bool(quote.get("orders_enabled")) and int(balance["balance_minor"]) >= int(quote["amount_minor"])

        if not await asyncio.wait_for(check(), timeout=7):
            await query.answer(ok=False, error_message="Сейчас не можем выдать подписку. Попробуйте позже.")
            return
    except (PartnerError, asyncio.TimeoutError):
        pass  # API недоступен – не блокируем оплату: заказ выдастся из очереди
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def stars_paid(message: Message, app: App) -> None:
    sp = message.successful_payment
    await app.orders.process_paid(sp.invoice_payload, provider_ref=sp.telegram_payment_charge_id)

