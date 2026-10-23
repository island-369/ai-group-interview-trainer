"""配置加载与 provider 推断（M0 验收项：配置加载测试通过）。"""

from app.core.config import Settings


def test_provider_inferred_mock_without_key():
    s = Settings(_env_file=None, llm_provider="", llm_api_key="")
    assert s.resolved_provider == "mock"


def test_provider_inferred_real_with_key():
    s = Settings(_env_file=None, llm_provider="", llm_api_key="sk-test")
    assert s.resolved_provider == "openai-compatible"


def test_explicit_provider_env_wins(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    s = Settings(_env_file=None, llm_api_key="sk-test")
    assert s.resolved_provider == "mock"


def test_paths_exist(mock_settings):
    assert mock_settings.personas_dir.is_dir()
    assert mock_settings.difficulty_file.is_file()
    assert mock_settings.questions_dir.is_dir()
