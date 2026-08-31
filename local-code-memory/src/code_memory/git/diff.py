"""Read a git diff and reduce it to changed line ranges per file.

``git`` is invoked as a read-only subprocess (`git diff --unified=0`). Hunk
headers ``@@ -a,b +c,d @@`` give the *new-side* line ranges we map onto graph
node spans.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileDiff:
    path: str
    status: str                       # "M" | "A" | "D" | "R" | "?"
    changed_ranges: list[tuple[int, int]] = field(default_factory=list)
    added: int = 0
    removed: int = 0


@dataclass
class DiffResult:
    ref: str
    base_resolved: str | None
    files: list[FileDiff] = field(default_factory=list)
    available: bool = True
    error: str | None = None

    def java_files(self) -> list[FileDiff]:
        return [f for f in self.files if f.path.endswith(".java")]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, timeout=30)


def read_diff(root: Path, ref: str = "HEAD",
              pathspec: str | None = None) -> DiffResult:
    if not (root / ".git").exists():
        return DiffResult(ref, None, available=False, error="not a git repo")

    # ``A..B`` compares two refs; a single ref compares it to the working tree.
    diff_args = [ref] if ".." in ref else [ref]
    resolved = None
    try:
        rp = _git(root, "rev-parse", "--short",
                  ref.split("..")[0] if ".." in ref else ref)
        resolved = rp.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass

    args = ["diff", "--unified=0", "--no-color", "--find-renames", *diff_args]
    if pathspec:
        args += ["--", pathspec]
    try:
        proc = _git(root, *args)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DiffResult(ref, resolved, available=False, error=str(exc))
    if proc.returncode != 0:
        return DiffResult(ref, resolved, available=False,
                          error=proc.stderr.strip() or "git diff failed")

    return DiffResult(ref, resolved, files=_parse(proc.stdout))


def _parse(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            if cur:
                files.append(cur)
            cur = FileDiff(path="?", status="M")
        elif cur is not None and line.startswith("new file mode"):
            cur.status = "A"
        elif cur is not None and line.startswith("deleted file mode"):
            cur.status = "D"
        elif cur is not None and line.startswith("rename to "):
            cur.status = "R"
            cur.path = line[len("rename to "):].strip()
        elif cur is not None and line.startswith("+++ b/"):
            cur.path = line[len("+++ b/"):].strip()
        elif cur is not None and line.startswith("+++ /dev/null"):
            cur.status = "D"
        elif cur is not None and line.startswith("--- a/") and cur.path == "?":
            cur.path = line[len("--- a/"):].strip()
        elif cur is not None:
            m = _HUNK_RE.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2) or "1")
                if count == 0:                     # pure deletion at this point
                    cur.changed_ranges.append((start, start))
                else:
                    cur.changed_ranges.append((start, start + count - 1))
            elif line.startswith("+") and not line.startswith("+++"):
                cur.added += 1
            elif line.startswith("-") and not line.startswith("---"):
                cur.removed += 1
    if cur:
        files.append(cur)
    return [f for f in files if f.path != "?"]
