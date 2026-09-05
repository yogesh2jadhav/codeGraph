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
from code_memory.llm.patch import generate_patch
from code_memory.llm.prompts import render_task_prompt, resolve_mode, system_prompt
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
    mode: str = "implement_feature"
    patch: dict[str, Any] | None = None   # {path, apply_check, files, ...}

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "mode": self.mode, "provider": self.provider,
                "model": self.model, "parsed": self.parsed, "meta": self.meta,
                "prompt_tokens_est": self.prompt_tokens_est,
                "patch": self.patch}


class CodingAdvisor:
    def __init__(self, config: Config, provider: LLMProvider | None = None):
        self.config = config
        self.provider = provider or get_llm_provider(config)

    def _assemble_prompt(self, task_dir: Path, task: str,
                         mode: str = "implement_feature") -> tuple[str, int]:
        budget = int(self.config.get("context.max_tokens", 24000))
        # reserve room for the model's own output
        reserve = min(3000, max(400, budget // 4))
        remaining = max(600, budget - reserve)

        head = render_task_prompt(task, self.config, mode=mode)
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

    def advise(self, task_dir: Path, task: str, *, mode: str | None = None,
               patch: bool = False) -> Advice:
        mode = resolve_mode(mode)
        prompt, ptok = self._assemble_prompt(task_dir, task, mode)
        resp = self.provider.generate(
            system=system_prompt(self.config), prompt=prompt,
            temperature=float(self.config.get("llm.temperature", 0.1)))

        parsed = _extract_json(resp.text)
        advice = Advice(task, resp.provider, resp.model, resp.text, parsed,
                        resp.meta, ptok, mode=mode)

        (task_dir / "advice.md").write_text(_render_advice(advice),
                                            encoding="utf-8")
        (task_dir / "advice.json").write_text(
            json.dumps(advice.to_dict(), indent=2) + "\n", encoding="utf-8")
        log.info("advice generated", extra={"provider": resp.provider,
                                            "model": resp.model, "mode": mode,
                                            "prompt_tokens_est": ptok,
                                            "parsed": parsed is not None})

        # Phase 16 - patch generation (never auto-applied)
        if patch:
            advice.patch = generate_patch(self.config, self.provider, task_dir,
                                          task, advice)
            (task_dir / "advice.json").write_text(
                json.dumps(advice.to_dict(), indent=2) + "\n", encoding="utf-8")
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
    lines = ["# Advice", "", f"> Task: {a.task}", f"> Mode: `{a.mode}`",
             f"> Model: `{a.model}` via `{a.provider}`  "
             f"(prompt ~{a.prompt_tokens_est} tokens)", ""]
    p = a.parsed
    if not p:
        lines += ["_Model did not return parseable JSON; raw output below._", "",
                  "```", a.raw.strip(), "```", ""]
        return "\n".join(lines) + "\n"

    summary = p.get("summary") or p.get("root_cause") or ""
    conf = p.get("confidence") or p.get("risk_level") or "?"
    lines += [f"**{summary}**", f"_confidence / risk: {conf}_", ""]

    # Schema-tolerant: render every remaining key generically.
    for key, value in p.items():
        if key in ("summary", "confidence", "risk_level", "root_cause"):
            continue
        title = key.replace("_", " ").capitalize()
        if isinstance(value, list) and value:
            lines += [f"## {title}", ""]
            for item in value:
                lines.append("- " + _fmt_item(item))
            lines.append("")
        elif isinstance(value, dict) and value:
            lines += [f"## {title}", ""]
            for k, v in value.items():
                lines.append(f"- **{k}**: {_fmt_item(v)}")
            lines.append("")
        elif value:
            lines += [f"## {title}", "", str(value), ""]

    if a.patch:
        lines += ["## Patch", "",
                  f"- File: `{a.patch.get('path')}`",
                  f"- `git apply --check`: {a.patch.get('apply_check', 'n/a')}",
                  f"- Files touched: {', '.join(a.patch.get('files', [])) or '-'}",
                  "", "_Not applied. Review, then `git apply` yourself._", ""]

    lines += ["## Raw model output", "", "```", a.raw.strip(), "```", ""]
    return "\n".join(lines) + "\n"


def _fmt_item(item: Any) -> str:
    if isinstance(item, dict):
        if "file" in item:
            loc = f" ({item['lines']})" if item.get("lines") else ""
            extra = item.get("reason") or item.get("statement") or ""
            return f"`{item['file']}`{loc}" + (f" - {extra}" if extra else "")
        # explain_code's shapes - formatted for readability rather than the
        # generic "k: v; k: v" fallback below.
        if "lines" in item and "explanation" in item:
            return f"**lines {item['lines']}**: {item['explanation']}"
        if "name" in item and "role" in item:
            typ = f" ({item['type']})" if item.get("type") else ""
            return f"`{item['name']}`{typ} - {item['role']}"
        if "target" in item and "why" in item:
            return f"`{item['target']}` - {item['why']}"
        if "kind" in item and "name" in item and "how" in item:
            return f"{item['how']} `{item['name']}` ({item['kind']})"
        return "; ".join(f"{k}: {v}" for k, v in item.items())
    if isinstance(item, list):
        return " -> ".join(str(x) for x in item)
    return str(item)
