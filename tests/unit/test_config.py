"""DU1: cấu hình model Gemini qua biến môi trường GEMINI_MODEL."""
from agent.core_agent import DEFAULT_MODEL, GeminiLLM


def test_gemini_model_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-model-du-phong")
    assert GeminiLLM(api_key="test-key").model == "gemini-model-du-phong"


def test_gemini_model_argument_wins(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-model-du-phong")
    assert GeminiLLM(model="model-tham-so", api_key="test-key").model == "model-tham-so"


def test_default_model_is_flash_lite():
    assert DEFAULT_MODEL == "gemini-3.5-flash-lite"
