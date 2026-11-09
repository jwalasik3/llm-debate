from fastapi.testclient import TestClient

from app.main import create_app
from app.prompts import TRAITS, build_request, system_prompt
from tests.helpers import FakeProvider, config


def test_config_validation_preview_and_exports(tmp_path):
    provider = FakeProvider(block=True)
    app = create_app(str(tmp_path / "api.sqlite3"), provider=provider)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["api_key_configured"]
        data = config(rounds=1).model_dump(mode="json")
        preview = client.post("/api/prompts/preview", json=data).json()
        assert "PRYWATNE_A" in preview["agents"][0]
        assert "PRYWATNE_B" not in preview["agents"][0]
        assert client.post("/api/debates", json={**data, "rounds": 0}).status_code == 422
        assert (
            client.post("/api/debates", json={**data, "agents": data["agents"][:1]}).status_code
            == 422
        )
        response = client.post("/api/debates", json=data)
        assert response.status_code == 201
        debate_id = response.json()["id"]
        assert client.post("/api/debates", json=data).status_code == 409
        assert client.get("/api/debates/not-found").status_code == 404
        assert (
            client.patch(
                f"/api/debates/{debate_id}/annotations",
                json={"notes": "Notatka", "outcome": "consensus"},
            ).status_code
            == 200
        )
        client.post(f"/api/debates/{debate_id}/control", json={"action": "stop"})
        exported = client.get(f"/api/debates/{debate_id}/export")
        assert "attachment" in exported.headers["content-disposition"]
        result = exported.json()
        assert result["config"] == data
        assert result["annotations"]["notes"] == "Notatka"
        assert result["schema_version"] == 1
        assert result["status"] == "stopped"
        assert client.get("/api/debates?q=nowa").json()[0]["id"] == debate_id
        assert client.get("/api/debates?q=brak").json() == []
        assert "api_key" not in exported.text.lower()
        assert client.get(f"/api/debates/{debate_id}/attempts/missing").status_code == 404
    with TestClient(create_app(str(tmp_path / "api.sqlite3"), provider=FakeProvider())) as client:
        assert client.get(f"/api/debates/{debate_id}").json()["config"] == data
        assert (
            client.get(f"/api/debates/{debate_id}").json()["annotations"]["outcome"] == "consensus"
        )


def test_delete_debate_removes_snapshot_and_cached_state(tmp_path):
    database = str(tmp_path / "delete.sqlite3")
    with TestClient(create_app(database, provider=FakeProvider(block=True))) as client:
        debate_id = client.post("/api/debates", json=config().model_dump(mode="json")).json()["id"]
        path = f"/api/debates/{debate_id}"
        assert client.delete(path).status_code == 409
        assert client.get(path).status_code == 200
        client.post(f"{path}/control", json={"action": "stop"})
        assert client.delete(path).status_code == 204
        assert client.get("/api/debates").json() == []
        assert client.get(path).status_code == 404
        assert client.get(f"{path}/export").status_code == 404
        assert client.post(f"{path}/control", json={"action": "resume"}).status_code == 404
        assert client.patch(f"{path}/annotations", json={"notes": "test"}).status_code == 404
        assert client.delete(path).status_code == 404
    with TestClient(create_app(database, provider=FakeProvider())) as client:
        assert client.get("/api/debates").json() == []
        assert client.get(path).status_code == 404


def test_deleting_archive_does_not_interrupt_another_debate(tmp_path):
    with TestClient(
        create_app(str(tmp_path / "delete.sqlite3"), provider=FakeProvider(block=True))
    ) as client:
        data = config().model_dump(mode="json")
        old_id = client.post("/api/debates", json=data).json()["id"]
        client.post(f"/api/debates/{old_id}/control", json={"action": "stop"})
        active_id = client.post("/api/debates", json=data).json()["id"]
        assert client.delete(f"/api/debates/{old_id}").status_code == 204
        assert client.get("/api/health").json()["active_debate_id"] == active_id
        assert [item["id"] for item in client.get("/api/debates").json()] == [active_id]


def test_missing_key_and_static_frontend(tmp_path, monkeypatch):
    provider = FakeProvider()
    provider.configured = False
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>Test</h1>")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("// frontend asset")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    with TestClient(create_app(str(tmp_path / "db"), provider)) as client:
        assert client.get("/").text == "<h1>Test</h1>"
        assert client.get("/assets/app.js").text == "// frontend asset"
        assert client.get("/api/health").json()["api_key_configured"] is False
        assert client.post("/api/debates", json=config().model_dump(mode="json")).status_code == 409
        assert client.get("/api/debates").json() == []
        assert client.get("/api/unknown").status_code == 404


def test_selected_provider_requires_its_own_key_and_matching_model(tmp_path):
    groq, gemini = FakeProvider(), FakeProvider(block=True)
    groq.configured = False
    with TestClient(
        create_app(str(tmp_path / "providers.sqlite3"), {"groq": groq, "gemini": gemini})
    ) as client:
        assert client.get("/api/health").json()["providers"] == {"groq": False, "gemini": True}
        assert {item["id"] for item in client.get("/api/settings").json()["providers"]} == {
            "groq",
            "gemini",
        }
        denied = client.post("/api/debates", json=config().model_dump(mode="json"))
        assert denied.status_code == 409
        assert "GROQ_API_KEY" in denied.json()["detail"]
        data = config(provider="gemini").model_dump(mode="json")
        assert client.post("/api/debates", json={**data, "provider": "groq"}).status_code == 422
        created = client.post("/api/debates", json=data)
        assert created.status_code == 201
        debate_id = created.json()["id"]
        client.post(f"/api/debates/{debate_id}/control", json={"action": "stop"})
        result = client.get(f"/api/debates/{debate_id}/export").json()
        assert result["provider"] == "gemini"
        assert result["model"] == data["model"]
        assert groq.requests == []


def test_traits_and_reasoning_are_independent():
    data = config()
    for key, definition in TRAITS.items():
        assert len(definition["instructions"]) == 5
        for value in range(1, 6):
            setattr(data.agents[0].traits, key, value)
            assert definition["instructions"][value - 1] in system_prompt(data, 0)
    off = build_request(data, 0, [])
    data.agents[0].reasoning = True
    on = build_request(data, 0, [])
    assert off["reasoning_effort"] == "none"
    assert on["reasoning_effort"] == "low"
    assert on["reasoning_format"] == "parsed"
    assert not {"temperature", "top_p", "seed"} & on.keys()


def test_legacy_word_target_does_not_change_prompts_or_exported_config(tmp_path):
    from app.models import DebateConfig

    data = config().model_dump(mode="json")
    legacy = {**data, "target_words": 100}
    restored = DebateConfig.model_validate(legacy)
    assert restored.model_dump(mode="json") == data
    assert legacy["target_words"] == 100  # archived snapshots are not modified
    request = build_request(restored, 0, [])
    assert request["max_completion_tokens"] == 1024
    assert "słów" not in request["messages"][0]["content"]
    assert "1024" not in request["messages"][0]["content"]
    with TestClient(
        create_app(str(tmp_path / "legacy.sqlite3"), provider=FakeProvider())
    ) as client:
        current = client.post("/api/prompts/preview", json=data)
        previous = client.post("/api/prompts/preview", json=legacy)
        assert previous.status_code == 200
        assert previous.json() == current.json()
