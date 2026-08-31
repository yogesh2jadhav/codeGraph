"""Phase 12/13 - coding advisor.

Takes a Phase 11 task pack, assembles a budgeted prompt (system + AI coding
instructions + the pack files), sends it to a local LLM, parses the structured
answer (PLAN.md §40) and renders ``advice.md`` next to the pack.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_memory.config import Config
from code_memory.context.util import estimate_tokens
from code_memory.llm.prompts import render_task_prompt, system_prompt
from code_memory.llm.provider import LLMProvider, get_llm_provider
from code_memory.logging_setup import get_logger

log = get_logger("llm.advisor")

# order matters: earliest files are most important, trimmed last
_PACK_ORDER = [
    "task.md", "relevant_symbols.md", "source_context.md", "call_graph.md",
    "data_flow.md", "sql.md", "tests.md", "configuration.md", "relevant_files.md",
]
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_JSON_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class Advice:
    task: str
    provider: str
    model: str
    raw: str
    parsed: dict[str, Any] | None
    meta: dict[str, Any] = field(default_factory=dict)
    prompt_tokens_est: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "provider": self.provider, "model": self.model,
                "parsed": self.parsed, "meta": self.meta,
                "prompt_tokens_est": self.prompt_tokens_est}


class CodingAdvisor:
    def __init__(self, config: Config, provider: LLMProvider | None = None):
        self.config = config
        self.provider = provider or get_llm_provider(config)

    def _assemble_prompt(self, task_dir: Path, task: str) -> tuple[str, int]:
        budget = int(self.config.get("context.max_tokens", 24000))
        # reserve room for the model's own output
        reserve = min(3000, max(400, budget // 4))
        remaining = max(600, budget - reserve)

        head = render_task_prompt(task, self.config)
        parts: list[str] = [head]
        used = estimate_tokens(head)

        sections: list[tuple[str, str]] = []
        instr = self.config.output_dir / "context" / "14_ai_coding_instructions.md"
        if instr.is_file():
            sections.append(("Repository coding instructions",
                             instr.read_text(encoding="utf-8")))
        for name in _PACK_ORDER:
            f = task_dir / name
            if f.is_file():
                sections.append((name, f.read_text(encoding="utf-8")))

        for title, body in sections:
            if used >= remaining:
                break
            cost = estimate_tokens(body)
            if used + cost > remaining:
                body = body[: max(0, remaining - used) * 4]
                cost = estimate_tokens(body)
            if not body.strip():
                continue
            parts.append(f"\n\n---\n# {title}\n\n{body}")
            used += cost

        prompt = "".join(parts)
        return prompt, estimate_tokens(prompt)

    def advise(self, task_dir: Path, task: str) -> Advice:
        prompt, ptok = self._assemble_prompt(task_dir, task)
        resp = self.provider.generate(
            system=system_prompt(self.config), prompt=prompt,
            temperature=float(self.config.get("llm.temperature", 0.1)))

        parsed = _extract_json(resp.text)
        advice = Advice(task, resp.provider, resp.model, resp.text, parsed,
                        resp.meta, ptok)

        (task_dir / "advice.md").write_text(_render_advice(advice),
                                            encoding="utf-8")
        (task_dir / "advice.json").write_text(
            json.dumps(advice.to_dict(), indent=2) + "\n", encoding="utf-8")
        log.info("advice generated", extra={"provider": resp.provider,
                                            "model": resp.model,
                                            "prompt_tokens_est": ptok,
                                            "parsed": parsed is not None})
        return advice


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text or ""
    candidates: list[str] = [m.group(1) for m in _FENCE_RE.finditer(text)]
    m = _JSON_RE.search(text)
    if m:
        candidates.append(m.group(0))
        candidates.append(_first_balanced(text, m.start()))
    for c in candidates:
        if not c:
            continue
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _first_balanced(text: str, start: int) -> str:
    """Return the substring from ``start`` that closes its first '{' cleanly."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def _render_advice(a: Advice) -> str:
    lines = ["# Advice", "", f"> Task: {a.task}",
             f"> Model: `{a.model}` via `{a.provider}`  "
             f"(prompt ~{a.prompt_tokens_est} tokens)", ""]
    p = a.parsed
    if not p:
        lines += ["_Model did not return parseable JSON; raw output below._", "",
                  "```", a.raw.strip(), "```", ""]
        return "\n".join(lines) + "\n"

    lines += [f"**{p.get('summary', '')}**",
              f"_confidence: {p.get('confidence', '?')}_", ""]
    for title, key in (("Files to change", "files_to_change"),
                       ("Files to review", "files_to_review"),
                       ("Tests to update", "tests_to_update")):
        rows = p.get(key) or []
        if rows:
            lines += [f"## {title}", ""]
            for r in rows:
                if isinstance(r, dict):
                    loc = f" ({r['lines']})" if r.get("lines") else ""
                    lines.append(f"- `{r.get('file', '?')}`{loc} - "
                                 f"{r.get('reason', '')}")
                else:
                    lines.append(f"- {r}")
            lines.append("")
    if p.get("risks"):
        lines += ["## Risks", ""] + [f"- {r}" for r in p["risks"]] + [""]
    if p.get("implementation_plan"):
        lines += ["## Implementation plan", ""]
        lines += [f"{i}. {s}" for i, s in enumerate(p["implementation_plan"], 1)]
        lines.append("")
    lines += ["## Raw model output", "", "```", a.raw.strip(), "```", ""]
    return "\n".join(lines) + "\n"
