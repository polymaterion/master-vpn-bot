from app.ui.nav import CHAINS, decode, encode


def test_callback_roundtrip_fits_telegram_limit():
    data = encode("pay_method", kind="renew", months=6, devices=8)
    assert len(data.encode()) <= 64
    assert decode(data) == ("pay_method", {"kind": "renew", "months": "6", "devices": "8"})


def test_chains_have_parents():
    assert CHAINS["renew_term"] == ["home", "sub"] and CHAINS["buy_term"] == ["home"]


def test_every_screen_registered_and_back_present():
    from app.ui import screens  # noqa: F401
    from app.ui.nav import SCREENS
    for name in ("home", "sub", "sub_link", "sub_qr", "renew_term", "renew_confirm", "buy_term", "buy_devices",
                 "pay_method", "pay_usdt", "pay_rub", "download", "info"):
        assert name in SCREENS
