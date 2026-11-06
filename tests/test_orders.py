from app.partner.client import PartnerError


async def _paid_payment(shop, tg=7, kind="buy", devices=2, provider="stars"):
    p = await shop.orders.new_payment(tg_id=tg, provider=provider, kind=kind, months=1, devices=devices,
                                      amount=150, currency="STARS")
    return p


async def test_purchase_once_even_if_payment_reported_twice(shop):
    p = await _paid_payment(shop)
    assert await shop.orders.process_paid(p.id, "charge-1") == "fulfilled"
    assert await shop.orders.process_paid(p.id, "charge-1") == "duplicate"
    puts = [c for c in shop.partner.calls if c[0] == "PUT"]
    assert len(puts) == 1
    assert puts[0][4] == "stars-charge-1" and puts[0][5] == "10500"  # ключ из платежа, потолок из /quotes
    assert shop.notifier.events == [("ok", 7)]


async def test_insufficient_balance_waits_then_succeeds_with_same_key(shop):
    shop.partner.balance = 0
    p = await _paid_payment(shop)
    assert await shop.orders.process_paid(p.id, "c2") == "pending"
    assert shop.notifier.events == [("pending", 7, "insufficient_balance")]
    # повторный прогон очереди до пополнения тоже ничего не ломает и не шлёт повторное уведомление
    await shop.db.bump_pending(p.id, "x")
    shop.partner.balance = 10_000_000
    await shop.db.set_fields(p.id)  # no-op
    from datetime import timedelta
    from sqlalchemy import update
    from app.db import PendingOrderRow
    from app.utils import utcnow
    async with shop.db.session() as s, s.begin():
        await s.execute(update(PendingOrderRow).values(next_try_at=utcnow() - timedelta(seconds=1)))
    assert await shop.orders.retry_due() == 1
    row = await shop.db.get_payment(p.id)
    assert row.status == "fulfilled"
    keys = {c[4] for c in shop.partner.calls if c[0] == "PUT"}
    assert keys == {"stars-c2"}                     # тот же ключ на всех попытках
    assert [e[0] for e in shop.notifier.events] == ["pending", "ok"]


async def test_permanent_error_marks_failed_and_notifies(shop):
    shop.partner.fail = [PartnerError(409, {"code": "plan_exceeds_terms", "request_id": "req_1"})]
    p = await _paid_payment(shop)
    assert await shop.orders.process_paid(p.id, "c3") == "failed"
    row = await shop.db.get_payment(p.id)
    assert row.status == "failed" and row.error_code == "plan_exceeds_terms" and row.request_id == "req_1"
    assert shop.notifier.events == [("failed", 7, "plan_exceeds_terms")]
    # админ нажал «Повторить» – заказ уходит тем же ключом
    assert await shop.orders.admin_retry(p.id) == "fulfilled"
    assert {c[4] for c in shop.partner.calls if c[0] == "PUT"} == {"stars-c3"}


async def test_excess_devices_are_cleared_and_link_rotated_instead_of_failing(shop):
    shop.partner.device_list["tg-7"] = ["a", "b", "c"]            # было 3 устройства, покупает на 1
    p = await _paid_payment(shop, devices=1)
    assert await shop.orders.process_paid(p.id, "c9") == "fulfilled"
    assert shop.partner.device_list["tg-7"] == []                 # устройства освобождены
    assert shop.partner.rotated == ["tg-7"]                       # выпущена новая ссылка
    assert {c[4] for c in shop.partner.calls if c[0] == "PUT"} == {"stars-c9"}   # ключ заказа один
    assert shop.notifier.events == [("ok", 7)]


async def test_temporary_api_error_goes_to_queue(shop):
    shop.partner.fail = [PartnerError(503, {"code": "unavailable"})]
    p = await _paid_payment(shop)
    assert await shop.orders.process_paid(p.id, "c4") == "pending"
    assert (await shop.db.get_payment(p.id)).status == "paid"


async def test_renew_uses_extend_with_current_devices(shop):
    first = await _paid_payment(shop, devices=4)
    await shop.orders.process_paid(first.id, "buy-1")
    renew = await _paid_payment(shop, kind="renew", devices=4)
    assert await shop.orders.process_paid(renew.id, "renew-1") == "fulfilled"
    assert ("EXTEND", "tg-7", 30, "stars-renew-1") in shop.partner.calls


async def test_expired_local_payment_still_honoured_if_money_arrived(shop):
    p = await _paid_payment(shop, provider="cryptopay")
    await shop.db.transition(p.id, ("created",), "expired")
    assert await shop.orders.process_paid(p.id, "inv-9") == "fulfilled"
    assert {c[4] for c in shop.partner.calls} == {"cryptopay-inv-9"}


async def test_has_paid_only_after_fulfilment(shop):
    p = await _paid_payment(shop)
    assert not await shop.db.has_paid(7)
    await shop.orders.process_paid(p.id, "c5")
    assert await shop.db.has_paid(7)


async def test_catalog_is_refreshed_when_empty_before_delivery(db):
    """Бот стартовал на пробе (каталог пуст), проект перешёл на боевые условия: продаём ступень сетки, не тестовый тариф."""
    from app.services.orders import OrderService
    from app.services.plans import PlanCatalog
    from tests.conftest import FakeNotifier, FakePartner

    partner = FakePartner()
    plans = PlanCatalog(partner)  # type: ignore[arg-type]
    assert not plans.grid
    orders = OrderService(db, partner, plans, FakeNotifier())  # type: ignore[arg-type]
    p = await orders.new_payment(tg_id=9, provider="stars", kind="buy", months=1, devices=4, amount=1, currency="STARS")
    assert await orders.process_paid(p.id, "c9") == "fulfilled"
    assert partner.calls[0][2] == "plan-4"
