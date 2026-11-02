import pytest

from app.config import ConfigError, Settings
from tests.conftest import full_env


def test_full_config_ok():
    s = Settings.from_env(full_env())
    assert s.price("RUB", 3, 4) == 100 and str(s.price("USDT", 1, 1)) == "1.50"
    assert s.admin_ids == (1,)


def test_missing_prices_listed_by_name():
    env = full_env()
    del env["PRICE_STARS_6M_8D"]
    env["PRICE_USDT_1M_1D"] = "abc"
    with pytest.raises(ConfigError) as e:
        Settings.from_env(env)
    msg = str(e.value)
    assert "PRICE_STARS_6M_8D" in msg and "PRICE_USDT_1M_1D" in msg


def test_rub_price_must_be_integer():
    with pytest.raises(ConfigError):
        Settings.from_env(full_env(PRICE_RUB_1M_1D="99.5"))


def test_unfilled_links_detected():
    s = Settings.from_env(full_env(LINK_WINDOWS="https://example.com/CHANGE_ME"))
    assert "LINK_WINDOWS" in s.unfilled_links() and "SUPPORT_URL" not in s.unfilled_links()


def test_example_env_is_complete(tmp_path):
    import pathlib
    lines = pathlib.Path(".env.example").read_text().splitlines()
    env = dict(l.split("=", 1) for l in lines if "=" in l and not l.startswith("#"))
    Settings.from_env(env)  # .env.example должен проходить валидацию


def test_legacy_link_names_still_work_and_channel_is_gone():
    s = Settings.from_env(full_env(LINK_GOOGLE_PLAY="https://play", LINK_APP_STORE="https://store", LINK_APK="https://apk",
                                   LINK_CHANNEL="https://t.me/x"))
    assert (s.links["android"], s.links["ios"], s.links["windows"]) == ("https://play", "https://store", "https://apk")
    assert "channel" not in s.links
    new = Settings.from_env(full_env(LINK_ANDROID="https://a", LINK_GOOGLE_PLAY="https://old"))
    assert new.links["android"] == "https://a"             # новое имя важнее прежнего
