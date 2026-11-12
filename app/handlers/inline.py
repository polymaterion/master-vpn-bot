"""Inline-режим: @бот в любом чате показывает готовую рекламную карточку (картинка + текст + кнопка).

Включается один раз в @BotFather: /setinline. Кнопка ведёт в бота по ссылке ?start=inline –
такие заходы попадают в статистику источников с меткой «inline».
"""
from __future__ import annotations

from aiogram import Router
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery, InlineQueryResultArticle,
                           InlineQueryResultPhoto, InputTextMessageContent)

from .. import texts as T
from ..context import App

router = Router()
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


@router.inline_query()
async def inline(query: InlineQuery, app: App) -> None:
    me = await app.bot.me()
    # карточка одинакова для всех – Telegram может кешировать ответ
    await query.answer(build_results(app.settings.public_base_url, me.username or ""),
                       cache_time=300, is_personal=False)
