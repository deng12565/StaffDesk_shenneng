from fastapi.testclient import TestClient

from app.main import app


def test_swagger_and_openapi_are_enabled() -> None:
    client = TestClient(app)

    docs = client.get("/docs")
    schema = client.get("/openapi.json")

    assert docs.status_code == 200
    assert "text/html" in docs.headers["content-type"]
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == app.title
