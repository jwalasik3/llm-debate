import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import FakeProvider, config


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("APP_USERNAME", "professor")
    monkeypatch.setenv("APP_PASSWORD", "test-only-long-password")
    return ("professor", "test-only-long-password")


def test_all_routes_are_protected(tmp_path, credentials):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<h1>Private</h1>")
    (dist / "assets" / "app.js").write_text("// private")
    app = create_app(str(tmp_path / "db"), FakeProvider(), str(dist))
    with TestClient(app) as client:
        for path in [
            "/", "/assets/app.js", "/docs", "/openapi.json", "/api/health",
            "/api/debates", "/api/debates/test/export", "/api/debates/test/events",
        ]:
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.headers["www-authenticate"].startswith("Basic ")
            assert client.get(path, auth=credentials).status_code != 401
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.post("/api/debates", json={}).status_code == 401
        assert client.delete("/api/debates/test").status_code == 401


@pytest.mark.parametrize("header", ["Basic !!!", "Basic Og==", "Bearer token", "Basic"])
def test_invalid_credentials(tmp_path, credentials, header):
    with TestClient(create_app(str(tmp_path / "db"), FakeProvider())) as client:
        assert client.get("/", headers={"Authorization": header}).status_code == 401
        assert client.get("/", auth=(credentials[0], "wrong")).status_code == 401
        assert client.get("/", auth=("wrong", credentials[1])).status_code == 401


def test_authenticated_writes_and_cross_origin_protection(tmp_path, credentials):
    with TestClient(create_app(str(tmp_path / "db"), FakeProvider(block=True))) as client:
        client.auth = credentials
        payload = config().model_dump(mode="json")
        assert client.post(
            "/api/debates", json=payload, headers={"Origin": "https://other.example"}
        ).status_code == 403
        assert client.get("/api/debates").json() == []
        result = client.post(
            "/api/debates", json=payload, headers={"Origin": "http://testserver"}
        )
        assert result.status_code == 201
        debate_id = result.json()["id"]
        assert client.post(
            f"/api/debates/{debate_id}/control", json={"action": "stop"}
        ).status_code == 200


@pytest.mark.parametrize("username,password", [("", ""), ("user", ""), ("", "pass"), ("a:b", "pass")])
def test_required_login_fails_closed(monkeypatch, username, password):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("APP_USERNAME", username)
    monkeypatch.setenv("APP_PASSWORD", password)
    with pytest.raises(ValueError, match="APP_USERNAME"):
        create_app()
