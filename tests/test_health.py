"""H1: /health is a liveness check with a generic body; /readyz checks the database and never returns error text."""
import pytest

SECRET_ERROR = "password authentication failed for user owner at db.internal:6543"


@pytest.fixture()
def broken_db(client):
    from app.routes.health import get_db  # the function the route depends on (route modules are not reloaded)

    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError(SECRET_ERROR)

    def _get_db():
        yield _Broken()

    client.app.dependency_overrides[get_db] = _get_db
    yield
    client.app.dependency_overrides.pop(get_db, None)


def test_health_is_generic_and_independent_of_the_database(client, broken_db):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert (body["status"], body["version"]) == ("healthy", "1.0.0")
    assert set(body) <= {"status", "version", "timestamp"}
    assert "password" not in res.text


def test_readyz_ok_when_database_answers(client):
    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ready"}


def test_readyz_is_503_with_generic_body_when_database_fails(client, broken_db):
    res = client.get("/readyz")
    assert res.status_code == 503
    assert res.json() == {"status": "unavailable"}
    assert "password" not in res.text and "6543" not in res.text


def test_readyz_is_not_in_the_public_schema(client):
    assert "/readyz" not in client.get("/openapi.json").json()["paths"]
