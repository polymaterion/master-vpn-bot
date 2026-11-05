"""Все экраны бота. UI отделён от бизнес-логики: логика оплаты – в services/, здесь только вид."""
from __future__ import annotations

from datetime import timedelta

from aiogram.types import InlineKeyboardButton

from .. import texts as T
from ..config import DAYS_PER_MONTH, DEVICES, MONTHS
from ..providers.cryptopay import CryptoPayError
from ..services.payments import crypto_payment
from ..utils import esc, fmt_date, fmt_gib, fmt_money, make_qr_png
from .nav import Ctx, HOME, Screen, back_row, btn, screen

MONTH_NAMES = {1: "1 месяц", 3: "3 месяца", 6: "6 месяцев"}


def _url(text: str, url: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, url=url)] if url else []


def _int(p: dict, key: str) -> int:
    return int(p[key])


def summary(kind: str, months: int, devices: int) -> str:
    title = "Продление" if kind == "renew" else "Новая подписка"
    return f"{title}: <b>{MONTH_NAMES[months]}</b>, устройств: <b>{devices}</b>"


def new_until(sub, months: int):
    start = sub.expires_at if sub.running else sub.now
    return start + timedelta(days=months * DAYS_PER_MONTH)


@screen(HOME)
async def home(ctx: Ctx, p: dict) -> Screen:
    try:
        can_buy = (await ctx.sub()).can_buy
    except Exception:  # API недоступен – главное меню всё равно открываем
        can_buy = True
    rows = [[btn(T.BTN_SUB, "sub")]]
    if can_buy:
        rows.append([btn(T.BTN_BUY, "buy_term")])
    rows.append([btn(T.BTN_DOWNLOAD, "download"), btn(T.BTN_INFO, "info")])
    rows.append(_url(T.BTN_SUPPORT, ctx.settings.support_url))
    return Screen(T.HOME.format(brand=esc(ctx.settings.brand_name)), [r for r in rows if r])


@screen("sub")
async def sub_card(ctx: Ctx, p: dict) -> Screen:
    sub = await ctx.sub()
    if not sub.exists:
        rows = [[btn(T.BTN_BUY, "buy_term")],
                [InlineKeyboardButton(text=T.BTN_TRIAL, callback_data="a|trial")], back_row()]
        return Screen(T.SUB_NONE, rows)
    u = sub.user or {}
    try:
        used = len((await ctx.app.partner.devices(f"tg-{ctx.tg_id}")).get("devices", []))
    except Exception:
        used = 0
    status = T.STATUS_PAUSED if sub.paused else T.STATUS_ACTIVE if sub.running else T.STATUS_EXPIRED
    text = T.SUB_CARD.format(
        status=status, until=fmt_date(sub.expires_at), devices_used=used, devices_limit=sub.device_limit,
        used=fmt_gib(u.get("traffic_used_bytes")), limit=fmt_gib(u.get("traffic_limit_bytes")))
    rows = [[btn(T.BTN_LINK, "sub_link"), btn(T.BTN_QR, "sub_qr")]]
    if sub.can_renew:
        rows.append([btn(T.BTN_RENEW, "renew_term")])
    if sub.can_buy:
        rows.append([btn(T.BTN_BUY, "buy_term")])
    rows.append(back_row())
    return Screen(text, rows)


@screen("sub_link")
async def sub_link(ctx: Ctx, p: dict) -> Screen:
    sub = await ctx.sub()
    url = (sub.user or {}).get("subscription_url", "")
    return Screen(T.LINK.format(url=esc(url)), [back_row()])


@screen("sub_qr")
async def sub_qr(ctx: Ctx, p: dict) -> Screen:
    sub = await ctx.sub()
    url = (sub.user or {}).get("subscription_url", "")
    return Screen(T.QR.format(url=esc(url)), [back_row()], photo=make_qr_png(url) if url else None)


# --- продление -----------------------------------------------------------------
@screen("renew_term")
async def renew_term(ctx: Ctx, p: dict) -> Screen:
    sub = await ctx.sub()
    devices = sub.device_limit
    rows = []
    for m in MONTHS:
        price = ctx.settings.prices.get(("RUB", m, devices))
        label = MONTH_NAMES[m] + (f" — {fmt_money('RUB', price)}" if price else "")
        rows.append([btn(label, "renew_confirm", months=m)])
    return Screen(T.RENEW_TERM.format(devices=devices), rows + [back_row()])


@screen("renew_confirm")
async def renew_confirm(ctx: Ctx, p: dict) -> Screen:
    sub, months = await ctx.sub(), _int(p, "months")
    devices = sub.device_limit
    text = T.RENEW_CONFIRM.format(months_text=MONTH_NAMES[months], devices=devices, until=fmt_date(new_until(sub, months)),
                                  price=fmt_money("RUB", ctx.settings.price("RUB", months, devices)))
    return Screen(text, [[btn(T.BTN_PAY, "pay_method", kind="renew", months=months, devices=devices)], back_row()])


# --- покупка ---------------------------------------------------------------------
@screen("buy_term")
async def buy_term(ctx: Ctx, p: dict) -> Screen:
    sub = await ctx.sub()
    if not sub.can_buy:
        return Screen(T.BUY_ALREADY, [[btn(T.BTN_SUB, "sub")], back_row()])
    return Screen(T.BUY_TERM, [[btn(MONTH_NAMES[m], "buy_devices", months=m)] for m in MONTHS] + [back_row()])


@screen("buy_devices")
async def buy_devices(ctx: Ctx, p: dict) -> Screen:
    sub, months = await ctx.sub(), _int(p, "months")
    fixed = sub.running and not sub.has_paid  # действующий триал: другая ступень недоступна
    options = [sub.device_limit] if fixed else list(DEVICES)
    rows = [[btn(f"{d} устр. — {fmt_money('RUB', ctx.settings.price('RUB', months, d))}", "pay_method",
                 kind="buy", months=months, devices=d)] for d in options]
    text = T.BUY_DEVICES.format(months_text=MONTH_NAMES[months]) + (T.BUY_DEVICES_FIXED if fixed else "")
    return Screen(text, rows + [back_row()])


# --- оплата ----------------------------------------------------------------------
@screen("pay_method")
async def pay_method(ctx: Ctx, p: dict) -> Screen:
    kind, months, devices = p["kind"], _int(p, "months"), _int(p, "devices")
    sub = await ctx.sub()
    if kind == "buy" and sub.exists:
        # лишние устройства API не снимает сам: предупреждаем до оплаты, а не после
        used = len((await ctx.app.partner.devices(f"tg-{ctx.tg_id}")).get("devices", []))
        if used > devices:
            rows = [_url(T.BTN_SUPPORT, ctx.settings.support_url), back_row()]
            return Screen(T.BUY_EXCESS.format(used=used, limit=devices), [r for r in rows if r])
    s = ctx.settings
    rows = [
        # Stars: без промежуточного экрана – кнопка сразу отправляет счёт (см. handlers/user.py)
        [InlineKeyboardButton(text=T.BTN_STARS.format(price=fmt_money("STARS", s.price("STARS", months, devices))),
                              callback_data="a|stars")],
        [btn(T.BTN_USDT.format(price=fmt_money("USDT", s.price("USDT", months, devices))), "pay_usdt", **p)],
        [btn(T.BTN_RUB.format(price=fmt_money("RUB", s.price("RUB", months, devices))), "pay_rub", **p)],
        back_row(),
    ]
    text = T.PAY_METHOD.format(summary=summary(kind, months, devices), until=fmt_date(new_until(sub, months)))
    return Screen(text, rows)


@screen("pay_usdt")
async def pay_usdt(ctx: Ctx, p: dict) -> Screen:
    kind, months, devices = p["kind"], _int(p, "months"), _int(p, "devices")
    if not ctx.app.cryptopay.enabled:
        return Screen(T.PAY_UNAVAILABLE, [back_row()])
    amount = ctx.settings.price("USDT", months, devices)
    try:
        pay = await crypto_payment(ctx.app, ctx.tg_id, kind, months, devices, amount,
                                   f"Master VPN: {MONTH_NAMES[months]}, устройств: {devices}")
    except CryptoPayError:
        return Screen(T.PAY_UNAVAILABLE, [back_row()])
    text = T.PAY_USDT.format(summary=summary(kind, months, devices), price=fmt_money("USDT", amount))
    rows = [_url(T.BTN_PAY_LINK, pay.invoice_url or ""),
            [InlineKeyboardButton(text=T.BTN_CHECK, callback_data="a|check")], back_row()]
    return Screen(text, [r for r in rows if r])


@screen("pay_rub")
async def pay_rub(ctx: Ctx, p: dict) -> Screen:
    # Платёжка Platega подключается позже (app/providers/platega.py). Пока – заглушка.
    return Screen(T.PAY_RUB_SOON, [back_row()])


# --- справочные экраны ---------------------------------------------------------------
@screen("download")
async def download(ctx: Ctx, p: dict) -> Screen:
    L = ctx.settings.links
    rows = [_url(T.BTN_ANDROID, L["android"]), _url(T.BTN_IOS, L["ios"]), _url(T.BTN_WINDOWS, L["windows"])]
    return Screen(T.DOWNLOAD, [r for r in rows if r] + [back_row()])


@screen("info")
async def info(ctx: Ctx, p: dict) -> Screen:
    L = ctx.settings.links
    rows = [_url(T.BTN_POLICY, L["policy"]), _url(T.BTN_TERMS, L["terms"])]
    return Screen(T.INFO, [r for r in rows if r] + [back_row()])
