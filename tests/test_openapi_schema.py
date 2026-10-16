def test_internal_endpoints_are_not_in_the_public_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/uploads/presign" in paths
    assert [p for p in paths if p.startswith("/internal")] == []
