import json

from code_memory.config import load_config
from code_memory.llm import EchoProvider, get_llm_provider
from code_memory.llm.advisor import _extract_json
from code_memory.llm.prompts import render_task_prompt, system_prompt


def test_echo_provider_returns_valid_json_stub():
    resp = EchoProvider().generate(system="s", prompt="> do the thing")
    assert resp.provider == "echo"
    payload = _extract_json(resp.text)
    assert payload["confidence"] == "LOW"
    assert "files_to_change" in payload and "implementation_plan" in payload


def test_extract_json_from_noisy_text():
    text = 'Sure! Here is the plan.\n```json\n{"summary":"x","confidence":"HIGH"}\n```\nDone.'
    assert _extract_json(text) == {"summary": "x", "confidence": "HIGH"}
    assert _extract_json("no json here at all") is None


def test_render_task_prompt_injects_task():
    cfg = load_config()
    p = render_task_prompt("Add caching to findOrder", cfg)
    assert "Add caching to findOrder" in p
    assert "{{TASK}}" not in p


def test_system_prompt_loads():
    assert "advisor" in system_prompt(load_config()).lower()


def test_get_llm_provider_falls_back_to_echo(monkeypatch):
    cfg = load_config()
    cfg.data["llm"]["provider"] = "ollama"
    cfg.data["llm"]["base_url"] = "http://127.0.0.1:59999"  # nothing there
    prov = get_llm_provider(cfg)
    assert isinstance(prov, EchoProvider)


def test_get_llm_provider_explicit_echo():
    cfg = load_config()
    cfg.data["llm"]["provider"] = "echo"
    assert isinstance(get_llm_provider(cfg), EchoProvider)
