"""Read git commit / branch without shelling out to git.

Parses .git/HEAD and the referenced ref file (or packed-refs). Returns
``(commit, branch)`` with either element possibly ``None`` (detached HEAD,
no repo, fresh repo with no commits).
"""

from __future__ import annotations

from pathlib import Path


def read_git_info(root: Path) -> tuple[str | None, str | None]:
    git_dir = root / ".git"
    # Support worktrees / submodules where .git is a file pointing elsewhere.
    if git_dir.is_file():
        try:
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                git_dir = (root / pointer.split(":", 1)[1].strip()).resolve()
        except OSError:
            return None, None
    if not git_dir.is_dir():
        return None, None

    head = git_dir / "HEAD"
    if not head.is_file():
        return None, None
    try:
        content = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None, None

    if content.startswith("ref:"):
        ref = content.split(":", 1)[1].strip()
        branch = ref.rsplit("/", 1)[-1]
        commit = _resolve_ref(git_dir, ref)
        return commit, branch

    # Detached HEAD - content is the commit sha itself.
    return content or None, None


def _resolve_ref(git_dir: Path, ref: str) -> str | None:
    loose = git_dir / ref
    if loose.is_file():
        try:
            return loose.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    packed = git_dir / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith(("#", "^")):
                    sha, _, name = line.partition(" ")
                    if name.strip() == ref:
                        return sha.strip()
        except OSError:
            return None
    return None
