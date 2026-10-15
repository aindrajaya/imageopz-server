"""The conftest isolation holds: no .env, dummy credentials, and external connections are blocked and reported."""
import socket

import pytest


def test_settings_do_not_read_dotenv(client):
    import app.config as config

    assert config.Settings.model_config.get("env_file") is None
    s = config.settings
    assert s.lemonsqueezy_api_key == "" and s.r2_uploads_access_key_id == ""
    assert s.r2_access_key_id == "dummy-r2-key" and s.supabase_anon_key == "dummy-anon-key"


def test_external_connections_are_blocked_and_recorded(_block_external_network):
    from conftest import ExternalNetworkBlocked
    import httpx

    with pytest.raises(ExternalNetworkBlocked, match="api.lemonsqueezy.com"):
        httpx.post("https://api.lemonsqueezy.com/v1/checkouts", json={})
    with pytest.raises(ExternalNetworkBlocked, match="1.1.1.1"):
        socket.create_connection(("1.1.1.1", 443), timeout=1)
    try:  # code that swallows the error is still caught at teardown via the recorded attempts
        httpx.get("https://dummy-account.r2.cloudflarestorage.com/")
    except Exception:
        pass
    assert _block_external_network == ["api.lemonsqueezy.com", "1.1.1.1", "dummy-account.r2.cloudflarestorage.com"]
    _block_external_network.clear()  # these attempts were the point of this test


def test_local_connections_are_allowed():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        with socket.create_connection(srv.getsockname(), timeout=1):
            pass


def test_gethostbyname_and_udp_are_blocked(_block_external_network):
    from conftest import ExternalNetworkBlocked

    with pytest.raises(ExternalNetworkBlocked, match="example.com"):
        socket.gethostbyname("example.com")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        with pytest.raises(ExternalNetworkBlocked, match="8.8.8.8"):
            s.sendto(b"x", ("8.8.8.8", 53))
    assert _block_external_network == ["example.com", "8.8.8.8"]
    _block_external_network.clear()
