from code_memory.config import load_config


def test_defaults_load():
    cfg = load_config()
    assert cfg.get("llm.provider") == "ollama"
    assert cfg.get("context.max_tokens") == 24000


def test_env_override(monkeypatch):
    monkeypatch.setenv("CODE_MEMORY__LLM__MODEL", "qwen3-coder:14b")
    monkeypatch.setenv("CODE_MEMORY__CONTEXT__MAX_TOKENS", "8000")
    monkeypatch.setenv("CODE_MEMORY__SCANNER__INCREMENTAL", "false")
    cfg = load_config()
    assert cfg.get("llm.model") == "qwen3-coder:14b"
    assert cfg.get("context.max_tokens") == 8000
    assert cfg.get("scanner.incremental") is False


def test_env_list_override(monkeypatch):
    monkeypatch.setenv("CODE_MEMORY__SCANNER__EXCLUDE_DIRS", "[a, b, c]")
    cfg = load_config()
    assert cfg.get("scanner.exclude_dirs") == ["a", "b", "c"]


def test_missing_key_returns_default():
    cfg = load_config()
    assert cfg.get("nope.not.here", "fallback") == "fallback"
