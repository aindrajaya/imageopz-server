import io

from PIL import Image


def _png_bytes() -> bytes:
    img = Image.new("RGB", (8, 8), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_health_smoke(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert "status" in body
    assert "version" in body


def test_optimize_returns_503_when_queue_unavailable(client):
    # With REDIS_URL pointed at a closed port (see conftest),
    # the queue should be unavailable and the endpoint should return 503.
    files = [
        ("images", ("test.png", _png_bytes(), "image/png")),
    ]
    res = client.post("/api/v1/optimize", files=files)
    assert res.status_code == 503
    assert "Job queue unavailable" in res.text

