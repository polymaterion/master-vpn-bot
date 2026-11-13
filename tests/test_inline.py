from __future__ import annotations

from aiogram.types import InlineQueryResultArticle

from app import texts as T
from app.handlers.inline import build_results


def test_article_with_preview():
    (r,) = build_results("https://bot.example.com", "mybot")
    assert isinstance(r, InlineQueryResultArticle)
    c = r.input_message_content
    assert c.message_text == T.INLINE_CAPTION
    assert c.link_preview_options.url == "https://bot.example.com/static/inline.jpg"
    assert c.link_preview_options.show_above_text and c.link_preview_options.prefer_large_media
    assert r.thumbnail_url == "https://bot.example.com/static/inline_thumb.jpg"
    assert r.reply_markup.inline_keyboard[0][0].url == "https://t.me/mybot?start=inline"


def test_text_only_without_public_url():
    (r,) = build_results("", "mybot")
    assert r.input_message_content.link_preview_options is None and r.thumbnail_url is None


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
