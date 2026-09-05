"""Local REST API + web UI (PLAN.md section 36, section 52.F).

Optional - needs ``pip install -e "local-code-memory[api]"``. Binds to
127.0.0.1 only, never exposed publicly by default. ``code-memory serve``
launches it.
"""

from code_memory.api.app import create_app

__all__ = ["create_app"]
