import json
import shutil
from pathlib import Path

from code_memory.context import generate_task_context
from code_memory.llm import CodingAdvisor, EchoProvider
from code_memory.pipeline import run_scan

FIXTURES = Path(__file__).parents[1] / "fixtures"

_TASK_FILES = {
    "task.md", "relevant_symbols.md", "relevant_files.md", "call_graph.md",
    "data_flow.md", "tests.md", "configuration.md", "sql.md",
    "source_context.md", "llm_prompt.md",
}


def _scanned(tmp_path, config_for):
    root = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", root)
    cfg = config_for(root)
    run_scan(cfg, mode="full")
    return cfg, root


def test_task_pack_contents(tmp_path, config_for):
    cfg, root = _scanned(tmp_path, config_for)
    pack = generate_task_context(cfg, "add retry when saving an order fails")

    assert {f.name for f in pack.files} == _TASK_FILES
    assert pack.directory.name.startswith("task_")
    assert 0 < pack.est_tokens < cfg.get("context.max_tokens")

    task_md = (pack.directory / "task.md").read_text()
    assert "add retry when saving an order fails" in task_md
    # OrderService.place / OrderRepository.save should be near the top
    assert "place(Order)" in task_md or "save(Order)" in task_md

    src = (pack.directory / "source_context.md").read_text()
    assert "```java" in src and "OrderService" in src

    cg = (pack.directory / "call_graph.md").read_text()
    assert "calls:" in cg or "called by:" in cg


def test_task_ids_increment(tmp_path, config_for):
    cfg, _ = _scanned(tmp_path, config_for)
    a = generate_task_context(cfg, "first task")
    b = generate_task_context(cfg, "second task")
    assert a.directory != b.directory
    assert b.directory.name.endswith("_002")


def test_advisor_with_echo_provider(tmp_path, config_for):
    cfg, root = _scanned(tmp_path, config_for)
    pack = generate_task_context(cfg, "add a DELETE endpoint for an order")

    advisor = CodingAdvisor(cfg, provider=EchoProvider("echo-test"))
    advice = advisor.advise(pack.directory, "add a DELETE endpoint for an order")

    assert advice.provider == "echo"
    assert advice.parsed is not None
    assert set(advice.parsed) >= {"summary", "confidence", "files_to_change",
                                  "implementation_plan"}
    assert (pack.directory / "advice.md").is_file()
    saved = json.loads((pack.directory / "advice.json").read_text())
    assert saved["model"] == "echo-test"
    assert advice.prompt_tokens_est > 0


def test_advisor_prompt_respects_budget(tmp_path, config_for):
    cfg, root = _scanned(tmp_path, config_for)
    cfg.data["context"]["max_tokens"] = 1500
    pack = generate_task_context(cfg, "refactor order handling")

    advisor = CodingAdvisor(cfg, provider=EchoProvider())
    prompt, ptok = advisor._assemble_prompt(pack.directory, "refactor order handling")
    assert ptok <= 1500 + 200  # instruction block + slack
