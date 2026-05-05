def test_session_me_returns_defaults(client):
    res = client.get("/api/v1/session/me", headers={"X-Session-Id": "sess_me_1"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    assert body["session_id"] == "sess_me_1"
    assert body["is_pro"] in (False, True)
    assert "credits_remaining" in body
    assert "pro_verified" in body
    assert "pro_source" in body
    assert "needs_restore" in body
    assert "pro_tier_max_credits" in body
    assert "pro_credits_used" in body
    assert "pro_credits_remaining" in body


def test_session_logout_rotates_cookie(client):
    res = client.post("/api/v1/session/logout")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    assert body.get("session_id")
    # Should set cookie at least once (clear + set)
    set_cookie = res.headers.get("set-cookie", "")
    assert "imageopz_sid" in set_cookie
