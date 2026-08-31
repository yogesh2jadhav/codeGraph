"""Enable ``python -m code_memory`` as the CLI entry point."""

from code_memory.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
