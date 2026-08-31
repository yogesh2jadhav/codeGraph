"""Map a git diff onto the code graph and compute change impact.

For every changed line range in a Java file we find the graph nodes
(methods / types) whose source span overlaps, then use the graph repository to
gather callers, transitive callers, tests and related SQL/tables. Result is
written to ``.code-memory/change_impact.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_memory.config import Config
from code_memory.git.diff import DiffResult, read_diff
from code_memory.graph.repository import get_graph_repository
from code_memory.logging_setup import get_logger

log = get_logger("git.impact")


@dataclass
class ChangeImpact:
    ref: str
    diff: DiffResult
    changed_symbols: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    impacted_callers: set[str] = field(default_factory=set)
    impacted_tests: set[str] = field(default_factory=set)
    impacted_sql: set[str] = field(default_factory=set)
    impacted_endpoints: set[str] = field(default_factory=set)
    unmapped_files: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "changed_files": len(self.diff.files),
            "changed_symbols": len(self.changed_symbols),
            "impacted_callers": len(self.impacted_callers),
            "impacted_tests": len(self.impacted_tests),
            "impacted_sql": len(self.impacted_sql),
            "impacted_endpoints": len(self.impacted_endpoints),
        }


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def change_impact(config: Config, ref: str = "HEAD") -> ChangeImpact:
    diff = read_diff(config.project_root, ref, pathspec="*.java")
    ci = ChangeImpact(ref=ref, diff=diff)
    if not diff.available:
        return ci

    repo = get_graph_repository(config)
    # index method/type nodes by file
    nodes_by_file: dict[str, list[dict]] = {}
    for kind in ("Method", "Constructor", "Class", "Interface", "Enum", "Record"):
        for n in repo.find_nodes(kind=kind):
            loc = n.get("location") or {}
            rel = loc.get("relative_path")
            if rel:
                nodes_by_file.setdefault(rel, []).append(n)

    for fd in diff.files:
        if fd.status == "A":
            ci.new_files.append(fd.path)
            continue
        if fd.status == "D":
            ci.deleted_files.append(fd.path)
        candidates = nodes_by_file.get(fd.path, [])
        if not candidates and fd.status not in ("D",):
            ci.unmapped_files.append(fd.path)
        for n in candidates:
            loc = n["location"]
            span = (loc.get("line_start", 0), loc.get("line_end", 0))
            if any(_overlaps(span, r) for r in fd.changed_ranges) or not fd.changed_ranges:
                ci.changed_symbols.append(n["id"])
                imp = repo.find_impact(n["id"])
                ci.impacted_callers.update(imp.get("transitive_callers", []))
                ci.impacted_tests.update(imp.get("tests", []))
                for etype, ids in (imp.get("related") or {}).items():
                    for nid in ids:
                        if nid.startswith("sql:"):
                            ci.impacted_sql.add(nid)
                        elif nid.startswith("endpoint:"):
                            ci.impacted_endpoints.add(nid)
                # endpoints that map to this method
                for nb in repo.neighbors(n["id"], edge_types=("MAPPED_TO",),
                                         direction="in"):
                    ci.impacted_endpoints.add(nb["id"])

    ci.changed_symbols = sorted(set(ci.changed_symbols))
    _write_report(config, ci)
    log.info("change impact computed", extra=ci.summary())
    return ci


def _short(nid: str) -> str:
    return nid.split(":", 1)[-1]


def _write_report(config: Config, ci: ChangeImpact) -> None:
    d = ci.diff
    lines = ["# Change impact", "",
             f"> Diff `{ci.ref}` (base `{d.base_resolved or '?'}`) vs working tree",
             ""]
    if not d.available:
        lines.append(f"_git diff unavailable: {d.error}_")
        (config.output_dir / "change_impact.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        return

    lines += ["## Changed files", "", "| File | Status | +/- | Ranges |",
              "| --- | --- | --- | --- |"]
    for fd in d.files:
        rng = ", ".join(f"{a}-{b}" if a != b else str(a)
                        for a, b in fd.changed_ranges) or "-"
        lines.append(f"| `{fd.path}` | {fd.status} | +{fd.added}/-{fd.removed} "
                     f"| {rng} |")
    lines.append("")

    lines += ["## Changed symbols", ""]
    lines += [f"- `{_short(s)}`" for s in ci.changed_symbols] or ["_none mapped_"]
    if ci.unmapped_files:
        lines += ["", "_Files with changes but no graph mapping "
                  "(new/renamed, non-Java, or unparsed):_"]
        lines += [f"- `{f}`" for f in ci.unmapped_files]
    lines.append("")

    for title, items in (("Impacted callers (transitive)", ci.impacted_callers),
                         ("Impacted tests", ci.impacted_tests),
                         ("Impacted endpoints", ci.impacted_endpoints),
                         ("Impacted SQL statements", ci.impacted_sql)):
        lines += [f"## {title} ({len(items)})", ""]
        lines += [f"- `{_short(i)}`" for i in sorted(items)[:60]] or ["_none_"]
        lines.append("")

    (config.output_dir / "change_impact.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
