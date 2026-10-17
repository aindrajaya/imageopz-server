"""Client IP behind trusted proxies (TRUSTED_PROXY_HOPS): a client-supplied X-Forwarded-For prefix must never change the
derived IP. Proxies append, they do not replace: Google's load balancer documents
"X-Forwarded-For: [<supplied-value>,]<client-ip>,<load-balancer-ip>" (https://docs.cloud.google.com/load-balancing/docs/https).
TRUSTED_PROXY_HOPS = number of rightmost entries appended by trusted infrastructure; the client IP is the leftmost of
those. 0 = ignore the header and use the socket peer (local dev)."""

import pytest

SESSION_ME = "/api/v1/session/me"


def _hash(ip):
    from app.utils.security import QuotaManager

    return QuotaManager.calculate_ip_hash(ip)


@pytest.fixture
def ip_hash_for(client, monkeypatch):
    import app.utils.security as security

    def get(hops, *xff):
        # The stale module-level settings object is the one IpFingerprint reads (modules reload per test).
        monkeypatch.setattr(security.settings, "trusted_proxy_hops", hops)
        headers = [("X-Session-Id", "ip-test-session")] + [("X-Forwarded-For", v) for v in xff]
        res = client.get(SESSION_ME, headers=headers)
        assert res.status_code == 200, res.text
        return res.json()["ip_hash"]

    return get


def test_default_trusts_one_hop():
    from app.config import Settings

    assert Settings().trusted_proxy_hops == 1


@pytest.mark.parametrize("value", ["-1", "two"])
def test_invalid_hops_fail_validation(monkeypatch, value):
    from pydantic import ValidationError
    from app.config import Settings

    monkeypatch.setenv("TRUSTED_PROXY_HOPS", value)
    with pytest.raises(ValidationError):
        Settings()


def test_one_hop_takes_the_entry_the_proxy_appended(ip_hash_for):
    assert ip_hash_for(1, "203.0.113.9") == _hash("203.0.113.9")


def test_spoofed_leading_entry_does_not_change_the_ip_one_hop(ip_hash_for):
    honest = ip_hash_for(1, "203.0.113.9")
    assert ip_hash_for(1, "1.2.3.4, 203.0.113.9") == honest
    assert ip_hash_for(1, "10.0.0.1,  evil value , 203.0.113.9") == honest


def test_load_balancer_shape_two_hops(ip_hash_for):
    # <client-ip>,<load-balancer-ip> with and without a supplied prefix
    honest = ip_hash_for(2, "198.51.100.7,34.120.0.1")
    assert honest == _hash("198.51.100.7")
    assert ip_hash_for(2, "1.2.3.4, 198.51.100.7, 34.120.0.1") == honest


def test_zero_hops_ignores_the_header_and_uses_the_socket_peer(ip_hash_for):
    peer = _hash("testclient")  # TestClient's socket peer host
    assert ip_hash_for(0, "1.2.3.4, 203.0.113.9") == peer
    assert ip_hash_for(0) == peer


def test_missing_header_uses_the_socket_peer(ip_hash_for):
    assert ip_hash_for(1) == _hash("testclient")


def test_fewer_entries_than_hops_uses_the_leftmost(ip_hash_for):
    # Every entry was appended by a trusted proxy (a client cannot remove entries), so the leftmost is the client.
    assert ip_hash_for(2, "198.51.100.7") == _hash("198.51.100.7")


def test_repeated_headers_are_one_list(ip_hash_for):
    # A spoofed header line plus the proxy's line: still the rightmost trusted entry.
    assert ip_hash_for(1, "1.2.3.4", "203.0.113.9") == _hash("203.0.113.9")
