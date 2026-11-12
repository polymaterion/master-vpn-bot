from __future__ import annotations

import csv
import io
from datetime import timedelta

from app.db import PaymentRow
from app.services.users import build_users_csv, format_sources
from app.utils import parse_source, utcnow


def test_parse_source():
    assert parse_source("YT_Ivan-1") == "yt_ivan-1"
    assert parse_source("a b$c") == "abc"
    assert parse_source(None) is None and parse_source("  ") is None
    assert len(parse_source("x" * 100)) == 64


async def test_register_first_touch(db):
    assert await db.register_user(1, "ann", "Ann", None, "yt") is True
    assert await db.register_user(1, "ann2", "Ann", None, "other") is False   # источник не перезаписывается
    assert await db.register_user(2, None, "Bob", "B", None) is True
    users = {u.tg_id: u for u in await db.list_users()}
    assert users[1].source == "yt" and users[1].username == "ann2"
    await db.touch_profile(2, "bob", "Bob", "B")
    await db.touch_profile(999, "ghost", "G", None)                           # неизвестного не создаёт
    assert {u.tg_id for u in await db.list_users()} == {1, 2}
    assert (await db.list_users())[1].username == "bob"


async def test_csv_and_stats(db):
    await db.register_user(1, "ann", "=cmd", None, "yt")
    await db.register_user(2, None, "Бob", None, "yt")
    await db.register_user(3, None, "C", None, None)
    await db.add_payment(PaymentRow(id="p1", tg_id=1, provider="stars", kind="buy", months=1, devices=1,
                                    amount=1, currency="STARS", status="fulfilled"))
    rows = list(csv.reader(io.StringIO(build_users_csv(await db.list_users(), await db.paid_user_ids()).decode("utf-8-sig"))))
    assert rows[0][:5] == ["tg_id", "username", "first_name", "last_name", "source"]
    assert rows[1][2] == "'=cmd" and rows[1][6] == "yes" and rows[2][6] == "no"
    stats = await db.source_stats()
    assert stats[0] == {"source": "yt", "users": 2, "paid": 1}
    assert await db.source_stats(utcnow() + timedelta(days=1)) == []
    assert "yt: 2 польз., оплатили 1 (50%)" in format_sources(stats, "bot", None)
