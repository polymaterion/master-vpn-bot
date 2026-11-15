"""Inline-режим: @бот в любом чате показывает готовую рекламную карточку (картинка + текст + кнопка).

Включается один раз в @BotFather: /setinline. Кнопка ведёт в бота по ссылке ?start=inline –
такие заходы попадают в статистику источников с меткой «inline».
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery, InlineQueryResultArticle,
                           InputTextMessageContent, LinkPreviewOptions)

from .. import texts as T
from ..context import App
from ..emoji import plain

router = Router()
log = logging.getLogger("inline")
INLINE_SOURCE = "inline"


def build_results(public_base_url: str, bot_username: str, *, premium: bool = True) -> list:
    """Карточка в списке: маленькая иконка + название + описание.
    В чат уходит текст, а картинка показывается превью ссылки над ним (как у @tribute)."""
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=T.INLINE_BUTTON, url=f"https://t.me/{bot_username}?start={INLINE_SOURCE}")]])
    https = public_base_url.startswith("https://")
    preview = LinkPreviewOptions(url=f"{public_base_url}/static/inline.jpg", prefer_large_media=True,
                                 show_above_text=True) if https else None
    return [InlineQueryResultArticle(
        id="promo", title=T.INLINE_TITLE, description=T.INLINE_DESCRIPTION,
        input_message_content=InputTextMessageContent(
            message_text=T.INLINE_CAPTION if premium else plain(T.INLINE_CAPTION),
            parse_mode="HTML", link_preview_options=preview),
        reply_markup=kb,
        thumbnail_url=f"{public_base_url}/static/inline_thumb.jpg" if https else None,
        thumbnail_width=320, thumbnail_height=320)]


@router.inline_query()
async def inline(query: InlineQuery, app: App) -> None:
    me = await app.bot.me()
    log.info("inline-запрос от %s: %r", query.from_user.id, query.query)
    base, name = app.settings.public_base_url, me.username or ""
    # от лучшего к простому: премиум-эмодзи + картинка, обычные эмодзи + картинка, только текст
    attempts = [(base, True), (base, False), ("", False)]
    for i, (url, premium) in enumerate(attempts):
        try:
            # карточка одинакова для всех – Telegram может кешировать ответ
            await query.answer(build_results(url, name, premium=premium), cache_time=60, is_personal=False)
            return
        except TelegramAPIError as e:
            log.error("inline: ответ отклонён (%s), пробую проще", e)
            if i == len(attempts) - 1:
                raise
