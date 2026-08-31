"""Command-line interface for Local Code Memory.

``main`` is imported lazily (see ``code_memory.cli.get_main``) so that
``python -m code_memory.cli.main`` does not trigger the "found in sys.modules"
RuntimeWarning from importing the submodule via the package during startup.
"""


def get_main():
    from code_memory.cli.main import main

    return main
