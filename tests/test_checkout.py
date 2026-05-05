def test_checkout_requires_api_key(client):
    res = client.post(
        "/api/v1/checkout/lemonsqueezy",
        json={"email": "test@example.com", "session_id": "sess_1"},
    )
    assert res.status_code == 500
    assert "LEMONSQUEEZY_API_KEY" in res.text
