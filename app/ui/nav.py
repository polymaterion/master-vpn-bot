"""Navigation Stack + Screen Controller.

Каждый экран – функция (ctx, params) -> Screen. Стек экранов хранится в БД (переживает перезапуск).
Любая кнопка открывает новый экран и правит то же сообщение; «Назад» всегда ровно на один экран назад.
"""
from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions,
                           Message)

from .. import texts as T
from ..partner.client import PartnerError

if TYPE_CHECKING:
    from ..context import App

log = logging.getLogger("nav")
MAX_DEPTH = 20
HOME = "home"

# Экраны, на которые ведут кнопки в уведомлениях (gr|<экран>): цепочка «откуда пришёл» для Back.
CHAINS: dict[str, list[str]] = {
    "sub_link": [HOME, "sub"], "sub_qr": [HOME, "sub"],
    "renew_term": [HOME, "sub"], "buy_term": [HOME],
}


@dataclass
class Screen:
    text: str
    rows: list[list[InlineKeyboardButton]] = field(default_factory=list)
    photo: bytes | None = None

    @property
    def kb(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=self.rows)


def encode(name: str, **params: object) -> str:
    return "|".join(["g", name] + [f"{k}={v}" for k, v in params.items()])


def decode(data: str) -> tuple[str, dict[str, str]]:
    _, name, *rest = data.split("|")
    return name, dict(item.split("=", 1) for item in rest)


def btn(text: str, name: str, **params: object) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=encode(name, **params))


def back_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=T.BACK, callback_data="b")]


@dataclass
class Ctx:
    app: "App"
    tg_id: int

    @property
    def settings(self):
        return self.app.settings

    async def sub(self):
        from ..services.subscription import load_sub
        return await load_sub(self.app, self.tg_id)


ScreenFn = Callable[[Ctx, dict], Awaitable[Screen]]
SCREENS: dict[str, ScreenFn] = {}


def screen(name: str) -> Callable[[ScreenFn], ScreenFn]:
    def deco(fn: ScreenFn) -> ScreenFn:
        SCREENS[name] = fn
        return fn
    return deco


async def show(bot: Bot, chat_id: int, target: Message | None, scr: Screen) -> None:
    """Правит существующее сообщение; новое – только когда меняется тип (текст ⇄ фото) или цели нет."""
    if target is not None:
        if scr.photo is None and not target.photo:
            try:
                await target.edit_text(scr.text, reply_markup=scr.kb,
                                       link_preview_options=LinkPreviewOptions(is_disabled=True))
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                log.info("не удалось отредактировать, отправляю заново: %s", e)
        with contextlib.suppress(Exception):
            await target.delete()
    if scr.photo is not None:
        await bot.send_photo(chat_id, BufferedInputFile(scr.photo, "qr.png"), caption=scr.text, reply_markup=scr.kb)
    else:
        await bot.send_message(chat_id, scr.text, reply_markup=scr.kb,
                               link_preview_options=LinkPreviewOptions(is_disabled=True))


class Navigator:
    def __init__(self, app: "App"):
        self.app = app

    def ctx(self, tg_id: int) -> Ctx:
        return Ctx(self.app, tg_id)

    async def stack(self, tg_id: int) -> list[dict]:
        return await self.app.db.get_nav(tg_id) or [{"s": HOME, "p": {}}]

    async def top_params(self, tg_id: int) -> dict:
        return (await self.stack(tg_id))[-1]["p"]

    async def _render(self, tg_id: int, target: Message | None, stack: list[dict], notice: str | None = None) -> None:
        await self.app.db.set_nav(tg_id, stack)
        top = stack[-1]
        fn = SCREENS.get(top["s"])
        if fn is None:
            stack[:] = [{"s": HOME, "p": {}}]
            fn, top = SCREENS[HOME], stack[-1]
        try:
            scr = await fn(self.ctx(tg_id), top["p"])
        except PartnerError as e:
            log.warning("экран %s: ошибка API %s", top["s"], e)
            scr = Screen(T.ERROR_SCREEN, [back_row()] if len(stack) > 1 else [])
        if notice:
            scr.text = f"{notice}\n\n{scr.text}"
        await show(self.app.bot, tg_id, target, scr)

    async def start(self, tg_id: int, target: Message | None = None, notice: str | None = None) -> None:
        await self._render(tg_id, target, [{"s": HOME, "p": {}}], notice)

    async def push(self, tg_id: int, target: Message | None, name: str, params: dict | None = None) -> None:
        stack = await self.stack(tg_id)
        stack.append({"s": name, "p": params or {}})
        await self._render(tg_id, target, stack[-MAX_DEPTH:])

    async def refresh(self, tg_id: int, target: Message | None = None) -> None:
        """Показать текущий (верхний) экран стека заново; без target – новым сообщением."""
        await self._render(tg_id, target, await self.stack(tg_id))

    async def back(self, tg_id: int, target: Message | None) -> None:
        stack = await self.stack(tg_id)
        if len(stack) > 1:
            stack.pop()
        await self._render(tg_id, target, stack)

    async def reset(self, tg_id: int, target: Message | None, names: list[str], notice: str | None = None) -> None:
        """Стек = цепочка экранов (последний – текущий). Для кнопок в уведомлениях и после выдачи."""
        await self._render(tg_id, target, [{"s": n, "p": {}} for n in names], notice)

    async def to_chain(self, tg_id: int, target: Message | None, name: str) -> None:
        await self.reset(tg_id, target, CHAINS.get(name, [HOME]) + [name])
