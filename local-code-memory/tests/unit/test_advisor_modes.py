import pytest

from code_memory.config import load_config
from code_memory.llm.prompts import (
    MODES,
    render_patch_prompt,
    render_task_prompt,
    resolve_mode,
)


def test_resolve_mode_and_aliases():
    assert resolve_mode(None) == "implement_feature"
    assert resolve_mode("debug") == "debug"
    assert resolve_mode("fix") == "find_fix"
    assert resolve_mode("logging") == "add_logging"
    assert resolve_mode("impact") == "impact_analysis"
    with pytest.raises(ValueError):
        resolve_mode("teleport")


@pytest.mark.parametrize("mode", sorted(MODES))
def test_each_mode_template_renders(mode):
    cfg = load_config()
    p = render_task_prompt("Do the thing", cfg, mode=mode)
    assert "Do the thing" in p
    assert "{{TASK}}" not in p
    assert "```json" in p or mode == "impact_analysis" or "unified diff" not in p


def test_add_logging_prompt_has_guardrails():
    p = render_task_prompt("add logs", load_config(), mode="add_logging")
    assert "never log secrets" in p.lower() or "secrets" in p.lower()
    assert "level" in p.lower()


def test_patch_prompt_injects_task_and_plan():
    p = render_patch_prompt("Add retry", "1. wrap in loop\n2. backoff", load_config())
    assert "Add retry" in p and "backoff" in p
    assert "unified diff" in p.lower()
