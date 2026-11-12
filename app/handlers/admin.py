from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..context import App
from ..notify import PROVIDER_TITLES
from ..partner.client import PartnerError
from ..services.users import build_users_csv, format_sources
from ..utils import utcnow, fmt_date, fmt_gib, fmt_money, parse_iso

log = logging.getLogger("admin")


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def user_card(app: App, tg_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    ext = f"tg-{tg_id}"
    u = await app.partner.user(ext)
    if u is None:
        return f"👤 {ext}\nКлиент не найден в Partner API.", None
    try:
        used = len((await app.partner.devices(ext)).get("devices", []))
    except PartnerError:
        used = "?"
    text = (f"👤 {ext}\nСтатус: {u['status']}\nДо: {fmt_date(parse_iso(u.get('expires_at')))}\n"
            f"Устройств: {used} из {u['device_limit']}\n"
            f"Трафик: {fmt_gib(u['traffic_used_bytes'])} / {fmt_gib(u['traffic_limit_bytes'])}\n"
            f"Ссылка: {u['subscription_url']}")
    kb = _kb([_b("⏸ Пауза", f"adm|pause|{tg_id}"), _b("▶️ Возобновить", f"adm|resume|{tg_id}")],
             [_b("🔑 Новая ссылка", f"adm|rotate_ask|{tg_id}")])
    return text, kb


def build_admin_router(admin_ids: tuple[int, ...]) -> Router:
    router = Router()
    router.message.filter(F.from_user.id.in_(admin_ids))
    router.callback_query.filter(F.from_user.id.in_(admin_ids))

    @router.message(Command("user"))
    async def cmd_user(message: Message, command: CommandObject, app: App) -> None:
        arg = (command.args or "").strip().removeprefix("tg-")
        if not arg.isdigit():
            await message.answer("Использование: /user <tg_id>")
            return
        text, kb = await user_card(app, int(arg))
        await message.answer(text, reply_markup=kb, parse_mode=None)

    @router.message(Command("stats"))
    async def cmd_stats(message: Message, app: App) -> None:
        bal, stats = await app.partner.balance(), await app.partner.stats(7)
        trial = bal.get("customer_trial", {})
        await message.answer(
            f"📊 Кошелёк: {fmt_money('RUB', int(bal['balance_minor']) / 100)}\n"
            f"Триалов ещё помещается: ~{trial.get('remaining_estimate', 0)}\n\n"
            f"За 7 суток: клиентов новых {stats['clients_new']}, активных {stats['clients_active']}, "
            f"заказов {stats['payments']}", parse_mode=None)

    @router.message(Command("users"))
    async def cmd_users(message: Message, app: App) -> None:
        users = await app.db.list_users()
        data = build_users_csv(users, await app.db.paid_user_ids())
        name = f"users_{utcnow():%Y%m%d_%H%M}.csv"
        await message.answer_document(BufferedInputFile(data, filename=name), caption=f"Пользователей: {len(users)}")

    @router.message(Command("sources"))
    async def cmd_sources(message: Message, command: CommandObject, app: App) -> None:
        arg = (command.args or "").strip()
        days = int(arg) if arg.isdigit() and 0 < int(arg) <= 3650 else None
        since = utcnow() - timedelta(days=days) if days else None
        rows = await app.db.source_stats(since)
        me = await app.bot.me()
        await message.answer(format_sources(rows, me.username or "", days), parse_mode=None,
                             disable_web_page_preview=True)

    @router.message(Command("pending"))
    async def cmd_pending(message: Message, app: App) -> None:
        rows = await app.db.unfinished()
        if not rows:
            await message.answer("Зависших выдач нет ✅")
            return
        for p in rows:
            kb = _kb([_b("🔁 Повторить", f"adm|retry|{p.id}")]) if p.status == "failed" else None
            await message.answer(f"{p.status}: tg-{p.tg_id}, {PROVIDER_TITLES.get(p.provider, p.provider)}, "
                                 f"{fmt_money(p.currency, p.amount)}, код: {p.error_code or '–'}",
                                 reply_markup=kb, parse_mode=None)

    async def _refresh(cb: CallbackQuery, app: App, tg_id: int, notice: str) -> None:
        text, kb = await user_card(app, tg_id)
        await cb.message.edit_text(f"{notice}\n\n{text}", reply_markup=kb, parse_mode=None)

    @router.callback_query(F.data.startswith("adm|"))
    async def admin_action(cb: CallbackQuery, app: App) -> None:
        _, action, arg = cb.data.split("|", 2)
        admin = cb.from_user.id
        try:
            if action in ("pause", "resume"):
                tg = int(arg)
                await (app.partner.pause(f"tg-{tg}") if action == "pause" else app.partner.resume(f"tg-{tg}"))
                await app.db.audit(admin, action, f"tg-{tg}")
                await _refresh(cb, app, tg, "✅ Готово")
            elif action == "rotate_ask":
                await cb.message.edit_text(
                    f"Перевыпустить ссылку tg-{arg}? Старая сразу перестанет работать, клиенту нужно отдать новую.",
                    reply_markup=_kb([_b("Да, перевыпустить", f"adm|rotate|{arg}"), _b("Отмена", f"adm|card|{arg}")]))
            elif action == "rotate":
                res = await app.partner.rotate(f"tg-{arg}")
                await app.db.audit(admin, "rotate", f"tg-{arg}")
                await _refresh(cb, app, int(arg), f"🔑 Новая ссылка: {res['subscription_url']}")
            elif action == "card":
                await _refresh(cb, app, int(arg), "")
            elif action == "retry":
                result = await app.orders.admin_retry(arg)
                await app.db.audit(admin, "retry", arg, result)
                await cb.message.answer(f"Повтор выдачи: {result}")
            elif action == "refund":
                pay = await app.db.get_payment(arg)
                if pay is None or pay.provider != "stars" or pay.status != "failed" or not pay.provider_ref:
                    await cb.answer("Возврат недоступен", show_alert=True)
                    return
                await app.bot.refund_star_payment(user_id=pay.tg_id, telegram_payment_charge_id=pay.provider_ref)
                await app.db.transition(pay.id, ("failed",), "refunded")
                await app.db.audit(admin, "refund", pay.id, pay.provider_ref)
                await app.notifier.send(pay.tg_id, "↩️ Звёзды за неудавшуюся покупку возвращены на ваш счёт.")
                await cb.message.answer("Звёзды возвращены")
        except PartnerError as e:
            await cb.answer(f"Ошибка API: {e.code or e.status}", show_alert=True)
            return
        except Exception as e:  # noqa: BLE001 — админу показываем причину
            log.exception("admin action %s", action)
            await cb.answer(f"Ошибка: {e}"[:190], show_alert=True)
            return
        await cb.answer()

    return router
