"""Phase 16 - patch generation.

Given an accepted plan, ask the model for a unified diff and write it to
``<task_dir>/patch.diff``. **Never applied automatically** (PLAN.md §41). When
the project is a git repo we run ``git apply --check`` (dry run) and record the
result so a human knows whether the diff is clean.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from code_memory.config import Config
from code_memory.context.util import estimate_tokens
from code_memory.llm.prompts import render_patch_prompt, system_prompt
from code_memory.llm.provider import LLMProvider
from code_memory.logging_setup import get_logger

log = get_logger("llm.patch")

_FENCE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.S)
_DIFF_FILE_RE = re.compile(r"^\+\+\+ [ab]/(.+)$", re.M)


def _plan_text(advice) -> str:
    p = advice.parsed or {}
    bits = [p.get("summary", ""), ""]
    for fc in (p.get("files_to_change") or []):
        if isinstance(fc, dict):
            bits.append(f"- {fc.get('file')} ({fc.get('lines', '?')}): "
                        f"{fc.get('reason', '')}"
                        + (f"  [{fc.get('statement')}]" if fc.get("statement") else ""))
        else:
            bits.append(f"- {fc}")
    for step in (p.get("implementation_plan") or []):
        bits.append(f"* {step}")
    return "\n".join(bits).strip() or advice.raw[:2000]


def _clean_diff(text: str) -> str:
    m = _FENCE.search(text or "")
    body = m.group(1) if m else (text or "")
    # trim leading prose before the first diff/--- header
    for marker in ("diff --git ", "--- "):
        idx = body.find(marker)
        if idx != -1:
            body = body[idx:]
            break
    return body.strip() + "\n"


def generate_patch(config: Config, provider: LLMProvider, task_dir: Path,
                   task: str, advice) -> dict[str, Any]:
    src = task_dir / "source_context.md"
    src_text = src.read_text(encoding="utf-8") if src.is_file() else ""
    budget = int(config.get("context.max_tokens", 24000))
    if estimate_tokens(src_text) > budget - 2000:
        src_text = src_text[: (budget - 2000) * 4]

    prompt = (render_patch_prompt(task, _plan_text(advice), config)
              + "\n\n---\n# source_context.md\n\n" + src_text)
    resp = provider.generate(system=system_prompt(config), prompt=prompt,
                             temperature=0.0)

    diff = _clean_diff(resp.text)
    patch_path = task_dir / "patch.diff"
    patch_path.write_text(diff, encoding="utf-8")

    files = _DIFF_FILE_RE.findall(diff)
    result: dict[str, Any] = {
        "path": str(patch_path),
        "files": files,
        "provider": resp.provider,
        "model": resp.model,
        "apply_check": _git_apply_check(config.project_root, patch_path),
        "applied": False,
    }
    log.info("patch generated", extra={"files": len(files),
                                       "apply_check": result["apply_check"]})
    return result


def _git_apply_check(root: Path, patch_path: Path) -> str:
    if not (root / ".git").exists():
        return "skipped (not a git repo)"
    try:
        proc = subprocess.run(
            ["git", "apply", "--check", "--verbose", str(patch_path)],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"
    if proc.returncode == 0:
        return "clean"
    return "does not apply: " + (proc.stderr.strip().splitlines()[-1]
                                 if proc.stderr.strip() else "unknown")
