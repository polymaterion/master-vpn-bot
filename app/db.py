"""БД: SQLAlchemy 2 (async). В проде PostgreSQL, в тестах SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import (JSON, BigInteger, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint,
                        and_, func, or_, select, update)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .utils import utcnow

BigPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    nav: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # метка из ?start=<метка>


class PaymentRow(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("provider", "provider_ref", name="uq_payment_provider_ref"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    provider: Mapped[str] = mapped_column(String(16))            # stars | cryptopay | platega
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(8))                 # buy | renew
    months: Mapped[int] = mapped_column(Integer)
    devices: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))             # STARS | USDT | RUB
    # created -> paid -> fulfilling -> fulfilled | failed -> refunded; created -> expired
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    idem_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PendingOrderRow(Base):
    __tablename__ = "pending_orders"
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_try_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WebhookEventRow(Base):
    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    seq: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdminAuditRow(Base):
    __tablename__ = "admin_audit"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    details: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReminderRow(Base):
    """Напоминание «скоро конец» отправляется один раз на один срок (tg_id + expires_at)."""
    __tablename__ = "reminders"
    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    expires_at: Mapped[str] = mapped_column(String(40), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def _cut(value: str | None, n: int) -> str | None:
    return value[:n] if value else None


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:  # только для тестов; в проде – Alembic
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.sessions()

    # --- пользователи и навигация ---------------------------------------------
    async def get_nav(self, tg_id: int) -> list[dict]:
        async with self.session() as s:
            row = await s.get(UserRow, tg_id)
            return list(row.nav or []) if row else []

    async def set_nav(self, tg_id: int, nav: list[dict]) -> None:
        async with self.session() as s, s.begin():
            row = await s.get(UserRow, tg_id)
            if row is None:
                s.add(UserRow(tg_id=tg_id, nav=nav))
            else:
                row.nav = nav

    async def register_user(self, tg_id: int, username: str | None, first_name: str | None,
                            last_name: str | None, source: str | None) -> bool:
        """Вызывается на /start. True – пользователь новый. Источник пишется только при первом заходе."""
        username, first_name, last_name = (username or None), _cut(first_name, 256), _cut(last_name, 256)
        try:
            async with self.session() as s, s.begin():
                row = await s.get(UserRow, tg_id)
                if row is None:
                    s.add(UserRow(tg_id=tg_id, nav=[], username=username, first_name=first_name,
                                  last_name=last_name, source=source))
                    return True
                row.username, row.first_name, row.last_name = username, first_name, last_name
                return False
        except IntegrityError:  # гонка двух одновременных /start
            return False

    async def touch_profile(self, tg_id: int, username: str | None, first_name: str | None,
                            last_name: str | None) -> None:
        """Обновляет имя и юзернейм уже известного пользователя; новых не создаёт."""
        async with self.session() as s, s.begin():
            await s.execute(update(UserRow).where(UserRow.tg_id == tg_id).values(
                username=username or None, first_name=_cut(first_name, 256), last_name=_cut(last_name, 256)))

    async def paid_user_ids(self) -> set[int]:
        async with self.session() as s:
            res = await s.execute(select(PaymentRow.tg_id).where(PaymentRow.status == "fulfilled").distinct())
            return set(res.scalars())

    async def list_users(self) -> list[UserRow]:
        async with self.session() as s:
            return list((await s.execute(select(UserRow).order_by(UserRow.created_at, UserRow.tg_id))).scalars())

    async def source_stats(self, since: datetime | None = None) -> list[dict]:
        """По источникам: сколько пользователей пришло и сколько из них хоть раз оплатили."""
        async with self.session() as s:
            q = select(UserRow.tg_id, UserRow.source)
            if since is not None:
                q = q.where(UserRow.created_at >= since)
            users = (await s.execute(q)).all()
        paid = await self.paid_user_ids()
        acc: dict[str | None, list[int]] = {}
        for tg_id, source in users:
            row = acc.setdefault(source, [0, 0])
            row[0] += 1
            row[1] += tg_id in paid
        out = [{"source": k, "users": v[0], "paid": v[1]} for k, v in acc.items()]
        return sorted(out, key=lambda r: (-r["users"], r["source"] or ""))

    # --- платежи ---------------------------------------------------------------
    async def add_payment(self, row: PaymentRow) -> PaymentRow:
        async with self.session() as s, s.begin():
            s.add(row)
        return row

    async def get_payment(self, payment_id: str) -> PaymentRow | None:
        async with self.session() as s:
            return await s.get(PaymentRow, payment_id)

    async def payment_by_ref(self, provider: str, ref: str) -> PaymentRow | None:
        async with self.session() as s:
            return (await s.execute(select(PaymentRow).where(
                PaymentRow.provider == provider, PaymentRow.provider_ref == ref))).scalar_one_or_none()

    async def open_payments(self, tg_id: int, provider: str) -> list[PaymentRow]:
        async with self.session() as s:
            res = await s.execute(select(PaymentRow).where(
                PaymentRow.tg_id == tg_id, PaymentRow.provider == provider, PaymentRow.status == "created"
            ).order_by(PaymentRow.created_at.desc()))
            return list(res.scalars())

    async def open_provider_payments(self, provider: str, since: datetime) -> list[PaymentRow]:
        async with self.session() as s:
            res = await s.execute(select(PaymentRow).where(
                PaymentRow.provider == provider, PaymentRow.status == "created", PaymentRow.created_at >= since
            ).order_by(PaymentRow.created_at))
            return list(res.scalars())

    async def transition(self, payment_id: str, from_statuses: Sequence[str], to_status: str,
                         **fields: Any) -> PaymentRow | None:
        """Атомарный переход статуса; None – платёж уже в другом статусе (повторное событие)."""
        async with self.session() as s, s.begin():
            res = await s.execute(update(PaymentRow).where(
                PaymentRow.id == payment_id, PaymentRow.status.in_(list(from_statuses))
            ).values(status=to_status, updated_at=utcnow(), **fields))
            if res.rowcount != 1:
                return None
            return await s.get(PaymentRow, payment_id, populate_existing=True)

    async def set_fields(self, payment_id: str, **fields: Any) -> None:
        async with self.session() as s, s.begin():
            await s.execute(update(PaymentRow).where(PaymentRow.id == payment_id).values(updated_at=utcnow(), **fields))

    async def has_paid(self, tg_id: int) -> bool:
        async with self.session() as s:
            n = (await s.execute(select(func.count()).select_from(PaymentRow).where(
                PaymentRow.tg_id == tg_id, PaymentRow.status == "fulfilled"))).scalar_one()
            return n > 0

    async def unfinished(self, limit: int = 20) -> list[PaymentRow]:
        async with self.session() as s:
            res = await s.execute(select(PaymentRow).where(
                PaymentRow.status.in_(["paid", "fulfilling", "failed"])
            ).order_by(PaymentRow.updated_at.desc()).limit(limit))
            return list(res.scalars())

    # --- очередь выдачи --------------------------------------------------------
    async def bump_pending(self, payment_id: str, error: str) -> int:
        async with self.session() as s, s.begin():
            row = await s.get(PendingOrderRow, payment_id)
            if row is None:
                row = PendingOrderRow(payment_id=payment_id, attempts=0)
                s.add(row)
            row.attempts = (row.attempts or 0) + 1
            row.last_error = error
            row.next_try_at = utcnow() + timedelta(seconds=min(60 * row.attempts, 600))
            return row.attempts

    async def drop_pending(self, payment_id: str) -> None:
        async with self.session() as s, s.begin():
            row = await s.get(PendingOrderRow, payment_id)
            if row is not None:
                await s.delete(row)

    async def due_payments(self) -> list[str]:
        now = utcnow()
        async with self.session() as s:
            res = await s.execute(
                select(PaymentRow.id).outerjoin(PendingOrderRow, PendingOrderRow.payment_id == PaymentRow.id).where(
                    PaymentRow.status == "paid",
                    or_(PendingOrderRow.next_try_at.is_(None), PendingOrderRow.next_try_at <= now)))
            return list(res.scalars())

    async def release_stuck(self, older_than: timedelta) -> int:
        """Выдача, зависшая в «fulfilling» после падения процесса, возвращается в очередь."""
        async with self.session() as s, s.begin():
            res = await s.execute(update(PaymentRow).where(
                PaymentRow.status == "fulfilling", PaymentRow.updated_at < utcnow() - older_than
            ).values(status="paid", updated_at=utcnow()))
            return res.rowcount or 0

    # --- события, напоминания, аудит -----------------------------------------
    async def remember_event(self, event_id: str, type_: str, seq: str) -> bool:
        """True – событие новое, False – уже обрабатывали (повтор доставки)."""
        try:
            async with self.session() as s, s.begin():
                s.add(WebhookEventRow(id=event_id, type=type_, seq=seq))
            return True
        except IntegrityError:
            return False

    async def remember_reminder(self, tg_id: int, expires_at: str) -> bool:
        try:
            async with self.session() as s, s.begin():
                s.add(ReminderRow(tg_id=tg_id, expires_at=expires_at))
            return True
        except IntegrityError:
            return False

    async def audit(self, admin_id: int, action: str, target: str = "", details: str = "") -> None:
        async with self.session() as s, s.begin():
            s.add(AdminAuditRow(admin_id=admin_id, action=action, target=target, details=details[:1000]))
