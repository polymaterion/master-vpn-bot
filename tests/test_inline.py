from __future__ import annotations

from aiogram.types import InlineQueryResultArticle, InlineQueryResultPhoto

from app import texts as T
from app.handlers.inline import build_results


def test_photo_result():
    (r,) = build_results("https://bot.example.com", "mybot")
    assert isinstance(r, InlineQueryResultPhoto)
    assert r.photo_url == "https://bot.example.com/static/inline.jpg" and r.caption == T.INLINE_CAPTION
    assert r.reply_markup.inline_keyboard[0][0].url == "https://t.me/mybot?start=inline"


def test_text_fallback_without_public_url():
    (r,) = build_results("", "mybot")
    assert isinstance(r, InlineQueryResultArticle)
    assert r.input_message_content.message_text == T.INLINE_CAPTION


async def test_static_served(tmp_path):
    import pathlib
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    import app.webhooks as w
    app = web.Application()
    app.router.add_static("/static", pathlib.Path(w.__file__).parent / "static")
    async with TestClient(TestServer(app)) as c:
        r = await c.get("/static/inline.jpg")
        assert r.status == 200 and r.content_type == "image/jpeg"
