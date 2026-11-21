from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client() -> TestClient:
    settings = Settings(_env_file=None, llm_provider="mock", app_env="local")
    return TestClient(create_app(settings))


def test_health():
    with _client() as client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["llm_provider"] == "mock"


def test_normalize_bilibili():
    with _client() as client:
        response = client.post(
            "/internal/sources/normalize",
            json={"url": "https://www.bilibili.com/video/BV1xx411c7mD?p=2"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "bilibili"
        assert body["page"] == 2


def test_normalize_youtube():
    with _client() as client:
        response = client.post(
            "/internal/sources/normalize",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
        assert response.status_code == 200
        assert response.json()["source_video_id"] == "dQw4w9WgXcQ"


def test_unsupported_source_returns_400():
    with _client() as client:
        response = client.post(
            "/internal/sources/normalize",
            json={"url": "https://vimeo.com/123456"},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_URL"


def test_effective_config_masks_keys():
    with _client() as client:
        response = client.get("/internal/config/effective")
        assert response.status_code == 200
        body = response.json()
        assert body["llm_provider"] == "mock"
        assert "api_key" not in body
        assert body["llm_api_key_configured"] is False
