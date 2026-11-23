"""Премиум-эмодзи (Bot API 9.4): в тексте сообщений и иконками на кнопках.

Работает, пока у владельца бота есть Telegram Premium. Если Telegram отклонит запрос из-за эмодзи, бот сам
повторит его с обычными эмодзи и дальше отправляет только обычные (см. PremiumEmojiMiddleware).
Выключить совсем: PREMIUM_EMOJI=0 в .env. Админские сообщения и кнопки остаются с обычными эмодзи.
"""
from __future__ import annotations

import logging
import os
import re

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (EditMessageCaption, EditMessageReplyMarkup, EditMessageText, SendMessage, SendPhoto,
                             TelegramMethod)
from aiogram.types import InlineKeyboardMarkup

log = logging.getLogger("emoji")

ENABLED = os.environ.get("PREMIUM_EMOJI", "1").strip().lower() not in ("0", "false", "no", "off")

# --- эмодзи в тексте сообщений: имя -> (обычный эмодзи, id) -------------------------------------
TEXT: dict[str, tuple[str, str]] = {
    "home": ("🛡", "5778570255555105942"),
    "sub": ("📱", "6035084557378654059"),
    "expired": ("⛔", "5774077015388852135"),
    "ok": ("✅", "5774022692642492953"),
    "paused": ("⏸", "6030563507299160824"),
    "gift": ("🎁", "5805298713211447980"),
    "link": ("🔗", "6028171274939797252"),
    "qr": ("🔳", "5766975922620076409"),
    "renew": ("🔄", "6030657343744644592"),
    "order": ("🧾", "5884106131822875141"),
    "buy": ("🛒", "5890727932011223292"),
    "pay": ("💳", "5769126056262898415"),
    "usdt": ("💲", "6037083366438737901"),
    "download": ("⬇️", "6039802767931871481"),
    "info": ("ℹ️", "6028435952299413210"),
    "warn": ("⚠️", "5881702736843511327"),
    "traffic": ("📉", "5776288820467077551"),
    "remind": ("⏰", "5983150113483134607"),
    "inline": ("👇", "5890925363067886150"),
    "broom": ("🧹", "6007942490076745785"),
    "refund": ("↩️", "5877536313623711363"),
}

# --- иконки на кнопках: (эмодзи в начале текста кнопки, слово в тексте или None, id) -------------
_BUTTON_RULES: list[tuple[str, str | None, str]] = [
    ("⬅️", None, "5960671702059848143"),
    ("📱", None, "5920332557466997677"),
    ("🛒", None, "5890883384057533697"),
    ("⬇️", None, "6039802767931871481"),
    ("ℹ️", None, "6028435952299413210"),
    ("💬", None, "6034831751308644168"),
    ("🎁", None, "5936088245560419529"),
    ("🔗", None, "6028171274939797252"),
    ("🔳", None, "5766975922620076409"),
    ("🔄", None, "6030657343744644592"),
    ("💳", "CryptoBot", "5891105528356018797"),
    ("💳", "СБП", "5265074015868822600"),
    ("💳", None, "5769126056262898415"),
    ("⭐", None, "6028338546736107668"),
    ("💲", None, "6037083366438737901"),
    ("🔍", None, "5884106131822875141"),
    ("🧹", None, "6007942490076745785"),
    ("✅", None, "5774022692642492953"),
]
_ICON_FALLBACK = {icon: char for char, _, icon in reversed(_BUTTON_RULES)}  # id -> обычный эмодзи

_TG_EMOJI = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)
_runtime_off = False
_fallbacks = 0
_OFF_AFTER = 3   # столько успешных откатов, и премиум-эмодзи отключаются до перезапуска
_UNRELATED = ("not modified", "message to edit not found", "message can't be edited", "query is too old",
              "chat not found", "message to delete not found")


def tg(name: str) -> str:
    char, emoji_id = TEXT[name]
    return f'<tg-emoji emoji-id="{emoji_id}">{char}</tg-emoji>' if ENABLED else char


def plain(text: str | None) -> str | None:
    """Убирает разметку премиум-эмодзи, оставляя обычный эмодзи."""
    return _TG_EMOJI.sub(r"\1", text) if text else text


def _icon_for(text: str) -> tuple[str, str] | None:
    for char, word, emoji_id in _BUTTON_RULES:
        if text.startswith(char + " ") and (word is None or word in text):
            rest = text[len(char):].strip()
            if char == "⭐":  # на кнопке звезда одна – иконкой, хвостовую из цены убираем
                rest = rest.removesuffix("⭐").strip()
            return emoji_id, rest
    return None


def iconize(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Переносит эмодзи из начала текста кнопки в иконку (icon_custom_emoji_id)."""
    rows, changed = [], False
    for row in markup.inline_keyboard:
        new = []
        for b in row:
            found = None if b.pay or b.icon_custom_emoji_id else _icon_for(b.text)
            if found:
                new.append(b.model_copy(update={"text": found[1], "icon_custom_emoji_id": found[0]}))
                changed = True
            else:
                new.append(b)
        rows.append(new)
    return markup.model_copy(update={"inline_keyboard": rows}) if changed else markup


def deiconize(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows, changed = [], False
    for row in markup.inline_keyboard:
        new = []
        for b in row:
            if b.icon_custom_emoji_id:
                char = _ICON_FALLBACK.get(b.icon_custom_emoji_id, "")
                new.append(b.model_copy(update={"text": f"{char} {b.text}".strip(), "icon_custom_emoji_id": None}))
                changed = True
            else:
                new.append(b)
        rows.append(new)
    return markup.model_copy(update={"inline_keyboard": rows}) if changed else markup


_MARKUP_METHODS = (SendMessage, SendPhoto, EditMessageText, EditMessageCaption, EditMessageReplyMarkup)


def _has_premium(method: TelegramMethod) -> bool:
    text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
    markup = getattr(method, "reply_markup", None)
    has_icon = isinstance(markup, InlineKeyboardMarkup) and any(
        b.icon_custom_emoji_id for row in markup.inline_keyboard for b in row)
    return "<tg-emoji" in text or has_icon


def _downgrade(method: TelegramMethod) -> None:
    for field in ("text", "caption"):
        if isinstance(getattr(method, field, None), str):
            setattr(method, field, plain(getattr(method, field)))
    markup = getattr(method, "reply_markup", None)
    if isinstance(markup, InlineKeyboardMarkup):
        method.reply_markup = deiconize(markup)


class PremiumEmojiMiddleware(BaseRequestMiddleware):
    """Иконки на кнопках и откат на обычные эмодзи, если Telegram премиум-эмодзи не принял."""

    async def __call__(self, make_request, bot: Bot, method: TelegramMethod):
        global _runtime_off, _fallbacks
        if not isinstance(method, _MARKUP_METHODS):
            return await make_request(bot, method)
        if ENABLED and not _runtime_off:
            markup = getattr(method, "reply_markup", None)
            if isinstance(markup, InlineKeyboardMarkup):
                method.reply_markup = iconize(markup)
        elif _has_premium(method):
            _downgrade(method)
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as first:
            # ошибки, не связанные с эмодзи (например, «message is not modified» при повторном нажатии кнопки),
            # не должны отключать премиум-эмодзи
            if not _has_premium(method) or any(m in str(first).lower() for m in _UNRELATED):
                raise
            log.warning("Telegram отклонил сообщение с премиум-эмодзи (%s): повторяю с обычными эмодзи", first)
            _downgrade(method)
            result = await make_request(bot, method)  # если и так не вышло – ошибка уйдёт наверх
            _fallbacks += 1
            if _fallbacks >= _OFF_AFTER and not _runtime_off:
                _runtime_off = True
                log.warning("Премиум-эмодзи отклонены %s раза подряд: дальше отправляю обычные. "
                            "Проверьте, что у владельца бота есть Telegram Premium.", _fallbacks)
            return result
