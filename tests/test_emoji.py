from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import emoji, texts as T
from app.handlers.inline import build_results


def kb(*labels):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data="x")] for t in labels])


def flat(markup):
    return [b for row in markup.inline_keyboard for b in row]


def test_user_buttons_get_icons():
    out = flat(emoji.iconize(kb("📱 Моя подписка", "⬅️ Назад", "🧹 Сбросить устройства", "✅ Да, сбросить")))
    assert [(b.text, b.icon_custom_emoji_id) for b in out] == [
        ("Моя подписка", "5920332557466997677"), ("Назад", "5960671702059848143"),
        ("Сбросить устройства", "6007942490076745785"), ("Да, сбросить", "5774022692642492953")]


def test_payment_button_icons_and_stars_suffix():
    out = flat(emoji.iconize(kb("💳 Оплатить", "💳 Оплатить в CryptoBot", "💳 Оплатить по СБП",
                               "⭐ Stars — 150 ⭐", "💲 USDT — 2.40 USDT")))
    assert [b.icon_custom_emoji_id for b in out[:3]] == ["5769126056262898415", "5891105528356018797",
                                                         "5265074015868822600"]
    assert out[3].text == "Stars — 150" and out[3].icon_custom_emoji_id == "6028338546736107668"


def test_admin_buttons_and_pay_button_untouched():
    m = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Повторить", callback_data="a")],
        [InlineKeyboardButton(text="↩️ Возврат Stars", callback_data="b")],
        [InlineKeyboardButton(text="⏸ Пауза", callback_data="c")],
        [InlineKeyboardButton(text="⭐ 150", pay=True)]])
    assert emoji.iconize(m) is m


def test_deiconize_restores_text():
    m = emoji.iconize(kb("💳 Оплатить по СБП", "🔍 Проверить оплату"))
    assert [b.text for b in flat(emoji.deiconize(m))] == ["💳 Оплатить по СБП", "🔍 Проверить оплату"]


def test_texts_use_premium_markup_and_plain_strips_it():
    assert '<tg-emoji emoji-id="5778570255555105942">🛡</tg-emoji>' in T.HOME
    assert emoji.plain(T.HOME).startswith("🛡 <b>")
    assert "<tg-emoji" not in emoji.plain(T.N_REFUND) and "↩️" in emoji.plain(T.N_REFUND)
    for name in ("SUB_CARD", "SUB_EXPIRED", "N_PAID_OK", "RESET_DEVICES", "PAY_USDT", "N_REMIND"):
        T_val = getattr(T, name)
        assert "<tg-emoji" in T_val
    T.SUB_EXPIRED.format(until="x"), T.SUB_CARD.format(status=T.STATUS_ACTIVE, until="x", devices_used=1,
                                                       devices_limit=1, used="1", limit="1")


def test_admin_texts_stay_plain():
    for t in (T.A_PAID_OK, T.A_WALLET_EMPTY, T.A_PENDING, T.A_FAILED, T.A_BALANCE_LOW, T.A_PLATEGA_ALERT):
        assert "<tg-emoji" not in t


def test_inline_premium_and_plain():
    (r,) = build_results("https://x.example", "bot")
    assert "<tg-emoji" in r.input_message_content.message_text
    (r,) = build_results("https://x.example", "bot", premium=False)
    assert "<tg-emoji" not in r.input_message_content.message_text and r.input_message_content.message_text.endswith("👇")


async def test_middleware_adds_icons_and_falls_back_once():
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendMessage

    calls = []

    async def make_request(bot, method):
        calls.append((method.text, [b.icon_custom_emoji_id for r in method.reply_markup.inline_keyboard for b in r]))
        if "<tg-emoji" in method.text:
            raise TelegramBadRequest(method=method, message="Bad Request: custom emoji not allowed")
        return "ok"

    mw = emoji.PremiumEmojiMiddleware()
    method = SendMessage(chat_id=1, text=T.HOME.format(brand="X"), reply_markup=kb("📱 Моя подписка"))
    try:
        assert await mw(make_request, None, method) == "ok"
        assert calls[0][1] == ["5920332557466997677"] and "<tg-emoji" in calls[0][0]
        assert calls[1][1] == [None] and "<tg-emoji" not in calls[1][0]
        assert method.reply_markup.inline_keyboard[0][0].text == "📱 Моя подписка"
        assert emoji._runtime_off is True
        # дальше сразу обычные эмодзи, без лишнего запроса
        calls.clear()
        m2 = SendMessage(chat_id=1, text=T.HOME.format(brand="X"), reply_markup=kb("📱 Моя подписка"))
        await mw(make_request, None, m2)
        assert len(calls) == 1 and "<tg-emoji" not in calls[0][0]
    finally:
        emoji._runtime_off = False
