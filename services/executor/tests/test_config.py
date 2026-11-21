import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_mock_provider_needs_no_key():
    settings = _settings(llm_provider="mock")
    assert settings.effective_provider.name == "mock"
    assert settings.effective_provider.model_for("writer") == "mock"


def test_deepseek_defaults():
    settings = _settings(llm_provider="deepseek", deepseek_api_key="sk-test")
    provider = settings.effective_provider
    assert provider.base_url == "https://api.deepseek.com/v1"
    assert provider.model_for("planner") == "deepseek-chat"
    assert provider.api_key == "sk-test"


def test_dashscope_switch_and_role_models():
    settings = _settings(
        llm_provider="dashscope",
        dashscope_api_key="sk-test",
        dashscope_model_writer="qwen-max",
        dashscope_model_router="qwen-turbo",
    )
    provider = settings.effective_provider
    assert provider.base_url.endswith("/compatible-mode/v1")
    assert provider.model_for("writer") == "qwen-max"
    assert provider.model_for("router") == "qwen-turbo"
    assert provider.model_for("critic") == "qwen-plus"


def test_generic_model_override_wins():
    settings = _settings(
        llm_provider="dashscope",
        dashscope_api_key="sk-test",
        llm_model_writer="override-model",
    )
    assert settings.effective_provider.model_for("writer") == "override-model"


def test_missing_key_fails_fast(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        _settings(llm_provider="deepseek", deepseek_api_key=None, llm_api_key=None)
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_unknown_provider_rejected():
    with pytest.raises(ValidationError):
        _settings(llm_provider="unknown")


def test_fallback_requires_key():
    with pytest.raises(ValidationError):
        _settings(
            llm_provider="deepseek",
            deepseek_api_key="sk-test",
            llm_fallback_provider="dashscope",
            dashscope_api_key=None,
        )
