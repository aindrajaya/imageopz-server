"""
Characterization tests for POST /api/v1/webhooks/lemonsqueezy.

These pin CURRENT behavior (main@bee0a98), including known gaps, so that later
changes (T-002 fixes, Go ingress) are deliberate and visible. A failing test here
means behavior changed; it is not necessarily a bug.

The test DB is session-scoped and shared across tests, so every test uses unique
emails / order ids / session ids.
"""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta

import pytest

SECRET = "test-lemonsqueezy-secret"
URL = "/api/v1/webhooks/lemonsqueezy"
PRO_DAYS = 40  # settings.pro_tier_expiry_days default


# ---------- helpers ----------

def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _sign(body: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def _payload(event_name="order_created", order_id=None, email=None, custom=None, meta_event=True):
    """Build a LemonSqueezy-style payload. Returns (dict, order_id)."""
    order_id = order_id or _uid("order")
    meta = {"custom_data": custom or {}}
    attrs = {}
    if email:
        attrs["user_email"] = email
    obj = {"meta": meta, "data": {"id": order_id, "attributes": attrs}}
    if meta_event:
        meta["event_name"] = event_name
    else:
        obj["event_name"] = event_name
    return obj, order_id


def _post(client, obj, signature="auto", raw=None):
    body = raw if raw is not None else json.dumps(obj)
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers["X-Signature"] = _sign(body)
    elif signature is not None:
        headers["X-Signature"] = signature
    return client.post(URL, content=body, headers=headers)


def _session():
    from app.models import db
    return db.get_session()


def _quota_count(s):
    from app.models import UsersQuota
    return s.query(UsersQuota).count()


# ---------- 1. signature ----------

def test_valid_signature_returns_200(client):
    obj, _ = _payload("some_other_event")
    assert _post(client, obj).status_code == 200


def test_invalid_signature_returns_401(client):
    obj, order_id = _payload(email=f"{_uid('u')}@example.com")
    res = _post(client, obj, signature="0" * 64)
    assert res.status_code == 401
    s = _session()
    try:
        from app.models import Order
        assert s.query(Order).filter_by(external_id=order_id).first() is None
    finally:
        s.close()


def test_missing_signature_header_returns_401(client):
    obj, _ = _payload()
    assert _post(client, obj, signature=None).status_code == 401


def test_unset_secret_returns_401_even_with_correct_looking_signature(client, monkeypatch):
    from app.utils import security
    monkeypatch.setattr(security.settings, "lemonsqueezy_secret", None)
    obj, _ = _payload()
    assert _post(client, obj).status_code == 401


# ---------- 2/3. order_created activation ----------

@pytest.mark.parametrize("event_name", ["order_created", "order:created"])
def test_order_created_activates_pro_and_writes_all_tables(client, event_name):
    from app.models import (
        CheckoutIntent, CheckoutIntentStatus, Job, JobStatus, Order, OrderStatus, User, UsersQuota,
    )

    email = f"{_uid('buyer')}@example.com"
    session_id = _uid("sess")
    old_expiry = datetime.utcnow() + timedelta(days=1)

    # Given: an existing free quota for the session, a live job, and a created checkout intent
    s = _session()
    try:
        quota = UsersQuota(session_id=session_id, is_pro=False, credits_used=2, pro_credits_used=7)
        s.add(quota)
        s.flush()
        quota_id = quota.id
        job = Job(session_id=session_id, user_quota_id=quota_id, status=JobStatus.DONE, expiry_date=old_expiry)
        s.add(job)
        intent = CheckoutIntent(
            provider="lemonsqueezy", checkout_id=_uid("chk"), email=email, session_id=session_id,
            status="created", expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        s.add(intent)
        s.commit()
        job_id, intent_id = job.id, intent.id
    finally:
        s.close()

    # When: order_created arrives
    before = datetime.utcnow()
    obj, order_id = _payload(event_name, email=email, custom={"session_id": session_id})
    res = _post(client, obj)

    # Then
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["message"] == "Pro activated"
    assert body["access_token"]  # current contract (3)

    s = _session()
    try:
        order = s.query(Order).filter_by(provider="lemonsqueezy", external_id=order_id).one()
        user = s.query(User).filter_by(email=email).one()
        assert order.status == OrderStatus.PAID.value
        assert order.event_name == event_name
        assert order.email == email
        assert order.user_id == user.id
        assert json.loads(order.raw_payload_json)["data"]["id"] == order_id

        q = s.query(UsersQuota).filter_by(id=quota_id).one()
        assert q.user_id == user.id            # linked to the email user
        assert q.is_pro is True
        assert q.pro_verified is True
        assert q.pro_source == "webhook_session"
        assert q.pro_credits_used == 0         # reset from 7
        assert q.credits_used == 2             # free credits untouched
        assert before + timedelta(days=PRO_DAYS) - timedelta(seconds=5) <= q.pro_expiry_date
        assert q.pro_expiry_date <= datetime.utcnow() + timedelta(days=PRO_DAYS)

        it = s.query(CheckoutIntent).filter_by(id=intent_id).one()
        assert it.status == CheckoutIntentStatus.PAID.value
        assert it.order_external_id == order_id
        assert it.paid_at is not None

        j = s.query(Job).filter_by(id=job_id).one()
        assert j.expiry_date == q.pro_expiry_date  # extended to pro expiry
    finally:
        s.close()


# ---------- 4. replay ----------

def test_replay_order_created_is_single_activation(client):
    # T-004 R1: a redelivered order_created is a no-op (was: re-activated, reset credits, extended expiry).
    from app.models import Order, UsersQuota

    email = f"{_uid('replay')}@example.com"
    session_id = _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": session_id})

    assert _post(client, obj).status_code == 200
    s = _session()
    try:
        q = s.query(UsersQuota).filter_by(session_id=session_id).one()
        first_expiry = q.pro_expiry_date
        q.pro_credits_used = 9  # simulate usage between deliveries
        s.commit()
    finally:
        s.close()

    assert _post(client, obj).status_code == 200  # same order id, redelivered

    s = _session()
    try:
        assert s.query(Order).filter_by(external_id=order_id).count() == 1  # unique: one order row
        q = s.query(UsersQuota).filter_by(session_id=session_id).one()
        assert s.query(UsersQuota).filter_by(session_id=session_id).count() == 1
        assert q.pro_credits_used == 9              # credits NOT reset
        assert q.pro_expiry_date == first_expiry    # expiry NOT extended
    finally:
        s.close()


# ---------- 5. unknown event ----------

def test_unknown_event_is_ignored_and_writes_nothing(client):
    # T-004c: an authentic but unhandled event returns 200 {"status": "ignored"} with no DB writes
    # (was: stored an order with status "unknown" and answered "Webhook processed").
    from app.models import Order, ProcessedPaymentEvent

    s = _session()
    try:
        quota_rows_before = _quota_count(s)
    finally:
        s.close()

    obj, order_id = _payload("subscription_created", email=f"{_uid('x')}@example.com")
    res = _post(client, obj)

    assert res.status_code == 200
    assert res.json() == {"status": "ignored"}
    s = _session()
    try:
        assert s.query(Order).filter_by(external_id=order_id).first() is None
        assert s.query(ProcessedPaymentEvent).filter_by(object_id=order_id).first() is None
        assert _quota_count(s) == quota_rows_before
    finally:
        s.close()


# ---------- 6. refund ----------

def test_refund_event_revokes_pro_and_marks_order_refunded(client):
    # T-004 R2: refund revokes the entitlement and sets status "refunded" (was: Pro kept, status "unknown").
    from app.models import Order, OrderStatus, UsersQuota

    email = f"{_uid('refund')}@example.com"
    session_id = _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": session_id})
    assert _post(client, obj).status_code == 200

    refund, _ = _payload("order_refunded", order_id=order_id, email=email, custom={"session_id": session_id})
    res = _post(client, refund)

    assert res.status_code == 200
    s = _session()
    try:
        q = s.query(UsersQuota).filter_by(session_id=session_id).one()
        assert q.is_pro is False
        assert q.pro_verified is False
        order = s.query(Order).filter_by(external_id=order_id).one()
        assert order.event_name == "order_refunded"
        assert order.status == OrderStatus.REFUNDED.value
    finally:
        s.close()


# ---------- 7. ip_hash-only ----------

def test_ip_hash_only_without_email_or_session_creates_pro_quota(client):
    from app.models import Order, User, UsersQuota

    ip_hash = _uid("iphash")
    users_before = None
    s = _session()
    try:
        users_before = s.query(User).count()
    finally:
        s.close()

    obj, order_id = _payload(custom={"ip_hash": ip_hash})  # no email, no session_id
    res = _post(client, obj)

    assert res.status_code == 200
    assert res.json()["access_token"]
    s = _session()
    try:
        q = s.query(UsersQuota).filter_by(ip_hash=ip_hash).one()
        assert q.user_id is None
        assert q.session_id is None
        assert q.is_pro is True
        assert q.pro_verified is True
        assert q.pro_source == "unknown"
        assert q.pro_credits_used == 0
        assert q.pro_expiry_date > datetime.utcnow() + timedelta(days=PRO_DAYS - 1)
        assert s.query(User).count() == users_before  # no user created without email
        assert s.query(Order).filter_by(external_id=order_id).one().user_id is None
    finally:
        s.close()


# ---------- 8. supabase_user_id conflict ----------

def test_supabase_user_id_conflict_is_logged_and_skipped_but_still_200(client, caplog):
    from app.models import User, UsersQuota

    sb_id = _uid("sb")
    owner_email = f"{_uid('owner')}@example.com"
    buyer_email = f"{_uid('buyer')}@example.com"
    session_id = _uid("sess")

    s = _session()
    try:
        s.add(User(email=owner_email, supabase_user_id=sb_id))
        s.commit()
    finally:
        s.close()

    obj, _ = _payload(email=buyer_email, custom={"supabase_user_id": sb_id, "session_id": session_id})
    with caplog.at_level(logging.WARNING, logger="imageopz.routes.optimization"):
        res = _post(client, obj)

    assert res.status_code == 200
    assert res.json()["message"] == "Pro activated"
    assert any("already linked to another user" in r.getMessage() for r in caplog.records)

    s = _session()
    try:
        buyer = s.query(User).filter_by(email=buyer_email).one()
        assert buyer.supabase_user_id is None  # link skipped
        assert s.query(User).filter_by(email=owner_email).one().supabase_user_id == sb_id
        assert s.query(UsersQuota).filter_by(session_id=session_id).one().is_pro is True  # email flow continues
    finally:
        s.close()


# ---------- 9. failure during activation ----------

def test_exception_during_activation_returns_500_and_leaves_no_partial_state(client, monkeypatch):
    """
    T-004 R4/R5: a failure mid-activation rolls back everything (was: Order committed as paid but not
    activated) and the 500 body carries no exception text (was: echoed).
    Induced without editing app/: an invalid pro_tier_expiry_days makes timedelta() raise.
    """
    from app.models import Order, ProcessedPaymentEvent, User, UsersQuota
    from app.services import payments

    email = f"{_uid('boom')}@example.com"
    session_id = _uid("sess")
    monkeypatch.setattr(payments.settings, "pro_tier_expiry_days", "not-an-int")

    obj, order_id = _payload(email=email, custom={"session_id": session_id})
    res = _post(client, obj)

    assert res.status_code == 500
    assert res.json() == {"detail": "Webhook processing failed"}  # generic body, no exception text
    assert "not-an-int" not in res.text

    s = _session()
    try:
        assert s.query(Order).filter_by(external_id=order_id).first() is None
        assert s.query(ProcessedPaymentEvent).filter_by(object_id=order_id).first() is None
        assert s.query(User).filter_by(email=email).first() is None
        assert s.query(UsersQuota).filter_by(session_id=session_id).first() is None
    finally:
        s.close()
