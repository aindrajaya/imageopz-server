"""
Payment event application (ADR-001, ADR-004).

apply_lemonsqueezy_event is the single writer of orders / users / users_quota for payment events. It is used by
the public signed webhook route and by the internal events route. Each event runs in ONE transaction: the dedupe
row and every effect commit together or not at all.
"""

import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    CheckoutIntent,
    CheckoutIntentStatus,
    Job,
    Order,
    OrderStatus,
    ProcessedPaymentEvent,
    User,
    UsersQuota,
)

logger = logging.getLogger("imageopz.services.payments")

PROVIDER = "lemonsqueezy"
PAID_EVENTS = {"order_created", "order:created"}
REFUND_EVENTS = {"order_refunded"}


HANDLED_EVENTS = {"order_created", "order_refunded"}  # after canonicalisation


@dataclass
class EventResult:
    outcome: str  # "processed" | "duplicate" | "ignored"
    activated: bool = False
    user_quota_id: Optional[str] = None
    reason: Optional[str] = None


class _Unprocessable(Exception):
    """An authentic event that cannot be applied (not retriable); becomes outcome "ignored"."""

    def __init__(self, reason: str, event_name: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.event_name = event_name


def _canonical_event_name(name: str) -> str:
    return "order_created" if name in PAID_EVENTS else name


def _opt_dict(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError
    return value


def _identify(data) -> tuple:
    """Return (event_name, object_type, object_id) or raise _Unprocessable with a reason code."""
    if not isinstance(data, dict):
        raise _Unprocessable("not_an_object")
    event_name = None
    try:
        meta = _opt_dict(data.get("meta"))
        event_data = _opt_dict(data.get("data"))
        event_name = data.get("event_name") or meta.get("event_name")
        attributes = _opt_dict(event_data.get("attributes"))
        custom = _opt_dict(meta.get("custom_data"))
        if event_name is not None and not isinstance(event_name, str):
            raise TypeError
        for value in (
            attributes.get("user_email"), attributes.get("customer_email"), attributes.get("email"),
            custom.get("email"), custom.get("ip_hash"), custom.get("session_id"),
        ):
            if value is not None and not isinstance(value, str):
                raise TypeError
        if custom.get("supabase_user_id") is not None and not isinstance(custom["supabase_user_id"], (str, int)):
            raise TypeError
        object_id = event_data.get("id")
        if object_id is not None and not isinstance(object_id, (str, int)):
            raise TypeError
        object_type = event_data.get("type")
        if object_type is not None and not isinstance(object_type, str):
            raise TypeError
    except TypeError:
        raise _Unprocessable("bad_shape", event_name if isinstance(event_name, str) else None)
    if not event_name:
        raise _Unprocessable("missing_event_name")
    if object_id is None or str(object_id) == "":
        raise _Unprocessable("missing_object_id", event_name)
    if _canonical_event_name(event_name) not in HANDLED_EVENTS:
        raise _Unprocessable("unhandled_event", event_name)
    return event_name, str(object_type or "unknown"), str(object_id)


def _ignored(reason: str, event_name: Optional[str]) -> EventResult:
    # Never log the payload or customer data; event_name is attacker-influenced after auth, so truncate it.
    logger.warning("Payment event ignored: event_name=%s reason=%s", (event_name or "-")[:64], reason)
    return EventResult(outcome="ignored", reason=reason)


def apply_lemonsqueezy_body(session: Session, body: bytes) -> EventResult:
    """Decode and apply a raw webhook body (called after authentication). Undecodable input is ignored."""
    try:
        text = body.decode("utf-8")
        data = json.loads(text)
    except ValueError:  # UnicodeDecodeError and JSONDecodeError
        return _ignored("invalid_json", None)
    return apply_lemonsqueezy_event(session, data, text)


def _claim_event(session: Session, event_name: str, object_type: str, object_id: str) -> bool:
    """
    First write of the transaction: INSERT ... ON CONFLICT DO NOTHING RETURNING. A returned row means this
    delivery owns the event; no row means it was already processed (or is being processed by a concurrent
    transaction that has not committed yet: Postgres makes this insert wait for it).
    """
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    table = ProcessedPaymentEvent.__table__
    stmt = (
        insert(table)
        .values(
            id=str(uuid.uuid4()),
            provider=PROVIDER,
            event_name=event_name,
            object_type=object_type,
            object_id=object_id,
            processed_at=datetime.utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["provider", "event_name", "object_type", "object_id"])
        .returning(table.c.id)
    )
    return session.execute(stmt).first() is not None


def apply_lemonsqueezy_event(session: Session, payload: dict, raw_body: Optional[str] = None) -> EventResult:
    """
    Apply one LemonSqueezy event exactly once. Commits on success; rolls back and re-raises on any failure.
    Redelivery of the same (event_name, data.type, data.id) returns outcome "duplicate" with no state change.
    An event that cannot be applied returns outcome "ignored" before any write.
    """
    try:
        event_name, object_type, object_id = _identify(payload)
    except _Unprocessable as exc:
        return _ignored(exc.reason, exc.event_name)
    raw = raw_body if raw_body is not None else json.dumps(payload)
    try:
        if not _claim_event(session, _canonical_event_name(event_name), object_type, object_id):
            session.rollback()
            logger.info("Duplicate payment event ignored: %s %s %s", event_name, object_type, object_id)
            return EventResult(outcome="duplicate")

        result = _apply(session, payload, event_name, object_id, raw)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def _apply(session: Session, data: dict, event_name: str, external_id: str, raw: str) -> EventResult:
    meta = data.get("meta") or {}
    event_data = data.get("data") or {}
    attributes = event_data.get("attributes") or {}
    user_email = (
        attributes.get("user_email")
        or attributes.get("customer_email")
        or attributes.get("email")
        or (meta.get("custom_data") or {}).get("email")
    )
    is_paid_event = event_name in PAID_EVENTS
    is_refund_event = event_name in REFUND_EVENTS

    order = session.query(Order).filter_by(provider=PROVIDER, external_id=external_id).first()
    activate = False
    if order is None:
        order = Order(
            provider=PROVIDER,
            external_id=external_id,
            event_name=event_name,
            email=user_email,
            raw_payload_json=raw,
            status=OrderStatus.PAID.value if is_paid_event else OrderStatus.REFUNDED.value,
        )
        session.add(order)
        activate = is_paid_event
    elif is_paid_event and order.status not in (OrderStatus.PAID.value, OrderStatus.REFUNDED.value):
        # Placeholder (unknown/created/cancelled) order becomes paid. A paid or refunded order never re-activates.
        order.event_name = event_name
        order.email = user_email
        order.raw_payload_json = raw
        order.status = OrderStatus.PAID.value
        activate = True
    elif is_refund_event and order.status != OrderStatus.REFUNDED.value:
        order.event_name = event_name
        order.status = OrderStatus.REFUNDED.value
    # Only order_created / order_refunded reach here; an existing paid/refunded order is never downgraded (R3).

    if is_refund_event:
        session.flush()
        _revoke_for_refund(session, order)
        return EventResult(outcome="processed")

    if not activate:
        return EventResult(outcome="processed")

    quota = _activate(session, order, data, meta, user_email, external_id)
    return EventResult(outcome="processed", activated=True, user_quota_id=quota.id)


def _revoke_for_refund(session: Session, order: Order) -> None:
    """
    Revoke the Pro entitlement granted by this order from every quota row that carries it, unless another paid
    order still backs it. Rule: the rows are the quota the order activated (orders.user_quota_id) plus every quota
    linked to the order's user (users_quota.user_id, which restore and session/me rehydration set). Never matched
    by ip_hash or session_id alone. Paid checkout intents of the order stop being eligible for rehydration.
    """
    session.query(CheckoutIntent).filter(
        CheckoutIntent.provider == PROVIDER,
        CheckoutIntent.order_external_id == order.external_id,
        CheckoutIntent.status == CheckoutIntentStatus.PAID.value,
    ).update({CheckoutIntent.status: CheckoutIntentStatus.REFUNDED.value}, synchronize_session=False)

    if not order.user_quota_id and not order.user_id:
        return
    backing = []
    if order.user_quota_id:
        backing.append(Order.user_quota_id == order.user_quota_id)
    if order.user_id:
        backing.append(Order.user_id == order.user_id)
    other_paid = (
        session.query(Order)
        .filter(Order.status == OrderStatus.PAID.value, Order.id != order.id, or_(*backing))
        .count()
    )
    if other_paid:
        return
    carriers = []
    if order.user_quota_id:
        carriers.append(UsersQuota.id == order.user_quota_id)
    if order.user_id:
        carriers.append(UsersQuota.user_id == order.user_id)
    for quota in session.query(UsersQuota).filter(or_(*carriers)).all():
        quota.is_pro = False
        quota.pro_verified = False
        quota.pro_expiry_date = None


def _extend_job_expiry(session: Session, user_quota_id: str, pro_expiry: datetime) -> None:
    session.query(Job).filter(
        Job.user_quota_id == user_quota_id,
        Job.deleted_at == None,  # noqa: E711
    ).update({Job.expiry_date: pro_expiry}, synchronize_session=False)


@contextmanager
def _best_effort(session: Session, what: str):
    """
    Run an optional step inside a SAVEPOINT. A failure is logged and rolled back to the savepoint only, so the
    enclosing transaction stays usable (on Postgres an unguarded failed statement aborts the whole transaction and
    a later commit() would silently roll back the event while reporting success).
    """
    try:
        with session.begin_nested():
            yield
    except Exception:
        logger.exception("Best-effort step failed, continuing without it: %s", what)


def _activate(session: Session, order: Order, data: dict, meta: dict, user_email, external_id: str) -> UsersQuota:
    # Best-effort mapping back to an existing free-tier user record.
    # Recommended: include `meta.custom_data.ip_hash` or `meta.custom_data.session_id`
    # when creating the LemonSqueezy checkout.
    custom = meta.get("custom_data", {}) or {}
    ip_hash = custom.get("ip_hash")
    session_id = custom.get("session_id")
    supabase_user_id = custom.get("supabase_user_id")

    pro_expiry = datetime.utcnow() + timedelta(days=settings.pro_tier_expiry_days)
    user = None
    if user_email:
        user = session.query(User).filter_by(email=user_email).first()
        if not user:
            user = User(email=user_email)
            session.add(user)
            session.flush()

    # Link Supabase user id if present (idempotent). On conflict, log and continue with email-based flow.
    if user and supabase_user_id:
        with _best_effort(session, "link supabase_user_id"):
            existing = session.query(User).filter_by(supabase_user_id=str(supabase_user_id)).first()
            if existing and existing.id != user.id:
                logger.warning(
                    "Supabase user id already linked to another user; skipping link. supabase_user_id=%s user_email=%s",
                    supabase_user_id,
                    user_email,
                )
            else:
                user.supabase_user_id = str(supabase_user_id)

    # Link order to user if possible
    if user:
        order.user_id = user.id

    # Create/update quota row for this paid user.
    # Priority:
    # 1) user_id (email-linked user)
    # 2) session_id (durable guest identifier)
    # ip_hash is last-resort and should not be used to grant Pro long-term.
    user_quota = None
    if user:
        user_quota = session.query(UsersQuota).filter_by(user_id=user.id).first()
    if not user_quota and session_id:
        user_quota = session.query(UsersQuota).filter_by(session_id=session_id).first()
    # ip_hash last-resort: only if a recent checkout intent exists (activation window).
    if not user_quota and ip_hash:
        with _best_effort(session, "ip_hash intent lookup"):
            now = datetime.utcnow()
            recent_intent = (
                session.query(CheckoutIntent)
                .filter(
                    CheckoutIntent.provider == PROVIDER,
                    CheckoutIntent.status == CheckoutIntentStatus.CREATED.value,
                    CheckoutIntent.expires_at != None,  # noqa: E711
                    CheckoutIntent.expires_at > now,
                    CheckoutIntent.ip_hash == ip_hash,
                )
                .order_by(CheckoutIntent.created_at.desc())
                .first()
            )
            if recent_intent:
                # Prefer binding by session_id from the intent if available.
                if getattr(recent_intent, "session_id", None):
                    user_quota = (
                        session.query(UsersQuota)
                        .filter_by(session_id=recent_intent.session_id)
                        .order_by(UsersQuota.updated_at.desc())
                        .first()
                    )
                # If we still cannot find a quota row, do NOT grant Pro purely by ip_hash.

    if not user_quota:
        user_quota = UsersQuota(
            id=str(uuid.uuid4()),
            user_id=user.id if user else None,
            ip_hash=ip_hash,
            session_id=session_id,
            is_pro=True,
            pro_expiry_date=pro_expiry,
            pro_credits_used=0,
            pro_verified=True,
            pro_source="webhook_session" if session_id else "unknown",
        )
        session.add(user_quota)
    else:
        if user and not user_quota.user_id:
            user_quota.user_id = user.id
        user_quota.is_pro = True
        user_quota.pro_expiry_date = pro_expiry
        # Reset pro credits window on successful purchase
        user_quota.pro_credits_used = 0
        user_quota.pro_verified = True
        user_quota.pro_source = "webhook_session" if session_id else "unknown"
    session.flush()
    order.user_quota_id = user_quota.id

    # Mark checkout intent as paid (best-effort). This helps activation UX and session rehydration.
    with _best_effort(session, "mark checkout intents paid"):
        now = datetime.utcnow()
        q_intent = session.query(CheckoutIntent).filter(
            CheckoutIntent.provider == PROVIDER,
            CheckoutIntent.status == CheckoutIntentStatus.CREATED.value,
        )
        matched = False
        if session_id:
            for it in q_intent.filter(CheckoutIntent.session_id == session_id).all():
                it.status = CheckoutIntentStatus.PAID.value
                it.paid_at = now
                it.order_external_id = external_id
                matched = True
        if (not matched) and ip_hash:
            for it in q_intent.filter(CheckoutIntent.ip_hash == ip_hash).all():
                it.status = CheckoutIntentStatus.PAID.value
                it.paid_at = now
                it.order_external_id = external_id
                matched = True
        if (not matched) and user_email:
            for it in q_intent.filter(CheckoutIntent.email == user_email).all():
                it.status = CheckoutIntentStatus.PAID.value
                it.paid_at = now
                it.order_external_id = external_id

    # Extend expiry window for existing jobs for this user (best-effort)
    with _best_effort(session, "extend job expiry"):
        _extend_job_expiry(session, user_quota.id, pro_expiry)

    return user_quota
