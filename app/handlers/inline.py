"""Inline-режим: @бот в любом чате показывает готовую рекламную карточку (картинка + текст + кнопка).

Включается один раз в @BotFather: /setinline. Кнопка ведёт в бота по ссылке ?start=inline –
такие заходы попадают в статистику источников с меткой «inline».
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery, InlineQueryResultArticle,
                           InlineQueryResultPhoto, InputTextMessageContent)

from .. import texts as T
from ..context import App

router = Router()
log = logging.getLogger("inline")
INLINE_SOURCE = "inline"
PHOTO_SIZE = (1280, 720)


def build_results(public_base_url: str, bot_username: str) -> list:
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=T.INLINE_BUTTON, url=f"https://t.me/{bot_username}?start={INLINE_SOURCE}")]])
    if public_base_url.startswith("https://"):
        return [InlineQueryResultPhoto(
            id="promo", photo_url=f"{public_base_url}/static/inline.jpg",
            thumbnail_url=f"{public_base_url}/static/inline_thumb.jpg",
            photo_width=PHOTO_SIZE[0], photo_height=PHOTO_SIZE[1],
            title=T.INLINE_TITLE, description=T.INLINE_DESCRIPTION,
            caption=T.INLINE_CAPTION, reply_markup=kb)]
    # без PUBLIC_BASE_URL картинку отдать нечем – отправляем только текст
    return [InlineQueryResultArticle(
        id="promo", title=T.INLINE_TITLE, description=T.INLINE_DESCRIPTION,
        input_message_content=InputTextMessageContent(message_text=T.INLINE_CAPTION), reply_markup=kb)]


def _text_only(bot_username: str) -> list:
    return build_results("", bot_username)


@router.inline_query()
async def inline(query: InlineQuery, app: App) -> None:
    me = await app.bot.me()
    log.info("inline-запрос от %s: %r", query.from_user.id, query.query)
    # карточка одинакова для всех – Telegram может кешировать ответ
    try:
        await query.answer(build_results(app.settings.public_base_url, me.username or ""),
                           cache_time=60, is_personal=False)
    except TelegramAPIError as e:
        # например, Telegram не смог скачать картинку – отдаём хотя бы текст
        log.error("inline: ответ с картинкой отклонён (%s), отправляю только текст", e)
        await query.answer(_text_only(me.username or ""), cache_time=60, is_personal=False)
