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


# advisor task modes (PLAN.md §13/§39) -> prompt template file
MODES = {
    "implement_feature": "implement_feature.md",
    "find_fix": "find_fix.md",
    "debug": "debug.md",
    "add_logging": "add_logging.md",
    "refactor": "refactor.md",
    "impact_analysis": "impact_analysis.md",
    "explain_code": "explain_code.md",
}
_MODE_ALIASES = {
    "feature": "implement_feature", "implement": "implement_feature",
    "fix": "find_fix", "bug": "find_fix", "logging": "add_logging",
    "log": "add_logging", "impact": "impact_analysis", "analyze": "impact_analysis",
    "explain": "explain_code", "walkthrough": "explain_code",
    "explain code": "explain_code",
}


def resolve_mode(mode: str | None) -> str:
    if not mode:
        return "implement_feature"
    m = mode.strip().lower().replace("-", "_")
    m = _MODE_ALIASES.get(m, m)
    if m not in MODES:
        raise ValueError(f"unknown mode '{mode}'. choose from: "
                         f"{', '.join(sorted(MODES))}")
    return m


def render_task_prompt(task: str, config: Config, *, mode: str | None = None,
                       template: str | None = None) -> str:
    tpl = template or MODES[resolve_mode(mode)]
    body = _load(tpl, _DEFAULT_FEATURE)
    return body.replace("{{TASK}}", task.strip())


def render_patch_prompt(task: str, plan_text: str, config: Config) -> str:
    body = _load("generate_patch.md",
                 "Produce a unified diff implementing:\n\n{{TASK}}\n\n"
                 "Plan:\n{{PLAN}}\n\nOutput a unified diff only.")
    return body.replace("{{TASK}}", task.strip()).replace("{{PLAN}}",
                                                          plan_text.strip())
