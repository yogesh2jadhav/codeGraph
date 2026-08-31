"""Prompt template loading + assembly for the coding advisor."""

from __future__ import annotations

from pathlib import Path

from code_memory.config import Config

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

_DEFAULT_FEATURE = """You are a senior Java engineer working inside an existing repository.
You are given a generated Code Memory pack. Source code is ground truth; edges
carry confidence tags - never treat non-HIGH facts as confirmed.

## Task

{{TASK}}

## Rules
- Cite file and line ranges for every proposed change.
- Distinguish facts from inference. Do not invent symbols or config keys.
- Inspect relevant tests; propose test updates.
- Keep the change minimal and consistent with existing patterns.

## Output
Return one JSON object then a short Markdown explanation:
```json
{"summary":"...","confidence":"HIGH|MEDIUM|LOW","files_to_change":[{"file":"...","lines":"..","reason":".."}],"files_to_review":[],"tests_to_update":[],"risks":[],"implementation_plan":[]}
```
"""

_DEFAULT_SYSTEM = ("You are Local Code Memory's coding advisor. You only have the "
                   "provided context pack, never a shell. Never fabricate paths "
                   "or symbols. Always cite file:line. Separate fact from "
                   "inference and flag insufficient context.")


def _load(name: str, fallback: str) -> str:
    p = _PROMPTS_DIR / name
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return fallback


def system_prompt(config: Config) -> str:
    override = config.get("llm.system_prompt_file")
    if override:
        try:
            return Path(override).read_text(encoding="utf-8")
        except OSError:
            pass
    return _load("system.md", _DEFAULT_SYSTEM)


def render_task_prompt(task: str, config: Config, template: str = "implement_feature.md") -> str:
    body = _load(template, _DEFAULT_FEATURE)
    return body.replace("{{TASK}}", task.strip())
