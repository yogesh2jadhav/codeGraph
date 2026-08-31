import shutil
import subprocess
from pathlib import Path

import pytest

from code_memory.git import change_impact, read_diff
from code_memory.llm.advisor import CodingAdvisor, Advice
from code_memory.llm.patch import _clean_diff, generate_patch
from code_memory.llm.provider import LLMProvider, LLMResponse
from code_memory.pipeline import run_scan

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def git_spring(tmp_path):
    root = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", root)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    return root


# -- Phase 14 -----------------------------------------------------------
def test_read_diff_changed_ranges(git_spring):
    svc = git_spring / "src/main/java/com/demo/svc/OrderService.java"
    text = svc.read_text().replace(
        "return repository.findById(id);",
        "// changed\n        return repository.findById(id);")
    svc.write_text(text)

    diff = read_diff(git_spring, "HEAD", pathspec="*.java")
    assert diff.available
    jf = {f.path: f for f in diff.java_files()}
    assert "src/main/java/com/demo/svc/OrderService.java" in jf
    assert jf["src/main/java/com/demo/svc/OrderService.java"].changed_ranges


def test_change_impact_maps_to_callers(git_spring, config_for):
    cfg = config_for(git_spring)
    run_scan(cfg, mode="full")

    repo_file = git_spring / "src/main/java/com/demo/repo/OrderRepository.java"
    repo_file.write_text(repo_file.read_text().replace(
        "Order save(Order order);",
        "Order save(Order order); // touched"))

    ci = change_impact(cfg, "HEAD")
    assert ci.diff.available
    assert any("OrderRepository" in s for s in ci.changed_symbols)
    # OrderService.place -> OrderRepository.save, so the service is an impacted caller
    assert any("OrderService" in c for c in ci.impacted_callers)
    assert (git_spring / ".code-memory" / "change_impact.md").is_file()


def test_change_impact_no_git(tmp_path, config_for):
    root = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", root)
    ci = change_impact(config_for(root), "HEAD")
    assert not ci.diff.available


# -- Phase 16 -----------------------------------------------------------
class _DiffProvider(LLMProvider):
    name = "fake"

    def generate(self, *, system, prompt, temperature=0.1):
        diff = ("```diff\n"
                "diff --git a/src/main/java/com/demo/svc/OrderService.java "
                "b/src/main/java/com/demo/svc/OrderService.java\n"
                "--- a/src/main/java/com/demo/svc/OrderService.java\n"
                "+++ b/src/main/java/com/demo/svc/OrderService.java\n"
                "@@ -1,1 +1,2 @@\n"
                " package com.demo.svc;\n"
                "+// added by test\n"
                "```\n")
        return LLMResponse(diff, "fake", "fake", {})


def test_clean_diff_strips_fences_and_prose():
    raw = "Sure, here you go:\n```diff\ndiff --git a/x b/x\n--- a/x\n+++ b/x\n```\nDone!"
    out = _clean_diff(raw)
    assert out.startswith("diff --git a/x b/x")
    assert "Sure" not in out and "Done" not in out


def test_generate_patch_writes_and_checks(git_spring, config_for):
    cfg = config_for(git_spring)
    run_scan(cfg, mode="full")
    from code_memory.context import generate_task_context

    pack = generate_task_context(cfg, "add a comment to OrderService")
    advice = Advice("t", "fake", "fake", "{}", {"summary": "x",
                    "files_to_change": []}, {})
    res = generate_patch(cfg, _DiffProvider(), pack.directory,
                         "add a comment", advice)

    assert (pack.directory / "patch.diff").is_file()
    assert res["files"] == ["src/main/java/com/demo/svc/OrderService.java"]
    assert res["apply_check"] in ("clean", ) or "does not apply" in res["apply_check"]
    assert res["applied"] is False
