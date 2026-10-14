"""
T-004: idempotent activation service + internal events endpoint (ADR-001 R1-R5, ADR-004).
Shared DB across tests: every test uses unique ids.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime

import pytest

SECRET = "test-lemonsqueezy-secret"
PUBLIC = "/api/v1/webhooks/lemonsqueezy"
INTERNAL = "/internal/payments/events"
TOKEN = "internal-test-token"


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _payload(event="order_created", order_id=None, email=None, custom=None, obj_type="orders"):
    order_id = order_id or _uid("order")
    attrs = {"user_email": email} if email else {}
    return (
        {"meta": {"event_name": event, "custom_data": custom or {}},
         "data": {"type": obj_type, "id": order_id, "attributes": attrs}},
        order_id,
    )


def _public(client, obj):
    body = json.dumps(obj)
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return client.post(PUBLIC, content=body, headers={"X-Signature": sig, "Content-Type": "application/json"})


@pytest.fixture()
def internal(client, monkeypatch):
    from app.utils import internal_auth
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)

    def post(obj, token=TOKEN, raw=None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Internal-Token"] = token
        return client.post(INTERNAL, content=raw if raw is not None else json.dumps(obj), headers=headers)

    return post


def _db():
    from app.models import db
    return db.get_session()


def _snapshot(order_id, session_id=None):
    """Comparable DB state for one order (ignores generated ids and timestamps)."""
    from app.models import Order, ProcessedPaymentEvent, User, UsersQuota
    s = _db()
    try:
        o = s.query(Order).filter_by(external_id=order_id).one()
        q = s.query(UsersQuota).filter_by(id=o.user_quota_id).first()
        u = s.query(User).filter_by(id=o.user_id).first() if o.user_id else None
        return {
            "order": (o.event_name, o.status, o.email, o.raw_payload_json, o.user_id is not None),
            "quota": q and (q.is_pro, q.pro_verified, q.pro_source, q.pro_credits_used, q.session_id),
            "user": u and u.email,
            "events": sorted(
                (e.event_name, e.object_type) for e in s.query(ProcessedPaymentEvent).filter_by(object_id=order_id)
            ),
        }
    finally:
        s.close()


# ---------- AC1: replay across routes ----------

def test_replay_three_times_mixed_routes_is_one_activation(client, internal):
    from app.models import ProcessedPaymentEvent, UsersQuota
    email, sid = f"{_uid('r')}@example.com", _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": sid})

    assert _public(client, obj).json()["message"] == "Pro activated"
    s = _db()
    try:
        q = s.query(UsersQuota).filter_by(session_id=sid).one()
        first_expiry = q.pro_expiry_date
        q.pro_credits_used = 5
        s.commit()
    finally:
        s.close()

    r2 = internal(obj)
    r3 = _public(client, obj)
    assert r2.status_code == 200 and r2.json() == {"status": "duplicate"}
    assert r3.status_code == 200 and r3.json() == {"success": True, "message": "Duplicate event ignored"}

    s = _db()
    try:
        q = s.query(UsersQuota).filter_by(session_id=sid).one()
        assert q.pro_credits_used == 5 and q.pro_expiry_date == first_expiry
        assert s.query(ProcessedPaymentEvent).filter_by(object_id=order_id).count() == 1
    finally:
        s.close()


def test_order_colon_created_alias_does_not_double_activate(client):
    email, sid = f"{_uid('a')}@example.com", _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": sid})
    assert _public(client, obj).json()["message"] == "Pro activated"
    alias, _ = _payload("order:created", order_id=order_id, email=email, custom={"session_id": sid})
    assert _public(client, alias).json()["message"] == "Duplicate event ignored"


# ---------- AC2: refund ----------

def test_refund_revokes_and_replayed_created_does_not_reactivate(client, internal):
    from app.models import UsersQuota
    email, sid = f"{_uid('f')}@example.com", _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": sid})
    assert _public(client, obj).status_code == 200
    refund, _ = _payload("order_refunded", order_id=order_id, email=email)
    assert internal(refund).json() == {"status": "processed"}

    assert _public(client, obj).json()["message"] == "Duplicate event ignored"
    s = _db()
    try:
        q = s.query(UsersQuota).filter_by(session_id=sid).one()
        assert q.is_pro is False and q.pro_expiry_date is None
    finally:
        s.close()
    assert _snapshot(order_id)["order"][1] == "refunded"


def test_refund_keeps_pro_when_another_paid_order_backs_same_quota(client):
    from app.models import UsersQuota
    email, sid = f"{_uid('m')}@example.com", _uid("sess")
    o1, id1 = _payload(email=email, custom={"session_id": sid})
    o2, id2 = _payload(email=email, custom={"session_id": sid})
    assert _public(client, o1).status_code == 200 and _public(client, o2).status_code == 200
    assert _public(client, _payload("order_refunded", order_id=id1)[0]).status_code == 200
    s = _db()
    try:
        assert s.query(UsersQuota).filter_by(session_id=sid).one().is_pro is True
    finally:
        s.close()


def test_order_created_after_refund_does_not_activate(client):
    from app.models import UsersQuota
    sid = _uid("sess")
    refund, order_id = _payload("order_refunded", email=f"{_uid('o')}@example.com")
    assert _public(client, refund).status_code == 200
    created, _ = _payload(order_id=order_id, email=f"{_uid('o')}@example.com", custom={"session_id": sid})
    res = _public(client, created)
    assert res.status_code == 200 and "access_token" not in res.json()
    s = _db()
    try:
        assert s.query(UsersQuota).filter_by(session_id=sid).first() is None
    finally:
        s.close()
    assert _snapshot(order_id)["order"][1] == "refunded"


# ---------- AC3: unknown event keeps status ----------

def test_unknown_event_on_paid_order_keeps_paid(client):
    email, sid = f"{_uid('u')}@example.com", _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": sid})
    assert _public(client, obj).status_code == 200
    other, _ = _payload("order_updated_by_something", order_id=order_id)
    assert _public(client, other).status_code == 200
    snap = _snapshot(order_id)
    assert snap["order"][1] == "paid" and snap["order"][0] == "order_created"


# ---------- AC4: forced failure ----------

def test_failure_mid_activation_rolls_back_everything_on_both_routes(client, internal, monkeypatch):
    from app.models import Order, ProcessedPaymentEvent, User, UsersQuota
    from app.services import payments

    def boom(*a, **k):
        raise RuntimeError("secret-internal-detail")

    # Fails after the quota row was written, i.e. late in activation.
    monkeypatch.setattr(payments, "_activate", lambda s, order, *a, **k: (s.add(User(email="x@y.z")), s.flush(), boom())[2])
    email, sid = f"{_uid('b')}@example.com", _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": sid})
    for res in (_public(client, obj), internal(obj)):
        assert res.status_code == 500
        assert "secret-internal-detail" not in res.text
    s = _db()
    try:
        assert s.query(Order).filter_by(external_id=order_id).first() is None
        assert s.query(ProcessedPaymentEvent).filter_by(object_id=order_id).first() is None
        assert s.query(User).filter_by(email="x@y.z").first() is None
        assert s.query(UsersQuota).filter_by(session_id=sid).first() is None
    finally:
        s.close()
    # Not poisoned: the same event succeeds once the fault is gone.
    monkeypatch.undo()
    from app.utils import internal_auth
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)
    assert internal(obj).json() == {"status": "processed"}


# ---------- AC5: internal auth ----------

def test_internal_missing_or_wrong_token_is_401(internal):
    obj, order_id = _payload()
    assert internal(obj, token=None).status_code == 401
    assert internal(obj, token="wrong").status_code == 401
    s = _db()
    try:
        from app.models import Order
        assert s.query(Order).filter_by(external_id=order_id).first() is None
    finally:
        s.close()


@pytest.mark.parametrize("mode", [None, "", "bogus"])
def test_internal_mode_unset_or_unknown_refuses_everything(client, monkeypatch, mode):
    from app.utils import internal_auth
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", mode)
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)
    obj, _ = _payload()
    res = client.post(INTERNAL, json=obj, headers={"X-Internal-Token": TOKEN})
    assert res.status_code == 401


def test_internal_shared_secret_unset_refuses(client, monkeypatch):
    from app.utils import internal_auth
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", None)
    assert client.post(INTERNAL, json=_payload()[0], headers={"X-Internal-Token": ""}).status_code == 401


def test_internal_shared_secret_processes(internal):
    obj, _ = _payload(email=f"{_uid('s')}@example.com")
    assert internal(obj).json() == {"status": "processed"}


def _oidc(monkeypatch, claims=None, raises=None):
    from app.utils import internal_auth
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "oidc")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_audience", "https://api.example.test")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_allowed_emails", "Tasks@proj.iam.gserviceaccount.com, other@x.y")
    seen = {}

    def fake(token, audience):
        seen["audience"] = audience
        if raises or audience != "https://api.example.test":
            raise ValueError("bad audience")
        return claims

    monkeypatch.setattr(internal_auth, "_verify_oidc_token", fake)
    return seen


def _bearer(client, obj, token="tok"):
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return client.post(INTERNAL, json=obj, headers=headers)


def test_oidc_accepts_allowed_email(client, monkeypatch):
    seen = _oidc(monkeypatch, {"email": "tasks@proj.iam.gserviceaccount.com", "email_verified": True})
    obj, _ = _payload(email=f"{_uid('o')}@example.com")
    assert _bearer(client, obj).json() == {"status": "processed"}
    assert seen["audience"] == "https://api.example.test"


def test_oidc_rejects_wrong_email_unverified_bad_token_and_missing_bearer(client, monkeypatch):
    obj, _ = _payload()
    _oidc(monkeypatch, {"email": "evil@x.y", "email_verified": True})
    assert _bearer(client, obj).status_code == 401
    _oidc(monkeypatch, {"email": "other@x.y", "email_verified": False})
    assert _bearer(client, obj).status_code == 401
    _oidc(monkeypatch, raises=True)
    assert _bearer(client, obj).status_code == 401
    _oidc(monkeypatch, {"email": "other@x.y", "email_verified": True})
    assert _bearer(client, obj, token=None).status_code == 401
    assert client.post(INTERNAL, json=obj, headers={"X-Internal-Token": TOKEN}).status_code == 401


def test_oidc_wrong_audience_config_is_rejected(client, monkeypatch):
    from app.utils import internal_auth
    _oidc(monkeypatch, {"email": "other@x.y", "email_verified": True})
    monkeypatch.setattr(internal_auth.settings, "internal_auth_audience", "https://different.example.test")
    assert _bearer(client, _payload()[0]).status_code == 401


# ---------- T-004c: authentic but unprocessable events -> 200 ignored, no writes ----------

def _counts():
    from app.models import Order, ProcessedPaymentEvent, User, UsersQuota
    s = _db()
    try:
        return tuple(s.query(m).count() for m in (Order, ProcessedPaymentEvent, User, UsersQuota))
    finally:
        s.close()


def _both(client, internal, raw: bytes):
    """Deliver the same raw bytes through the signed public route and the internal route."""
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    pub = client.post(PUBLIC, content=raw, headers={"X-Signature": sig, "Content-Type": "application/json"})
    return pub, internal(None, raw=raw)


def _ignored_case(client, internal, raw: bytes, caplog):
    import logging
    before = _counts()
    with caplog.at_level(logging.WARNING, logger="imageopz.services.payments"):
        pub, internal_res = _both(client, internal, raw)
    for res in (pub, internal_res):
        assert res.status_code == 200 and res.json() == {"status": "ignored"}
    assert _counts() == before  # no database writes
    warnings = [r.getMessage() for r in caplog.records if "ignored" in r.getMessage()]
    assert len(warnings) == 2 and all("reason=" in w for w in warnings)
    return warnings


def test_ignored_missing_data_id(client, internal, caplog):
    raw = json.dumps({"meta": {"event_name": "order_created"}, "data": {"attributes": {"user_email": "pii@example.com"}}}).encode()
    warnings = _ignored_case(client, internal, raw, caplog)
    assert all("order_created" in w and "pii@example.com" not in w for w in warnings)


def test_ignored_missing_event_name(client, internal, caplog):
    _ignored_case(client, internal, json.dumps({"data": {"id": _uid("o")}}).encode(), caplog)


@pytest.mark.parametrize("body", [
    {"meta": {"event_name": "order_created"}, "data": {"id": "1", "attributes": ["x"]}},
    {"meta": {"event_name": "order_created", "custom_data": "nope"}, "data": {"id": "1"}},
    {"meta": {"event_name": "order_created"}, "data": {"id": "1", "attributes": {"user_email": 5}}},
    {"meta": [], "data": {}},
])
def test_ignored_wrong_payload_shape(client, internal, caplog, body):
    _ignored_case(client, internal, json.dumps(body).encode(), caplog)


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"", b'{"meta": {"event_name": "order_created"}, "data": {"id": "\xff"}}'])
def test_ignored_invalid_json_or_non_object(client, internal, caplog, raw):
    _ignored_case(client, internal, raw, caplog)


def test_ignored_invalid_utf8_body_with_valid_signature(client, internal, caplog):
    raw = b'{"meta": {"event_name": "order_created"}, "data": {"id": "\xff\xfe"}}'
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    _ignored_case(client, internal, raw, caplog)


def test_invalid_utf8_body_with_bad_signature_is_still_401(client):
    raw = b'{"data": {"id": "\xff"}}'
    res = client.post(PUBLIC, content=raw, headers={"X-Signature": "0" * 64})
    assert res.status_code == 401


def test_ignored_unknown_event_type_leaves_existing_order_status(client, internal, caplog):
    obj, order_id = _payload(email=f"{_uid('k')}@example.com", custom={"session_id": _uid("sess")})
    assert _public(client, obj).status_code == 200
    other, _ = _payload("subscription_updated", order_id=order_id)
    _ignored_case(client, internal, json.dumps(other).encode(), caplog)
    assert _snapshot(order_id)["order"][1] == "paid"


def test_database_unavailable_is_retriable_5xx_with_generic_body(client, internal, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services import payments

    def down(*a, **k):
        raise OperationalError("SELECT secret_table", {}, Exception("connection to server at 10.0.0.5 failed"))

    monkeypatch.setattr(payments, "_claim_event", down)
    obj, _ = _payload(email=f"{_uid('d')}@example.com")
    for res in (_public(client, obj), internal(obj)):
        assert res.status_code == 500
        assert "10.0.0.5" not in res.text and "secret_table" not in res.text
    assert _public(client, obj).json() == {"detail": "Webhook processing failed"}
    assert internal(obj).json() == {"detail": "Payment event failed"}


def test_auth_failures_still_401_for_unprocessable_bodies(client, internal):
    assert internal(None, raw="not json", token="wrong").status_code == 401
    assert internal(None, raw="not json", token=None).status_code == 401
    assert client.post(PUBLIC, content=b"not json", headers={"X-Signature": "bad"}).status_code == 401


# ---------- AC6: same state through both routes ----------

def test_public_and_internal_routes_yield_identical_state(client, internal):
    snaps = []
    for route in ("public", "internal"):
        email, sid = f"same_{route}_{uuid.uuid4().hex[:8]}@example.com", _uid("sess")
        # Same shape; ids/emails differ only by route tag, which is normalised below.
        obj, order_id = _payload(email=email, custom={"session_id": sid})
        (_public(client, obj) if route == "public" else internal(obj))
        snap = _snapshot(order_id)
        raw = json.loads(snap["order"][3])
        assert raw == obj  # raw body stored verbatim on both routes
        snaps.append({
            "order": (snap["order"][0], snap["order"][1], snap["order"][4]),
            "quota": snap["quota"][:4],
            "events": snap["events"],
        })
    assert snaps[0] == snaps[1]


# ---------- T-004b ----------

def test_refund_revokes_every_quota_row_carrying_the_entitlement(client):
    """R2: restore / session-me rehydration creates a second Pro quota row; refund must revoke both."""
    from app.models import UsersQuota
    email, sid1, sid2, sid3 = f"{_uid('rh')}@example.com", _uid("s1"), _uid("s2"), _uid("s3")
    obj, order_id = _payload(email=email, custom={"session_id": sid1})
    assert _public(client, obj).status_code == 200

    # Second session via /session/restore (creates a second quota row for the same user).
    res = client.post("/api/v1/session/restore", json={"email": email}, headers={"X-Session-Id": sid2})
    assert res.status_code == 200, res.text
    me = client.get("/api/v1/session/me", headers={"X-Session-Id": sid2}).json()
    assert me["is_pro"] is True

    s = _db()
    try:
        rows = s.query(UsersQuota).filter(UsersQuota.session_id.in_([sid1, sid2])).all()
        assert len(rows) == 2 and all(r.is_pro for r in rows)
    finally:
        s.close()

    assert _public(client, _payload("order_refunded", order_id=order_id)[0]).status_code == 200

    s = _db()
    try:
        rows = s.query(UsersQuota).filter(UsersQuota.session_id.in_([sid1, sid2])).all()
        assert len(rows) == 2 and not any(r.is_pro for r in rows)
    finally:
        s.close()
    assert client.get("/api/v1/session/me", headers={"X-Session-Id": sid2}).json()["is_pro"] is False
    assert client.get("/api/v1/session/me", headers={"X-Session-Id": sid1}).json()["is_pro"] is False
    assert client.post("/api/v1/session/restore", json={"email": email}, headers={"X-Session-Id": sid3}).status_code == 403


def test_refund_of_never_activated_order_changes_no_quota(client):
    from app.models import UsersQuota
    s = _db()
    try:
        before = s.query(UsersQuota).count()
    finally:
        s.close()
    refund, order_id = _payload("order_refunded", email=f"{_uid('n')}@example.com")
    assert _public(client, refund).status_code == 200
    assert _snapshot(order_id)["order"][1] == "refunded"
    s = _db()
    try:
        assert s.query(UsersQuota).count() == before
    finally:
        s.close()


def test_conflict_path_with_preinserted_event_row_is_a_pure_duplicate(client, internal):
    """The dedupe insert is the first write: a pre-existing row short-circuits with no other writes."""
    from sqlalchemy import text
    from app.models import Order, User, UsersQuota, db
    email, sid = f"{_uid('c')}@example.com", _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": sid})
    with db.engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO processed_payment_events (id, provider, event_name, object_type, object_id) "
            "VALUES (:id, 'lemonsqueezy', 'order_created', 'orders', :oid)"
        ), {"id": str(uuid.uuid4()), "oid": order_id})
    assert _public(client, obj).json() == {"success": True, "message": "Duplicate event ignored"}
    assert internal(obj).json() == {"status": "duplicate"}
    s = _db()
    try:
        assert s.query(Order).filter_by(external_id=order_id).first() is None
        assert s.query(User).filter_by(email=email).first() is None
        assert s.query(UsersQuota).filter_by(session_id=sid).first() is None
    finally:
        s.close()


def test_orders_user_quota_id_is_nullable_fk_set_null(client):
    import app.models as models
    col = models.Order.__table__.c.user_quota_id
    assert col.nullable is True
    fk = next(iter(col.foreign_keys))
    assert fk.column.table.name == "users_quota" and fk.ondelete == "SET NULL"
    assert fk.constraint.name == "fk_orders_user_quota_id_users_quota"
    if "sqlite" in str(models.db.engine.url):
        return
    from sqlalchemy import text
    with models.db.engine.connect() as conn:
        rule = conn.execute(text(
            "SELECT confdeltype FROM pg_constraint WHERE conname = 'fk_orders_user_quota_id_users_quota'"
        )).scalar_one()
    assert rule == "n"  # SET NULL


def test_failed_best_effort_sql_does_not_poison_the_transaction(client, monkeypatch):
    """
    Review M1: a real SQL error inside a best-effort step (aborts a Postgres transaction) must roll back to its
    savepoint only; the event still commits fully and the response matches what was persisted.
    """
    from sqlalchemy import text
    from app.models import Order, ProcessedPaymentEvent, UsersQuota
    from app.services import payments

    def bad_sql(session, quota_id, expiry):
        session.execute(text("SELECT * FROM table_that_does_not_exist"))

    monkeypatch.setattr(payments, "_extend_job_expiry", bad_sql)
    email, sid = f"{_uid('sp')}@example.com", _uid("sess")
    obj, order_id = _payload(email=email, custom={"session_id": sid})
    res = _public(client, obj)
    assert res.status_code == 200 and res.json()["message"] == "Pro activated"
    s = _db()
    try:
        assert s.query(Order).filter_by(external_id=order_id).one().status == "paid"
        assert s.query(ProcessedPaymentEvent).filter_by(object_id=order_id).count() == 1
        assert s.query(UsersQuota).filter_by(session_id=sid).one().is_pro is True
    finally:
        s.close()
